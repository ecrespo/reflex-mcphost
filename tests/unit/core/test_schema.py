"""Bounded JSON Schema validation (F3-07, DD-010) and the elicitation subset check."""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from reflex_mcphost.core.schema import (
    MAX_ERRORS,
    MAX_INSTANCE_DEPTH,
    MAX_MESSAGE_LENGTH,
    MAX_PATTERN_LENGTH,
    MAX_SCHEMA_DEPTH,
    MAX_SCHEMA_NODES,
    CompiledSchema,
    SchemaCache,
    SchemaCompileError,
    compile_schema,
    elicitation_schema_problems,
    schema_hash,
)
from reflex_mcphost.core.secrets import default_registry

BOUND_SECONDS = 2.0
OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"a": {"type": "array", "items": {"type": "integer"}}, "b": {"type": "string"}},
    "required": ["a"],
}


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def refuse(*args: object, **_kw: object) -> None:
        calls.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    return calls


# ---------------------------------------------------------------- validation


def test_valid_and_invalid_instances() -> None:
    """DD-010: Draft 2020-12 validation reports instance path and keyword."""
    compiled = compile_schema(OBJECT_SCHEMA)
    assert isinstance(compiled, CompiledSchema)
    assert compiled.validate({"a": [1, 2], "b": "x"}).ok
    result = compiled.validate({"a": [1, "two"], "b": 3})
    assert not result.ok
    assert not result.truncated
    by_path = {i.path: i.keyword for i in result.issues}
    assert by_path == {"/a/1": "type", "/b": "type"}
    missing = compiled.validate({})
    assert [(i.path, i.keyword) for i in missing.issues] == [("", "required")]


def test_draft_2020_12_is_the_default_dialect() -> None:
    """DD-010: prefixItems is a 2020-12 keyword; it is honored without $schema."""
    compiled = compile_schema({"type": "array", "prefixItems": [{"type": "integer"}]})
    assert not compiled.validate(["x"]).ok
    assert compiled.validate([1, "x"]).ok


def test_declared_dialect_is_honored() -> None:
    """DD-010: a known $schema selects its draft; unknown dialects fall back to 2020-12."""
    draft7 = compile_schema(
        {"$schema": "http://json-schema.org/draft-07/schema#", "items": [{"type": "integer"}]}
    )
    assert not draft7.validate(["x"]).ok
    with pytest.raises(SchemaCompileError):  # array-form items is invalid in 2020-12
        compile_schema({"items": [{"type": "integer"}]})
    unknown = compile_schema({"$schema": "https://example.com/dialect", "type": "integer"})
    assert not unknown.validate("x").ok


def test_boolean_schemas() -> None:
    """DD-010: true/false are valid schemas."""
    assert compile_schema(True).validate({"x": 1}).ok
    assert not compile_schema(False).validate({}).ok


def test_in_document_refs_resolve() -> None:
    """DD-010: $ref limited to in-document references."""
    schema = {
        "$defs": {"pos": {"type": "integer", "minimum": 0}},
        "type": "object",
        "properties": {"n": {"$ref": "#/$defs/pos"}},
    }
    compiled = compile_schema(schema)
    assert compiled.validate({"n": 3}).ok
    assert [i.keyword for i in compiled.validate({"n": -1}).issues] == ["minimum"]


def test_remote_ref_is_not_fetched(no_network: list[object]) -> None:
    """DD-010: a remote $ref is never retrieved; it is a contained schema error."""
    for ref in ("https://example.com/s.json", "http://127.0.0.1:9/x", "file:///etc/passwd"):
        compiled = compile_schema({"type": "object", "properties": {"x": {"$ref": ref}}})
        with pytest.raises(SchemaCompileError, match="unresolvable"):
            compiled.validate({"x": 1})
        assert compiled.validate({}).ok  # the ref is not touched when not reached
    with pytest.raises(SchemaCompileError, match="unresolvable"):
        compile_schema({"$ref": "#/$defs/missing"}).validate(1)
    assert no_network == []


