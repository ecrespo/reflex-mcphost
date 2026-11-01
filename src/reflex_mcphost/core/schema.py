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
* Server-supplied regexes never run on stdlib ``re`` (REQ-NFR-021): the validator classes are
  ``jsonschema.validators.extend``ed with replacements for ``pattern``,
  ``patternProperties``, ``additionalProperties`` and ``unevaluatedProperties`` (and the
  meta-schema's ``format: regex`` check) that compile with ``regex`` and match with
  ``timeout=`` the remaining per-``validate()`` budget (``PATTERN_VALIDATE_BUDGET``),
  capped at ``PATTERN_MATCH_TIMEOUT`` per match. ``propertyNames`` reaches ``pattern``
  through its subschema. A timeout or an exhausted budget is an unusable schema
  (``SchemaCompileError("pattern evaluation timed out")``), never valid input. Matching runs
  inline: the bound keeps one validation from blocking the caller (the event loop) longer
  than the budget plus one match overshoot.
* Error output is bounded: at most ``MAX_ERRORS`` issues with messages truncated to
  ``MAX_MESSAGE_LENGTH`` and passed through ``core.secrets.redact``. Messages never echo
  the instance (an argument may be secret-like without being a registered secret,
  REQ-NFR-010): its ``repr`` in the ``jsonschema`` message is replaced by ``value``.

A compile error is contained per tool: the caller keeps the tool listed and turns the
error into an invocation outcome (``protocol_error`` / ``invalid_arguments``).
"""

from __future__ import annotations

import contextvars
import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # no stubs shipped
import jsonschema.exceptions  # type: ignore[import-untyped]
import jsonschema.validators  # type: ignore[import-untyped]
import referencing
import referencing.exceptions
import referencing.jsonschema
import regex  # type: ignore[import-untyped]  # no stubs shipped; no new dev dependency
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
PATTERN_MATCH_TIMEOUT = 0.05
"""Seconds one Server-regex match may run (REQ-NFR-021)."""
PATTERN_VALIDATE_BUDGET = 0.25
"""Seconds of Server-regex matching one ``validate()`` may spend in total (REQ-NFR-021)."""
_TIMED_OUT = "pattern evaluation timed out"
_NAME_LENGTH = 64
_ELLIPSIS = "…"

_BASE_DIALECTS: dict[str, Any] = {
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


def _value_free(error: Any) -> str:
    """``error.message`` with the instance's ``repr`` (how ``jsonschema`` quotes it) removed."""
    return str(error.message).replace(repr(error.instance), "value")


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


def _node_patterns(node: dict[str, Any]) -> list[Any]:
    patterns: list[Any] = []
    if isinstance(node.get("pattern"), str):
        patterns.append(node["pattern"])
    if isinstance(node.get("patternProperties"), dict):
        patterns.extend(node["patternProperties"])
    return patterns


def _check_patterns(node: dict[str, Any]) -> None:
    for pattern in _node_patterns(node):
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise SchemaCompileError(f"pattern longer than {MAX_PATTERN_LENGTH} characters")
        _compile_pattern(pattern)


def _compile_pattern(pattern: Any) -> Any:
    """``regex`` (never stdlib ``re``) compile of a Server pattern; invalid → compile error."""
    if not isinstance(pattern, str):
        raise SchemaCompileError("pattern must be a string")
    try:
        return regex.compile(pattern)
    except (regex.error, ValueError, OverflowError, RecursionError) as exc:
        raise SchemaCompileError(f"invalid pattern: {exc}") from None


def _collect_patterns(schema: Any) -> dict[str, Any]:
    """Every pattern in ``schema`` compiled once, cached with its validator (DD-010)."""
    compiled: dict[str, Any] = {}
    stack: list[Any] = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for pattern in _node_patterns(node):
                if pattern not in compiled:
                    compiled[pattern] = _compile_pattern(pattern)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return compiled


# ------------------------------------------------------------ bounded regex keywords

_clock: Callable[[], float] = time.perf_counter


class _PatternBudget:
    """Remaining match time of one ``validate()`` plus the validator's compiled patterns."""

    __slots__ = ("patterns", "remaining")

    def __init__(self, patterns: dict[str, Any]) -> None:
        self.patterns = patterns
        self.remaining = PATTERN_VALIDATE_BUDGET


# jsonschema validators are immutable and re-created by ``evolve`` on every descent, so the
# per-call budget travels in a context variable set by ``CompiledSchema.validate`` (each
# thread / task sees its own).
_BUDGET: contextvars.ContextVar[_PatternBudget | None] = contextvars.ContextVar(
    "reflex_mcphost_pattern_budget", default=None
)


def _search(pattern: str, text: str) -> bool:
    """Bounded ``search`` of a Server pattern (REQ-NFR-021)."""
    budget = _BUDGET.get()
    if budget is None:  # a validator used outside ``validate()``: per-match bound only
        budget = _PatternBudget({})
    compiled = budget.patterns.get(pattern)
    if compiled is None:
        compiled = budget.patterns[pattern] = _compile_pattern(pattern)
    if budget.remaining <= 0:
        raise SchemaCompileError(_TIMED_OUT)
    start = _clock()
    try:
        found = compiled.search(text, timeout=min(PATTERN_MATCH_TIMEOUT, budget.remaining))
    except TimeoutError:
        raise SchemaCompileError(_TIMED_OUT) from None
    finally:
        budget.remaining -= _clock() - start
    return found is not None


def _any_match(patterns: Iterable[Any], key: str) -> bool:
    return any(_search(pattern, key) for pattern in patterns)


def _extras_msg(extras: list[Any]) -> str:
    verb = "was" if len(extras) == 1 else "were"
    return f"{', '.join(repr(extra) for extra in extras)} {verb}"


def _is_valid(errors: Iterator[Any]) -> bool:
    return next(errors, None) is None


def _kw_pattern(validator: Any, patrn: Any, instance: Any, schema: Any) -> Iterator[Any]:
    if validator.is_type(instance, "string") and not _search(patrn, instance):
        yield jsonschema.exceptions.ValidationError(f"{instance!r} does not match {patrn!r}")


def _kw_pattern_properties(
    validator: Any, pattern_properties: Any, instance: Any, schema: Any
) -> Iterator[Any]:
    if not validator.is_type(instance, "object"):
        return
    for pattern, subschema in pattern_properties.items():
        for key, value in instance.items():
            if _search(pattern, key):
                yield from validator.descend(value, subschema, path=key, schema_path=pattern)


def _additional_keys(instance: Any, schema: Any) -> list[Any]:
    properties = schema.get("properties", {})
    patterns = list(schema.get("patternProperties", {}))
    return [
        key
        for key in instance
        if key not in properties and not (patterns and _any_match(patterns, key))
    ]


def _kw_additional_properties(
    validator: Any, additional: Any, instance: Any, schema: Any
) -> Iterator[Any]:
    if not validator.is_type(instance, "object"):
        return
    extras = _additional_keys(instance, schema)
    if validator.is_type(additional, "object"):
        for extra in extras:
            yield from validator.descend(instance[extra], additional, path=extra)
    elif not additional and extras:
        if "patternProperties" in schema:
            verb = "does" if len(extras) == 1 else "do"
            joined = ", ".join(repr(each) for each in sorted(extras, key=str))
            patterns = ", ".join(repr(each) for each in sorted(schema["patternProperties"]))
            message = f"{joined} {verb} not match any of the regexes: {patterns}"
        else:
            message = (
                "Additional properties are not allowed "
                f"({_extras_msg(sorted(extras, key=str))} unexpected)"
            )
        yield jsonschema.exceptions.ValidationError(message)


def _resolved(validator: Any, ref: Any) -> tuple[Any, Any]:
    resolved = validator._resolver.lookup(ref)  # read-only use of the validator's resolver
    sub = validator.evolve(schema=resolved.contents, _resolver=resolved.resolver)
    return sub, resolved.contents


def _evaluated_keys_2020(validator: Any, instance: Any, schema: Any) -> list[Any]:
    """Keys evaluated under ``schema`` (Draft 2020-12 semantics), pattern matching bounded."""
    if validator.is_type(schema, "boolean"):
        return []
    keys: list[Any] = []
    for ref_keyword in ("$ref", "$dynamicRef"):
        ref = schema.get(ref_keyword)
        if ref is not None:
            sub, contents = _resolved(validator, ref)
            keys.extend(_evaluated_keys_2020(sub, instance, contents))
    properties = schema.get("properties")
    if validator.is_type(properties, "object"):
        keys += properties.keys() & instance.keys()
    for keyword in ("additionalProperties", "unevaluatedProperties"):
        subschema = schema.get(keyword)
        if subschema is None:
            continue
        keys += (
            key for key, value in instance.items() if _is_valid(validator.descend(value, subschema))
        )
    keys += _pattern_keys(instance, schema)
    keys += _branch_keys(validator, instance, schema, _evaluated_keys_2020)
    return keys


def _evaluated_keys_2019(validator: Any, instance: Any, schema: Any) -> list[Any]:
    """Keys evaluated under ``schema`` (Draft 2019-09 semantics), pattern matching bounded."""
    if validator.is_type(schema, "boolean"):
        return []
    keys: list[Any] = []
    ref = schema.get("$ref")
    if ref is not None:
        sub, contents = _resolved(validator, ref)
        keys.extend(_evaluated_keys_2019(sub, instance, contents))
    if "$recursiveRef" in schema:
        resolved = referencing.jsonschema.lookup_recursive_ref(validator._resolver)
        sub = validator.evolve(schema=resolved.contents, _resolver=resolved.resolver)
        keys.extend(_evaluated_keys_2019(sub, instance, resolved.contents))
    for keyword in ("properties", "additionalProperties", "unevaluatedProperties"):
        if keyword in schema:
            value = schema[keyword]
            if validator.is_type(value, "boolean") and value:
                keys += instance.keys()
            elif validator.is_type(value, "object"):
                keys += [name for name in value if name in instance]
    keys += _pattern_keys(instance, schema)
    keys += _branch_keys(validator, instance, schema, _evaluated_keys_2019)
    return keys


def _pattern_keys(instance: Any, schema: Any) -> list[Any]:
    if "patternProperties" not in schema:
        return []
    return [
        key for key in instance for pattern in schema["patternProperties"] if _search(pattern, key)
    ]


def _branch_keys(
    validator: Any,
    instance: Any,
    schema: Any,
    walk: Callable[[Any, Any, Any], list[Any]],
) -> list[Any]:
    keys: list[Any] = []
    for name, subschema in schema.get("dependentSchemas", {}).items():
        if name in instance:
            keys += walk(validator, instance, subschema)
    for keyword in ("allOf", "oneOf", "anyOf"):
        for subschema in schema.get(keyword, []):
            if _is_valid(validator.descend(instance, subschema)):
                keys += walk(validator, instance, subschema)
    if "if" in schema:
        if validator.evolve(schema=schema["if"]).is_valid(instance):
            keys += walk(validator, instance, schema["if"])
            if "then" in schema:
                keys += walk(validator, instance, schema["then"])
        elif "else" in schema:
            keys += walk(validator, instance, schema["else"])
    return keys


def _unevaluated_properties(
    walk: Callable[[Any, Any, Any], list[Any]],
) -> Callable[[Any, Any, Any, Any], Iterator[Any]]:
    def keyword(validator: Any, unevaluated: Any, instance: Any, schema: Any) -> Iterator[Any]:
        if not validator.is_type(instance, "object"):
            return
        evaluated = walk(validator, instance, schema)
        failing: list[Any] = []
        for key in instance:
            if key not in evaluated:
                for _ in validator.descend(instance[key], unevaluated, path=key, schema_path=key):
                    failing.append(key)
        if failing:
            if unevaluated is False:
                message = (
                    "Unevaluated properties are not allowed "
                    f"({_extras_msg(sorted(failing, key=str))} unexpected)"
                )
            else:
                message = (
                    "Unevaluated properties are not valid under the given schema "
                    f"({_extras_msg(failing)} unevaluated and invalid)"
                )
            yield jsonschema.exceptions.ValidationError(message)

    return keyword


def _is_regex_format(instance: object) -> bool:
    if not isinstance(instance, str):
        return True
    return bool(regex.compile(instance))


def _meta_format_checker(cls: Any) -> Any:
    """The dialect's meta-schema format checker with ``format: regex`` checked by ``regex``."""
    checker = jsonschema.FormatChecker(formats=())
    checker.checkers = dict(cls.FORMAT_CHECKER.checkers)
    checker.checks("regex", raises=regex.error)(_is_regex_format)
    return checker


def _bounded_dialect(cls: Any) -> tuple[Any, Any]:
    replacements: dict[str, Any] = {
        "pattern": _kw_pattern,
        "patternProperties": _kw_pattern_properties,
        "additionalProperties": _kw_additional_properties,
    }
    if cls is jsonschema.Draft202012Validator:
        replacements["unevaluatedProperties"] = _unevaluated_properties(_evaluated_keys_2020)
    elif cls is jsonschema.Draft201909Validator:
        replacements["unevaluatedProperties"] = _unevaluated_properties(_evaluated_keys_2019)
    return jsonschema.validators.extend(cls, validators=replacements), _meta_format_checker(cls)


_DIALECTS: dict[str, tuple[Any, Any]] = {
    uri: _bounded_dialect(cls) for uri, cls in _BASE_DIALECTS.items()
}
_DEFAULT_DIALECT = _DIALECTS["json-schema.org/draft/2020-12/schema"]


def schema_hash(schema: Any) -> str:
    """Definition hash of a schema (key-order independent); a cache key, not a secret."""
    try:
        text = canonical_json(schema)
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaCompileError(f"schema is not canonical JSON ({type(exc).__name__})") from None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validator_class(schema: Any) -> tuple[Any, Any]:
    """The bounded validator class and meta-schema format checker for ``schema``'s dialect."""
    if isinstance(schema, dict) and isinstance(schema.get("$schema"), str):
        uri = schema["$schema"].split("://", 1)[-1].rstrip("#")
        return _DIALECTS.get(uri, _DEFAULT_DIALECT)
    return _DEFAULT_DIALECT


class CompiledSchema:
    """A checked schema and its validator. ``validate`` never raises for bad instances."""

    __slots__ = ("_patterns", "_validator", "definition_hash")

    def __init__(self, schema: Any, definition_hash: str) -> None:
        cls, format_checker = _validator_class(schema)
        self._patterns = _collect_patterns(schema)
        try:
            cls.check_schema(schema, format_checker=format_checker)
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
        (unresolvable/remote ``$ref``, ``$ref`` loop, a Server regex that hit its time bound,
        REQ-NFR-021)."""
        if _depth_exceeds(instance, MAX_INSTANCE_DEPTH):
            issue = SchemaIssue(
                path="", keyword="depth", message=f"nesting deeper than {MAX_INSTANCE_DEPTH}"
            )
            return SchemaValidation(issues=(issue,))
        token = _BUDGET.set(_PatternBudget(self._patterns))
        try:
            return self._collect(instance)
        finally:
            _BUDGET.reset(token)

    def _collect(self, instance: Any) -> SchemaValidation:
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
                        message=_bounded(_value_free(error)),
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
