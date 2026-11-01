"""Bounded evaluation of Server-supplied regexes (F8-10; REQ-NFR-021, DD-010).

Every keyword whose behaviour depends on a Server pattern (``pattern``,
``patternProperties``, the key matching of ``additionalProperties`` /
``unevaluatedProperties``, and ``propertyNames`` through its subschema) is matched with
``regex`` under a 50 ms per-match timeout and a 250 ms per-``validate()`` budget; a bound
that is hit is an unusable schema (``SchemaCompileError``), never valid input.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # no stubs shipped
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reflex_mcphost.core import schema as schema_mod
from reflex_mcphost.core.schema import (
    PATTERN_MATCH_TIMEOUT,
    PATTERN_VALIDATE_BUDGET,
    SchemaCache,
    SchemaCompileError,
)

EVIL = "^(a|aa)+$"
HOSTILE = "a" * 64 + "!"
WALL_LIMIT = 0.300  # budget + slack (F8-10 Done)
DRAFTS = (
    "https://json-schema.org/draft/2020-12/schema",
    "https://json-schema.org/draft/2019-09/schema",
    "http://json-schema.org/draft-07/schema#",
    "http://json-schema.org/draft-06/schema#",
    "http://json-schema.org/draft-04/schema#",
)


def _compile(schema: dict[str, Any]) -> Any:
    return SchemaCache().get(schema)


def _timed_out(schema: dict[str, Any], instance: Any) -> float:
    compiled = _compile(schema)
    start = time.perf_counter()
    with pytest.raises(SchemaCompileError, match="pattern evaluation timed out"):
        compiled.validate(instance)
    return time.perf_counter() - start


def test_bounds_are_the_specified_values() -> None:
    """REQ-NFR-021: at most 50 ms per match and 250 ms per schema validation."""
    assert (PATTERN_MATCH_TIMEOUT, PATTERN_VALIDATE_BUDGET) == (0.05, 0.25)


@pytest.mark.parametrize("draft", DRAFTS)
def test_catastrophic_pattern_is_unusable_schema(draft: str) -> None:
    """REQ-NFR-021, REQ-INV-002: ``^(a|aa)+$`` against 64 x ``a`` + ``!`` → SchemaCompileError
    within budget + slack, on every supported draft."""
    elapsed = _timed_out({"$schema": draft, "type": "string", "pattern": EVIL}, HOSTILE)
    assert elapsed <= WALL_LIMIT


@pytest.mark.parametrize("draft", DRAFTS)
def test_catastrophic_pattern_properties_key(draft: str) -> None:
    """REQ-NFR-021: a ``patternProperties`` key is matched against instance keys bounded."""
    schema = {"$schema": draft, "type": "object", "patternProperties": {EVIL: {}}}
    assert _timed_out(schema, {HOSTILE: 1}) <= WALL_LIMIT


@pytest.mark.parametrize("draft", DRAFTS)
def test_catastrophic_key_through_additional_properties(draft: str) -> None:
    """REQ-NFR-021: ``additionalProperties: false`` matches keys against ``patternProperties``
    under the same bound."""
    schema = {
        "$schema": draft,
        "type": "object",
        "properties": {"ok": {}},
        "patternProperties": {EVIL: {}},
        "additionalProperties": False,
    }
    assert _timed_out(schema, {HOSTILE: 1}) <= WALL_LIMIT


@pytest.mark.parametrize("draft", DRAFTS[:2])
def test_catastrophic_key_through_unevaluated_properties(draft: str) -> None:
    """REQ-NFR-021: ``unevaluatedProperties`` key matching (patternProperties in a subschema)
    is bounded on 2019-09 and 2020-12."""
    schema = {
        "$schema": draft,
        "type": "object",
        "allOf": [{"patternProperties": {EVIL: {}}}],
        "unevaluatedProperties": False,
    }
    assert _timed_out(schema, {HOSTILE: 1}) <= WALL_LIMIT


@pytest.mark.parametrize("draft", DRAFTS[:4])
def test_catastrophic_property_names(draft: str) -> None:
    """REQ-NFR-021: ``propertyNames`` reaches ``pattern`` through its subschema."""
    schema = {"$schema": draft, "type": "object", "propertyNames": {"pattern": EVIL}}
    assert _timed_out(schema, {HOSTILE: 1}) <= WALL_LIMIT


def test_catastrophic_pattern_through_ref_and_nested_items() -> None:
    """REQ-NFR-021, REQ-INV-007: patterns reached via in-document ``$ref`` and array items are
    bounded too (output-schema shape)."""
    schema = {
        "type": "object",
        "$defs": {"evil": {"type": "string", "pattern": EVIL}},
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/evil"}}},
    }
    assert _timed_out(schema, {"items": ["aa", HOSTILE]}) <= WALL_LIMIT


@pytest.mark.parametrize(
    ("schema", "good", "bad"),
    [
        ({"type": "string", "pattern": EVIL}, "aaaa", "aab"),
        ({"type": "string", "pattern": "^[0-9]+$"}, "123", "12a"),
        ({"type": "string", "pattern": "b"}, "abc", "xyz"),  # search, not fullmatch
        (
            {"type": "object", "patternProperties": {"^x-": {"type": "integer"}}},
            {"x-a": 1, "other": "s"},
            {"x-a": "s"},
        ),
        (
            {
                "type": "object",
                "properties": {"id": {}},
                "patternProperties": {"^x-": {}, "^y-": {}},
                "additionalProperties": False,
            },
            {"id": 1, "x-1": 1, "y-2": 2},
            {"id": 1, "z": 1},
        ),
        (
            {
                "type": "object",
                "properties": {"id": {}},
                "patternProperties": {"^x-": {}},
                "additionalProperties": {"type": "string"},
            },
            {"id": 1, "x-1": 1, "other": "s"},
            {"other": 1},
        ),
        (
            {
                "type": "object",
                "allOf": [{"patternProperties": {"^x-": {}}}, {"properties": {"id": {}}}],
                "unevaluatedProperties": False,
            },
            {"id": 1, "x-1": 1},
            {"id": 1, "z": 1},
        ),
        (
            {
                "type": "object",
                "if": {"properties": {"kind": {"const": "a"}}},
                "then": {"patternProperties": {"^a-": {}}},
                "else": {"patternProperties": {"^b-": {}}},
                "unevaluatedProperties": False,
            },
            {"kind": "a", "a-1": 1},
            {"kind": "b", "a-1": 1},
        ),
        (
            {
                "type": "object",
                "$defs": {"ext": {"patternProperties": {"^x-": {}}}},
                "$ref": "#/$defs/ext",
                "dependentSchemas": {"id": {"properties": {"id": {}, "extra": {}}}},
                "unevaluatedProperties": False,
            },
            {"x-1": 1, "id": 1, "extra": 2},
            {"x-1": 1, "extra": 2},
        ),
        (
            {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}},
            {"abc": 1},
            {"ABC": 1},
        ),
    ],
)
def test_ordinary_patterns_validate_as_before(schema: dict[str, Any], good: Any, bad: Any) -> None:
    """REQ-NFR-021, DD-010: the same patterns on matching / short inputs keep the stdlib
    ``jsonschema`` verdicts."""
    compiled = _compile(schema)
    assert compiled.validate(good).ok
    assert not compiled.validate(bad).ok
    stock = jsonschema.Draft202012Validator(schema)
    assert stock.is_valid(good)
    assert not stock.is_valid(bad)


@pytest.mark.parametrize("draft", DRAFTS[1:2])
def test_unevaluated_properties_draft2019_keys(draft: str) -> None:
    """REQ-NFR-021: 2019-09 ``unevaluatedProperties`` counts ``properties`` /
    ``patternProperties`` / ``$ref`` keys as evaluated."""
    schema = {
        "$schema": draft,
        "type": "object",
        "$defs": {"base": {"properties": {"id": {}}}},
        "$ref": "#/$defs/base",
        "anyOf": [{"patternProperties": {"^x-": {}}}],
        "dependentSchemas": {"id": {"properties": {"extra": {}}}},
        "if": {"required": ["id"]},
        "then": {"properties": {"t": {}}},
        "else": {"properties": {"e": {}}},
        "additionalProperties": True,
        "unevaluatedProperties": False,
    }
    compiled = _compile(schema)
    assert compiled.validate({"id": 1, "x-a": 1, "extra": 1, "t": 1, "zzz": 1}).ok
    strict = dict(schema, additionalProperties=False)
    strict_compiled = _compile(strict)
    assert not strict_compiled.validate({"zzz": 1}).ok
    with_rec = {
        "$schema": draft,
        "$recursiveAnchor": True,
        "type": "object",
        "properties": {"child": {"$recursiveRef": "#"}},
        "patternProperties": {"^x-": {}},
        "unevaluatedProperties": False,
    }
    rec = _compile(with_rec)
    assert rec.validate({"child": {"x-1": 1}, "x-2": 2}).ok
    assert not rec.validate({"child": {"bad": 1}}).ok


def test_unevaluated_properties_schema_and_messages() -> None:
    """REQ-NFR-021, REQ-NFR-010: an ``unevaluatedProperties`` subschema reports invalid
    unevaluated keys; ``additionalProperties: false`` with patterns names the regexes; neither
    message echoes an instance value."""
    compiled = _compile(
        {
            "type": "object",
            "patternProperties": {"^x-": {}},
            "unevaluatedProperties": {"type": "string"},
        }
    )
    assert compiled.validate({"x-1": 1, "a": "s"}).ok
    (issue,) = compiled.validate({"a": 1}).issues
    assert issue.keyword == "unevaluatedProperties"
    additional = _compile(
        {"type": "object", "patternProperties": {"^x-": {}}, "additionalProperties": False}
    )
    (issue,) = additional.validate({"a": 1, "b": 2}).issues
    assert issue.keyword == "additionalProperties"
    assert "^x-" in issue.message
    one = additional.validate({"a": 1}).issues[0]
    assert "does not match" in one.message


def test_non_object_and_non_string_instances_skip_matching() -> None:
    """REQ-NFR-021: keywords only match strings / object keys (no match on other types)."""
    compiled = _compile(
        {
            "patternProperties": {EVIL: {}},
            "additionalProperties": False,
            "unevaluatedProperties": False,
            "pattern": EVIL,
        }
    )
    for instance in (5, None, [HOSTILE], True):
        assert compiled.validate(instance).ok


def test_budget_exhaustion_is_unusable_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-NFR-021: the sum of match time is capped at 250 ms per ``validate()``; each match
    gets at most the remaining budget (capped at 50 ms)."""
    ticks = iter(i * 0.04 for i in range(10_000))
    monkeypatch.setattr(schema_mod, "_clock", lambda: next(ticks))
    compiled = _compile({"type": "array", "items": {"type": "string", "pattern": "^a+$"}})
    with pytest.raises(SchemaCompileError, match="pattern evaluation timed out"):
        compiled.validate(["a"] * 20)
    # Each match "took" 40 ms: six fit in 250 ms, the seventh finds the budget exhausted.
    assert compiled.validate(["a"] * 6).ok