def test_invalid_schema_is_a_compile_error() -> None:
    """DD-010: compile errors are contained (raised as SchemaCompileError, per tool)."""
    for bad in ({"type": 5}, {"pattern": "("}, {"minLength": -1}, [1, 2], "string", None):
        with pytest.raises(SchemaCompileError) as exc_info:
            compile_schema(bad)
        assert len(str(exc_info.value)) <= MAX_MESSAGE_LENGTH + 64
    with pytest.raises(SchemaCompileError):
        compile_schema({"enum": [float("nan")]})  # not canonical JSON


def test_compile_error_detail_is_redacted() -> None:
    """DD-010 / Art. 5: compile detail never echoes a registered secret."""
    secret = "schema-secret-0123456789"  # noqa: S105 - test sentinel
    default_registry.register(secret)
    try:
        with pytest.raises(SchemaCompileError) as exc_info:
            compile_schema({"type": secret})
        assert secret not in str(exc_info.value)
        assert secret not in exc_info.value.detail
    finally:
        default_registry.forget(secret)


# ---------------------------------------------------------------- bounds


def test_error_list_is_capped_and_messages_truncated() -> None:
    """DD-010: bounded error output — at most MAX_ERRORS issues, truncated messages."""
    schema = {"type": "object", "properties": {f"p{i}": {"type": "integer"} for i in range(100)}}
    result = compile_schema(schema).validate({f"p{i}": "x" * 5_000 for i in range(100)})
    assert len(result.issues) == MAX_ERRORS
    assert result.truncated
    assert all(len(i.message) <= MAX_MESSAGE_LENGTH for i in result.issues)


def test_messages_redact_registered_secrets() -> None:
    """DD-010 / Art. 5: validation messages never echo a registered secret verbatim."""
    secret = "tok-secret-0123456789"  # noqa: S105 - test sentinel
    default_registry.register(secret)
    try:
        compiled = compile_schema({"properties": {"token": {"type": "integer"}}})
        (issue,) = compiled.validate({"token": secret}).issues
        assert secret not in issue.message
        assert issue.path == "/token"
    finally:
        default_registry.forget(secret)


@pytest.mark.parametrize(
    ("schema", "instance"),
    [
        ({"properties": {"text": {"type": "string"}}}, {"text": {"token": "arg-secret-v-0001"}}),
        ({"properties": {"pin": {"maxLength": 3}}}, {"pin": "arg-secret-v-0002"}),
        ({"properties": {"pin": {"enum": ["a", "b"]}}}, {"pin": "arg-secret-v-0003"}),
        ({"properties": {"pin": {"pattern": "^[0-9]+$"}}}, {"pin": "arg-secret-v-0004"}),
        ({"anyOf": [{"type": "integer"}, {"type": "boolean"}]}, {"k": "arg-secret-v-0005"}),
    ],
)
def test_messages_never_echo_the_instance(schema: dict[str, Any], instance: Any) -> None:
    """REQ-NFR-010, Art. 5: a validation message never echoes the (possibly secret-like,
    unregistered) argument value — only the keyword's expectation and the path."""
    (issue,) = compile_schema(schema).validate(instance).issues
    leaf = next(v for v in _leaves(instance))
    assert leaf not in issue.message
    assert issue.message


def _leaves(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, str):
        yield value


