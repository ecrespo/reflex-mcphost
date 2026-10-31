"""Bounded JSON Schema validation (DD-010) and the elicitation subset check (REQ-HSH-012).

Server-supplied schemas are untrusted (Constitution Art. 5):

* Validators are ``jsonschema`` Draft 2020-12 by default (a known ``$schema`` dialect is
  honored, as the MCP spec allows), cached by definition hash in a bounded LRU.
* ``$ref`` resolution is limited to the document itself: the ``referencing`` registry
  refuses every retrieval, so a remote (or ``file:``) reference is never fetched and
  surfaces as a ``SchemaCompileError``.
* Pathological schemas are refused before compiling (nesting depth, node count, pattern
  length); ``$ref`` loops that never consume the instance are caught (``RecursionError``);
  instances nested deeper than ``MAX_INSTANCE_DEPTH`` are reported as an issue.
* Error output is bounded: at most ``MAX_ERRORS`` issues with messages truncated to
  ``MAX_MESSAGE_LENGTH`` and passed through ``core.secrets.redact``.

A compile error is contained per tool: the caller keeps the tool listed and turns the
error into an invocation outcome (``protocol_error`` / ``invalid_arguments``).
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # no stubs shipped
import jsonschema.exceptions  # type: ignore[import-untyped]
import referencing
import referencing.exceptions
import referencing.jsonschema
from pydantic import BaseModel

from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.hmac_key import canonical_json
from reflex_mcphost.core.secrets import redact

MAX_ERRORS = 20
MAX_MESSAGE_LENGTH = 200
MAX_SCHEMA_DEPTH = 64
MAX_SCHEMA_NODES = 10_000
MAX_PATTERN_LENGTH = 512
MAX_INSTANCE_DEPTH = 64
CACHE_SIZE = 1_024
_NAME_LENGTH = 64
_ELLIPSIS = "…"

_DIALECTS: dict[str, Any] = {
    "json-schema.org/draft-04/schema": jsonschema.Draft4Validator,
    "json-schema.org/draft-06/schema": jsonschema.Draft6Validator,
    "json-schema.org/draft-07/schema": jsonschema.Draft7Validator,
    "json-schema.org/draft/2019-09/schema": jsonschema.Draft201909Validator,
    "json-schema.org/draft/2020-12/schema": jsonschema.Draft202012Validator,
}


class SchemaCompileError(MCPHostError):
    """A schema that cannot be used for validation; ``detail`` is bounded and redacted."""

    def __init__(self, detail: str) -> None:
        self.detail = _bounded(detail)
        super().__init__(f"invalid JSON schema: {self.detail}")


class SchemaIssue(BaseModel, frozen=True):
    """One validation failure: JSON pointer into the instance, failing keyword, message."""

    path: str
    keyword: str
    message: str


class SchemaValidation(BaseModel, frozen=True):
    issues: tuple[SchemaIssue, ...] = ()
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.issues


def _bounded(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS


def _short(name: object) -> str:
    return repr(_bounded(str(name), _NAME_LENGTH))


def _pointer(parts: Iterable[object]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


# Default retrieval of an empty ``referencing.Registry`` raises ``NoSuchResource`` for every
# URI not already in it: nothing is ever fetched (http, https, file). jsonschema combines it
# with its bundled meta-schemas only, so ``$schema`` lookups stay local too.
_REGISTRY: referencing.jsonschema.SchemaRegistry = referencing.Registry()


def _depth_exceeds(obj: Any, limit: int) -> bool:
    """Iterative nesting check (no recursion on untrusted input)."""
    stack: list[tuple[Any, int]] = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children: Iterable[Any] = node.values()
        elif isinstance(node, list | tuple):
            children = node
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _check_shape(schema: Any) -> None:
    """Refuse pathological schemas before handing them to ``jsonschema``."""
    if not isinstance(schema, dict | bool):
        raise SchemaCompileError(
            f"schema must be an object or a boolean, got {type(schema).__name__}"
        )
    nodes = 0
    stack: list[tuple[Any, int]] = [(schema, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SCHEMA_NODES:
            raise SchemaCompileError(f"schema exceeds {MAX_SCHEMA_NODES} nodes")
        if not isinstance(node, dict | list):
            continue
        if depth > MAX_SCHEMA_DEPTH:
            raise SchemaCompileError(f"schema nesting exceeds depth {MAX_SCHEMA_DEPTH}")
        if isinstance(node, dict):
            _check_patterns(node)
            node = list(node.values())
        stack.extend((child, depth + 1) for child in node)


def _check_patterns(node: dict[str, Any]) -> None:
    patterns: list[Any] = []
    if isinstance(node.get("pattern"), str):
        patterns.append(node["pattern"])
    if isinstance(node.get("patternProperties"), dict):
        patterns.extend(node["patternProperties"])
    for pattern in patterns:
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise SchemaCompileError(f"pattern longer than {MAX_PATTERN_LENGTH} characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SchemaCompileError(f"invalid pattern: {exc}") from None


def schema_hash(schema: Any) -> str:
    """Definition hash of a schema (key-order independent); a cache key, not a secret."""
    try:
        text = canonical_json(schema)
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaCompileError(f"schema is not canonical JSON ({type(exc).__name__})") from None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validator_class(schema: Any) -> Any:
    if isinstance(schema, dict) and isinstance(schema.get("$schema"), str):
        uri = schema["$schema"].split("://", 1)[-1].rstrip("#")
        return _DIALECTS.get(uri, jsonschema.Draft202012Validator)
    return jsonschema.Draft202012Validator


class CompiledSchema:
    """A checked schema and its validator. ``validate`` never raises for bad instances."""

    __slots__ = ("_validator", "definition_hash")

    def __init__(self, schema: Any, definition_hash: str) -> None:
        cls = _validator_class(schema)
        try:
            cls.check_schema(schema)
        except jsonschema.exceptions.SchemaError as exc:
            raise SchemaCompileError(
                f"{exc.message} (at {_pointer(exc.absolute_path) or '/'})"
            ) from None
        except RecursionError:
            raise SchemaCompileError("schema recursion limit exceeded") from None
        self.definition_hash = definition_hash
        self._validator = cls(schema, registry=_REGISTRY)

    def validate(self, instance: Any) -> SchemaValidation:
        """Bounded issue list; ``SchemaCompileError`` when the schema itself is unusable
        (unresolvable/remote ``$ref``, ``$ref`` loop)."""
        if _depth_exceeds(instance, MAX_INSTANCE_DEPTH):
            issue = SchemaIssue(
                path="", keyword="depth", message=f"nesting deeper than {MAX_INSTANCE_DEPTH}"
            )
            return SchemaValidation(issues=(issue,))
        issues: list[SchemaIssue] = []
        truncated = False
        try:
            for error in self._validator.iter_errors(instance):
                if len(issues) == MAX_ERRORS:
                    truncated = True
                    break
                issues.append(
                    SchemaIssue(
                        path=_pointer(error.absolute_path),
                        keyword=_bounded(str(error.validator), _NAME_LENGTH),
                        message=_bounded(error.message),
                    )
                )
        except referencing.exceptions.Unresolvable as exc:
            raise SchemaCompileError(
                f"unresolvable $ref {_short(getattr(exc, 'ref', ''))} (remote refs are not fetched)"
            ) from None
        except RecursionError:
            raise SchemaCompileError("schema recursion limit exceeded ($ref loop)") from None
        return SchemaValidation(issues=tuple(issues), truncated=truncated)


class SchemaCache:
    """Bounded LRU of compiled schemas (and compile failures) keyed by definition hash."""

    def __init__(self, maxsize: int = CACHE_SIZE) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._entries: OrderedDict[str, CompiledSchema | SchemaCompileError] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, schema: Any, *, key: str | None = None) -> CompiledSchema:
        """Compiled validator for ``schema``; ``key`` (e.g. a tool's definition hash plus
        role) skips re-hashing. Raises ``SchemaCompileError``, also from the cache."""
        if key is None:
            _check_shape(schema)
            key = schema_hash(schema)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
        if entry is None:
            entry = _compile(schema)
            with self._lock:
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)
        if isinstance(entry, SchemaCompileError):
            raise SchemaCompileError(entry.detail)
        return entry


def _compile(schema: Any) -> CompiledSchema | SchemaCompileError:
    try:
        _check_shape(schema)
        return CompiledSchema(schema, schema_hash(schema))
    except SchemaCompileError as exc:
        return exc


_default_cache = SchemaCache()


def compile_schema(schema: Any, *, key: str | None = None) -> CompiledSchema:
    """Compile through the process-wide cache (DM §8: LRU 1,024 keyed by definition hash)."""
    return _default_cache.get(schema, key=key)


# ---------------------------------------------------------------------- elicitation subset

_TOP_KEYS = frozenset({"$schema", "type", "properties", "required", "title", "description"})
_ANNOTATIONS = frozenset({"type", "title", "description", "default"})
_STRING_KEYS = _ANNOTATIONS | {"minLength", "maxLength", "format"}
_NUMBER_KEYS = _ANNOTATIONS | {"minimum", "maximum"}
_ENUM_KEYS = _ANNOTATIONS | {"enum", "enumNames"}
_TITLED_KEYS = _ANNOTATIONS | {"oneOf"}
_MULTI_KEYS = _ANNOTATIONS | {"items", "minItems", "maxItems"}
_FORMATS = frozenset({"email", "uri", "date", "date-time"})


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _options(items: Any, label: str, where: str, problems: list[str]) -> list[str] | None:
    """``[{const, title}, ...]`` option lists (titled single/multi select)."""
    if not isinstance(items, list) or not items:
        problems.append(f"{where}: {label} must be a non-empty list of {{const, title}}")
        return None
    values: list[str] = []
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) - {"const", "title"}
            or not isinstance(item.get("const"), str)
            or not isinstance(item.get("title"), str)
        ):
            problems.append(f"{where}: {label} entries must be {{const: string, title: string}}")
            return None
        values.append(item["const"])
    return values


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


def _check_property(name: str, prop: Any, problems: list[str]) -> None:
    where = f"property {_short(name)}"
    if not isinstance(prop, dict):
        problems.append(f"{where}: must be an object")
        return
    for key in ("title", "description"):
        if key in prop and not isinstance(prop[key], str):
            problems.append(f"{where}: {key} must be a string")
    kind = prop.get("type")
    if kind == "string":
        if "oneOf" in prop:
            allowed, values = _TITLED_KEYS, _options(prop["oneOf"], "oneOf", where, problems)
        elif "enum" in prop or "enumNames" in prop:
            allowed, values = _ENUM_KEYS, prop.get("enum")
            if not _string_list(values):
                problems.append(f"{where}: enum must be a non-empty list of strings")
                values = None
            names = prop.get("enumNames")
            if names is not None and (
                not isinstance(values, list) or not _string_list(names) or len(names) != len(values)
            ):
                problems.append(f"{where}: enumNames must be strings matching enum")
        else:
            allowed, values = _STRING_KEYS, None
            fmt = prop.get("format")
            if fmt is not None and fmt not in _FORMATS:
                problems.append(f"{where}: format must be one of {sorted(_FORMATS)}")
            for key in ("minLength", "maxLength"):
                if key in prop and not _is_count(prop[key]):
                    problems.append(f"{where}: {key} must be a non-negative integer")
        if "default" in prop and (
            not isinstance(prop["default"], str)
            or (isinstance(values, list) and prop["default"] not in values)
        ):
            problems.append(f"{where}: default must be one of the allowed strings")
    elif kind in ("number", "integer"):
        allowed = _NUMBER_KEYS
        for key in ("minimum", "maximum"):
            if key in prop and not _is_number(prop[key]):
                problems.append(f"{where}: {key} must be a number")
        default: Any = prop.get("default")
        if "default" in prop and (
            not _is_number(default) or (kind == "integer" and not float(default).is_integer())
        ):
            problems.append(f"{where}: default must be a {kind}")
    elif kind == "boolean":
        allowed = _ANNOTATIONS
        if "default" in prop and not isinstance(prop["default"], bool):
            problems.append(f"{where}: default must be a boolean")
    elif kind == "array":
        allowed = _MULTI_KEYS
        _check_multi_select(where, prop, problems)
    else:
        problems.append(f"{where}: type must be string, number, integer, boolean or array")
        allowed = _ANNOTATIONS
    for key in sorted(set(prop) - allowed, key=str):
        problems.append(f"{where}: keyword {_short(key)} is not allowed")


def _check_multi_select(where: str, prop: dict[str, Any], problems: list[str]) -> None:
    items = prop.get("items")
    values: list[str] | None = None
    if isinstance(items, dict) and set(items) == {"anyOf"}:
        values = _options(items["anyOf"], "items.anyOf", where, problems)
    elif (
        isinstance(items, dict)
        and set(items) == {"type", "enum"}
        and items["type"] == "string"
        and _string_list(items["enum"])
    ):
        values = items["enum"]
    else:
        problems.append(f"{where}: items must be {{type: string, enum}} or {{anyOf: [...]}}")
    for key in ("minItems", "maxItems"):
        if key in prop and not _is_count(prop[key]):
            problems.append(f"{where}: {key} must be a non-negative integer")
    if "default" in prop:
        default = prop["default"]
        if not isinstance(default, list) or (
            values is not None and any(v not in values for v in default)
        ):
            problems.append(f"{where}: default must be a list of the allowed strings")


def elicitation_schema_problems(schema: Any) -> list[str]:
    """Problems that put ``schema`` outside the MCP elicitation ``requestedSchema`` subset:
    a flat object of primitive (string/number/integer/boolean) and enum (single or
    multi-select) properties. Empty list = allowed. Bounded to ``MAX_ERRORS`` entries."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return ["requested schema must be an object"]
    if schema.get("type") != "object":
        problems.append("requested schema type must be 'object'")
    for key in sorted(set(schema) - _TOP_KEYS, key=str):
        problems.append(f"top-level keyword {_short(key)} is not allowed")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        problems.append("requested schema needs a 'properties' object")
        properties = {}
    for name, prop in properties.items():
        if len(problems) >= MAX_ERRORS:
            break
        _check_property(str(name), prop, problems)
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        problems.append("required must be a list of property names")
    else:
        for name in required:
            if name not in properties:
                problems.append(f"required property {_short(name)} is not defined")
    return [_bounded(p) for p in problems[:MAX_ERRORS]]