def test_timeout_passed_is_remaining_budget_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-NFR-021: ``timeout=`` is min(50 ms, remaining budget)."""
    seen: list[float] = []

    class Spy:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def search(self, text: str, *, timeout: float) -> Any:
            seen.append(timeout)
            return self.inner.search(text, timeout=timeout)

    real = schema_mod._compile_pattern
    monkeypatch.setattr(schema_mod, "_compile_pattern", lambda p: Spy(real(p)))
    ticks = iter(i * 0.045 for i in range(10_000))
    monkeypatch.setattr(schema_mod, "_clock", lambda: next(ticks))
    compiled = SchemaCache().get({"type": "array", "items": {"type": "string", "pattern": "a"}})
    compiled._patterns.clear()
    with pytest.raises(SchemaCompileError):
        compiled.validate(["a"] * 10)
    assert seen[0] == pytest.approx(0.05)
    assert all(t <= 0.05 for t in seen)
    assert seen[-1] < 0.05


def test_timeout_is_not_cached_and_budget_is_per_call() -> None:
    """REQ-NFR-021: a timed-out validation does not poison the compiled schema; the next
    ``validate()`` starts with a fresh budget."""
    compiled = _compile({"type": "string", "pattern": EVIL})
    for _ in range(3):
        with pytest.raises(SchemaCompileError):
            compiled.validate(HOSTILE)
        assert compiled.validate("aaaa").ok


def test_budget_is_isolated_between_threads() -> None:
    """REQ-NFR-021: concurrent validations each get their own budget."""
    compiled = _compile({"type": "string", "pattern": EVIL})
    results: list[str] = []

    def worker() -> None:
        try:
            compiled.validate(HOSTILE)
            results.append("ok")
        except SchemaCompileError:
            results.append("timeout")
        results.append("good" if compiled.validate("aa").ok else "bad")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sorted(results) == ["good"] * 4 + ["timeout"] * 4


def test_invalid_pattern_is_still_a_compile_error() -> None:
    """DD-010: a pattern ``regex`` cannot compile is a compile error, as before."""
    for bad in ("(", "[a-", "a{2,1}"):
        with pytest.raises(SchemaCompileError, match="invalid pattern"):
            _compile({"pattern": bad})
        with pytest.raises(SchemaCompileError, match="invalid pattern"):
            _compile({"patternProperties": {bad: {}}})


def test_stdlib_re_never_touches_server_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-NFR-021: compile and validation never hand a Server pattern to stdlib ``re``."""
    marker = "^server-pattern-[0-9]+$"
    real_compile = re.compile

    def guarded(pattern: Any, flags: int = 0) -> Any:
        assert pattern != marker, "stdlib re saw a Server pattern"
        return real_compile(pattern, flags)

    def wrap(real: Any) -> Any:
        def checked(pattern: Any, *args: Any, **kwargs: Any) -> Any:
            assert pattern != marker, "stdlib re saw a Server pattern"
            return real(pattern, *args, **kwargs)

        return checked

    monkeypatch.setattr(re, "compile", guarded)
    for name in ("search", "match", "fullmatch"):
        monkeypatch.setattr(re, name, wrap(getattr(re, name)))
    compiled = _compile(
        {
            "type": "object",
            "properties": {"a": {"type": "string", "pattern": marker}},
            "patternProperties": {marker: {}},
            "additionalProperties": False,
            "propertyNames": {"pattern": "^[a-z0-9-]+$"},
        }
    )
    assert compiled.validate({"a": "server-pattern-1"}).ok
    assert not compiled.validate({"a": "x"}).ok