def test_deep_schema_fails_within_bounded_time() -> None:
    """DD-010: a pathological (very deep) schema fails fast instead of recursing."""
    schema: dict[str, Any] = {"type": "integer"}
    for _ in range(20_000):
        schema = {"properties": {"a": schema}}
    start = time.monotonic()
    with pytest.raises(SchemaCompileError, match="depth"):
        compile_schema(schema)
    assert time.monotonic() - start < BOUND_SECONDS
    ok: dict[str, Any] = {"type": "integer"}
    for _ in range(MAX_SCHEMA_DEPTH // 2 - 2):
        ok = {"properties": {"a": ok}}
    compile_schema(ok)


def test_huge_schema_and_pattern_are_refused() -> None:
    """DD-010: node count and pattern length are bounded."""
    wide = {"properties": {f"p{i}": {"type": "integer"} for i in range(MAX_SCHEMA_NODES)}}
    with pytest.raises(SchemaCompileError, match="nodes"):
        compile_schema(wide)
    with pytest.raises(SchemaCompileError, match="pattern"):
        compile_schema({"pattern": "a" * (MAX_PATTERN_LENGTH + 1)})
    with pytest.raises(SchemaCompileError, match="pattern"):
        compile_schema({"patternProperties": {"b" * (MAX_PATTERN_LENGTH + 1): {}}})
    with pytest.raises(SchemaCompileError, match="pattern"):
        compile_schema({"patternProperties": {"(": {}}})


def test_self_referencing_schema_is_bounded() -> None:
    """DD-010: a $ref loop that never consumes the instance fails within bounded time."""
    compiled = compile_schema({"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"})
    start = time.monotonic()
    with pytest.raises(SchemaCompileError, match="recursion"):
        compiled.validate(1)
    assert time.monotonic() - start < BOUND_SECONDS


def test_deep_instance_is_bounded() -> None:
    """DD-010: a too-deep instance is reported as an issue, not a crash."""
    compiled = compile_schema(
        {"$defs": {"n": {"items": {"$ref": "#/$defs/n"}}}, "$ref": "#/$defs/n"}
    )
    instance: list[Any] = []
    for _ in range(50_000):
        instance = [instance]
    start = time.monotonic()
    result = compiled.validate(instance)
    assert time.monotonic() - start < BOUND_SECONDS
    assert not result.ok
    assert [(i.path, i.keyword) for i in result.issues] == [("", "depth")]
    shallow: list[Any] = []
    for _ in range(MAX_INSTANCE_DEPTH - 1):
        shallow = [shallow]
    assert compiled.validate(shallow).ok


# ---------------------------------------------------------------- cache


def test_validators_are_cached_by_definition_hash() -> None:
    """DD-010: validators are cached by definition hash; key order does not matter."""
    cache = SchemaCache(maxsize=2)
    a = cache.get({"type": "integer", "minimum": 1})
    assert cache.get({"minimum": 1, "type": "integer"}) is a
    assert a.definition_hash == schema_hash({"minimum": 1, "type": "integer"})
    assert cache.get({"type": "string"}, key="tool-x:input") is cache.get(True, key="tool-x:input")
    assert len(cache) == 2
    cache.get({"type": "boolean"})  # evicts the least recently used entry
    assert len(cache) == 2
    assert cache.get({"type": "integer", "minimum": 1}) is not a


def test_compile_errors_are_cached() -> None:
    """DD-010: a broken tool schema is compiled once; later calls re-raise the same error."""
    cache = SchemaCache()
    with pytest.raises(SchemaCompileError) as first:
        cache.get({"type": 5})
    with pytest.raises(SchemaCompileError) as second:
        cache.get({"type": 5})
    assert first.value.detail == second.value.detail
    assert len(cache) == 1
    with pytest.raises(ValueError, match="maxsize"):
        SchemaCache(maxsize=0)


# ---------------------------------------------------------------- elicitation subset


VALID_ELICITATION: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Form",
    "description": "d",
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Name", "minLength": 1, "maxLength": 50},
        "email": {"type": "string", "format": "email", "default": "a@b.c"},
        "age": {"type": "integer", "minimum": 0, "maximum": 150, "default": 3},
        "score": {"type": "number", "description": "x", "default": 1.5},
        "ok": {"type": "boolean", "default": False},
        "color": {"type": "string", "enum": ["red", "green"], "default": "red"},
        "legacy": {"type": "string", "enum": ["a", "b"], "enumNames": ["A", "B"]},
        "titled": {"type": "string", "oneOf": [{"const": "a", "title": "A"}]},
        "multi": {
            "type": "array",
            "items": {"type": "string", "enum": ["x", "y"]},
            "minItems": 1,
            "maxItems": 2,
            "default": ["x"],
        },
        "multi_titled": {
            "type": "array",
            "items": {"anyOf": [{"const": "x", "title": "X"}]},
        },
    },
    "required": ["name", "age"],
}


def test_elicitation_subset_accepts_spec_primitives() -> None:
    """DD-010: elicitation subset — the spec's primitive and enum schemas pass."""
    assert elicitation_schema_problems(VALID_ELICITATION) == []
    assert elicitation_schema_problems({"type": "object", "properties": {}}) == []


@pytest.mark.parametrize(
    ("schema", "fragment"),
    [
        ("nope", "must be an object"),
        ({"properties": {}}, "type"),
        ({"type": "array", "properties": {}}, "type"),
        ({"type": "object"}, "properties"),
        ({"type": "object", "properties": []}, "properties"),
        ({"type": "object", "properties": {}, "$defs": {}}, "'$defs'"),
        ({"type": "object", "properties": {}, "required": "a"}, "required"),
        ({"type": "object", "properties": {}, "required": ["a"]}, "'a'"),
        ({"type": "object", "properties": {"a": "x"}}, "'a'"),
        ({"type": "object", "properties": {"a": {"type": "object"}}}, "type"),
        ({"type": "object", "properties": {"a": {"type": ["string", "null"]}}}, "type"),
        ({"type": "object", "properties": {"a": {"$ref": "#/x"}}}, "'$ref'"),
        ({"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}}, "pattern"),
        ({"type": "object", "properties": {"a": {"type": "string", "format": "ipv4"}}}, "format"),
        ({"type": "object", "properties": {"a": {"type": "string", "minLength": -1}}}, "minLength"),
        ({"type": "object", "properties": {"a": {"type": "string", "default": 1}}}, "default"),
        ({"type": "object", "properties": {"a": {"type": "integer", "minimum": "1"}}}, "minimum"),
        ({"type": "object", "properties": {"a": {"type": "integer", "default": 1.5}}}, "default"),
        ({"type": "object", "properties": {"a": {"type": "boolean", "default": 1}}}, "default"),
        ({"type": "object", "properties": {"a": {"type": "string", "title": 1}}}, "title"),
        ({"type": "object", "properties": {"a": {"type": "string", "enum": [1]}}}, "enum"),
        ({"type": "object", "properties": {"a": {"type": "string", "enum": []}}}, "enum"),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "string", "enum": ["x"], "default": "y"}},
            },
            "default",
        ),
        (
            {"type": "object", "properties": {"a": {"type": "string", "enumNames": ["X"]}}},
            "enumNames",
        ),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "string", "enum": ["x"], "enumNames": ["X", "Y"]}},
            },
            "enumNames",
        ),
        (
            {"type": "object", "properties": {"a": {"type": "string", "oneOf": [{"const": 1}]}}},
            "oneOf",
        ),
        ({"type": "object", "properties": {"a": {"type": "array"}}}, "items"),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "array", "items": {"type": "integer"}}},
            },
            "items",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["x"]},
                        "minItems": -1,
                    }
                },
            },
            "minItems",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["x"]},
                        "default": ["z"],
                    }
                },
            },
            "default",
        ),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "array", "items": {"anyOf": [{"title": "X"}]}}},
            },
            "anyOf",
        ),
    ],
)
def test_elicitation_subset_rejects_other_constructs(schema: Any, fragment: str) -> None:
    """DD-010: elicitation subset — constructs outside the subset are reported."""
    problems = elicitation_schema_problems(schema)
    assert problems
    assert any(fragment in p for p in problems), problems


def test_elicitation_problems_are_bounded() -> None:
    """DD-010: problem lists are capped and Server-supplied names truncated."""
    long_name = "n" * 1_000
    schema = {
        "type": "object",
        "properties": {f"{long_name}{i}": {"type": "object"} for i in range(100)},
    }
    problems = elicitation_schema_problems(schema)
    assert len(problems) == MAX_ERRORS
    assert all(len(p) <= MAX_MESSAGE_LENGTH for p in problems)