# ------------------------------------------------------------- property: same verdict as re

_ATOMS = st.sampled_from(["a", "b", ".", "[ab]", "\\d", "x"])


def _regexes() -> st.SearchStrategy[str]:
    def extend(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        return st.one_of(
            st.tuples(inner, inner).map("".join),
            st.tuples(inner, inner).map(lambda p: f"(?:{p[0]}|{p[1]})"),
            inner.map(lambda p: f"(?:{p})*"),
            inner.map(lambda p: f"(?:{p})+"),
            inner.map(lambda p: f"(?:{p})?"),
            inner.map(lambda p: f"({p})"),
        )

    body = st.recursive(_ATOMS, extend, max_leaves=8)
    return st.tuples(st.sampled_from(["", "^"]), body, st.sampled_from(["", "$"])).map("".join)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(pattern=_regexes(), text=st.text(alphabet="ab1x!", max_size=12))
def test_random_patterns_bounded_and_agree_with_re(pattern: str, text: str) -> None:
    """REQ-NFR-021: random patterns x random strings never take > 300 ms per ``validate()``
    and give the stdlib ``re`` verdict whenever ``re`` finishes within 50 ms."""
    compiled = _compile({"type": "string", "pattern": pattern})
    start = time.perf_counter()
    detail = ""
    try:
        ours: bool | None = compiled.validate(text).ok
    except SchemaCompileError as exc:
        detail, ours = exc.detail, None
    assert ours is not None or "timed out" in detail
    assert time.perf_counter() - start <= WALL_LIMIT
    re_start = time.perf_counter()
    expected = re.search(pattern, text) is not None
    if time.perf_counter() - re_start <= PATTERN_MATCH_TIMEOUT:
        assert ours is not None
        assert ours == expected
