"""View models of the Host UI: JSON-serializable rows built from core snapshots (TD §5.1).

Imports no ``reflex`` (import-linter contract, delta D7), so mapping and filtering are
unit-tested without Reflex. Rows are plain ``dict`` / ``list`` / ``str`` / ``int`` /
``bool`` / ``None``; Reflex state vars hold only these values, never core objects.

Secrets (Art. 5, REQ-REG-007): rows carry env and header **keys** only; HTTP Servers show
their ``destination`` (``scheme://host[:port]``), never the URL path, query or userinfo;
stdio Servers show the command, not its arguments. Server-provided text (names,
descriptions, log lines) is carried verbatim, to be rendered as plain text.

List views render at most ``PAGE_LIMIT`` rows (``page``) plus a "N more" line (DD-011).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.config.models import (
    ConfigProblem,
    HttpTransport,
    ServerDefinition,
    ShadowedDefinition,
    StdioTransport,
)
from reflex_mcphost.core.consent.models import ConsentRequest
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    TrustRequest,
)
from reflex_mcphost.core.schema import SchemaCompileError, compile_schema
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui.prompter import MESSAGE_FIELD, PendingPrompt, consent_options

if TYPE_CHECKING:
    from reflex_mcphost.core.audit import AuditRecord
    from reflex_mcphost.core.catalog import (
        PromptEntry,
        ResourceEntry,
        ResourceTemplateEntry,
        ToolEntry,
    )
    from reflex_mcphost.core.connection.state import ServerStatus
    from reflex_mcphost.core.connection.stderr import LogEntry
    from reflex_mcphost.core.consent.models import ConsentRule
    from reflex_mcphost.core.invoker import InvocationResult

__all__ = [
    "LOG_LIMIT",
    "PAGE_LIMIT",
    "SEARCH_KEYS",
    "Row",
    "audit_rows",
    "catalog_rows",
    "log_rows",
    "page",
    "prompt_cards",
    "replace_server_rows",
    "rule_rows",
    "server_row",
    "server_rows",
]

Row = dict[str, Any]
PAGE_LIMIT = 200
"""Rows a list view renders at most (REQ-NFR-004, DD-011)."""
LOG_LIMIT = 200
"""Log lines a Server's log view renders at most (of the 1,000-entry buffer)."""
SEARCH_KEYS = ("id", "fqn", "title", "description")
"""Row keys ``page`` matches the filter against."""


class RegistryView(Protocol):
    """The read side of ``ServerRegistry`` the Servers rows need."""

    def get(self, server_id: str) -> ServerDefinition: ...

    def shadowed(self) -> list[ShadowedDefinition]: ...

    def problems(self) -> list[ConfigProblem]: ...


# ------------------------------------------------------------------ Servers


def _destination(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}"


def _definition_fields(definition: ServerDefinition | None) -> Row:
    if definition is None:
        return {
            "transport": None,
            "command": None,
            "destination": None,
            "enabled": None,
            "origin_kind": None,
            "origin_path": None,
            "env_keys": [],
            "header_keys": [],
        }
    transport = definition.transport
    origin = definition.origin
    fields: Row = {
        "transport": transport.kind,
        "command": None,
        "destination": None,
        "enabled": definition.enabled,
        "origin_kind": origin.kind.value,
        "origin_path": str(origin.path) if origin.path is not None else None,
        "env_keys": [],
        "header_keys": [],
    }
    if isinstance(transport, StdioTransport):
        fields["command"] = transport.command
        fields["env_keys"] = sorted(transport.env)
    elif isinstance(transport, HttpTransport):
        fields["destination"] = _destination(str(transport.url))
        fields["header_keys"] = sorted(transport.headers)
    return fields


def server_row(status: ServerStatus, registry: RegistryView | None) -> Row:
    """One Servers row: connection state from ``status``, definition facts (keys only),
    shadowed definitions and config problems from ``registry`` (``None``: not loaded)."""
    definition: ServerDefinition | None = None
    shadowed: list[Row] = []
    problems: list[str] = []
    if registry is not None:
        try:
            definition = registry.get(status.server_id)
        except UnknownServerError:
            definition = None
        shadowed = [
            {
                "origin_kind": s.definition.origin.kind.value,
                "origin_path": str(s.definition.origin.path)
                if s.definition.origin.path is not None
                else None,
            }
            for s in registry.shadowed()
            if s.definition.id == status.server_id
        ]
        problems = [p.message for p in registry.problems() if p.server_id == status.server_id]
    info = status.server_info
    capabilities = status.capabilities
    return {
        "id": status.server_id,
        "state": status.state.value,
        "reason": status.reason,
        "era": status.era,
        "protocol_version": status.protocol_version,
        "legacy_transport": status.legacy_transport,
        "server_name": (info.title or info.name) if info is not None else None,
        "capabilities": sorted(capabilities.model_dump(exclude_none=True))
        if capabilities is not None
        else [],
        **_definition_fields(definition),
        "shadowed": shadowed,
        "problems": problems,
    }


def server_rows(statuses: Iterable[ServerStatus], registry: RegistryView | None) -> list[Row]:
    """Servers rows, in the order of ``statuses``."""
    return [server_row(status, registry) for status in statuses]


# ------------------------------------------------------------------ Catalog


def _tool_row(entry: ToolEntry) -> Row:
    hints = entry.annotations
    return {
        "id": str(entry.fqn),
        "fqn": str(entry.fqn),
        "server_id": entry.server_id,
        "name": entry.name,
        "title": entry.title,
        "description": entry.description,
        "stale": entry.stale,
        "has_ui": entry.ui_resource_uri is not None,
        "read_only": hints.read_only_hint if hints is not None else None,
        "destructive": hints.destructive_hint if hints is not None else None,
    }


def _resource_row(entry: ResourceEntry) -> Row:
    return {
        "id": entry.uri,
        "server_id": entry.server_id,
        "uri": entry.uri,
        "name": entry.name,
        "title": entry.title,
        "description": entry.description,
        "mime_type": entry.mime_type,
        "size": entry.size,
        "stale": entry.stale,
    }


def _template_row(entry: ResourceTemplateEntry) -> Row:
    return {
        "id": str(entry.fqn),
        "fqn": str(entry.fqn),
        "server_id": entry.server_id,
        "name": entry.name,
        "uri_template": entry.uri_template,
        "title": entry.title,
        "description": entry.description,
        "mime_type": entry.mime_type,
        "stale": entry.stale,
    }


def _prompt_row(entry: PromptEntry) -> Row:
    return {
        "id": str(entry.fqn),
        "fqn": str(entry.fqn),
        "server_id": entry.server_id,
        "name": entry.name,
        "title": entry.title,
        "description": entry.description,
        "arguments": [
            {"name": a.name, "description": a.description, "required": bool(a.required)}
            for a in entry.arguments
        ],
        "stale": entry.stale,
    }


_CATALOG_BUILDERS: Mapping[str, Any] = {
    "tools": _tool_row,
    "resources": _resource_row,
    "resource_templates": _template_row,
    "prompts": _prompt_row,
}


def catalog_rows(kind: str, entries: Iterable[Any]) -> list[Row]:
    """Rows of one Catalog list: ``tools``, ``resources``, ``resource_templates`` or
    ``prompts`` (``catalog.changed`` list names)."""
    build = _CATALOG_BUILDERS[kind]
    return [build(entry) for entry in entries]


# ------------------------------------------------------------------ Permissions, Audit, logs


def rule_rows(rules: Iterable[ConsentRule]) -> list[Row]:
    """Permissions rows (REQ-UI-006)."""
    return [
        {
            "id": r.id,
            "effect": r.effect,
            "operation": r.operation.value,
            "target": str(r.target),
            "target_kind": r.target.kind,
            "server_id": r.target.server_id,
            "fqn": str(r.target.fqn) if r.target.fqn is not None else None,
            "consumer": str(r.consumer) if r.consumer is not None else None,
            "lifetime": r.lifetime,
            "origin": r.origin,
            "suspended": r.suspended,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rules
    ]


def audit_rows(records: Iterable[AuditRecord]) -> list[Row]:
    """Audit rows (REQ-UI-006, REQ-INV-009): the argument hash, never the arguments."""
    return [
        {
            "id": r.invocation_id,
            "invocation_id": r.invocation_id,
            "args_hash": r.args_hash,
            "operation": r.operation.value,
            "timestamp": r.timestamp.isoformat(),
            "consumer": str(r.consumer),
            "server_id": r.server_id,
            "fqn": str(r.fqn) if r.fqn is not None else None,
            "decision_source": r.decision_source,
            "rule_id": r.rule_id,
            "duration_ms": r.duration_ms,
            "outcome": r.outcome.value,
            "error_code": r.error_code,
            "input_rounds": r.input_rounds,
            "attribution": r.attribution,
        }
        for r in records
    ]


def log_rows(entries: Iterable[LogEntry], *, limit: int = LOG_LIMIT) -> list[Row]:
    """The last ``limit`` log lines (plain text)."""
    rows = [
        {
            "level": e.level,
            "logger": e.logger,
            "timestamp": e.timestamp.isoformat(),
            "text": e.text,
        }
        for e in entries
    ]
    return rows[-limit:] if limit > 0 else []


# ------------------------------------------------------------------ prompt cards (F6-03)

SCOPE_NOTICE = {
    "server": (
        "Applies to every tool of this Server, including tools added later or changed; "
        "a definition change does not suspend it."
    ),
    "global": (
        "Applies to every tool of every Server, including tools added later or changed; "
        "a definition change does not suspend it."
    ),
}
"""The REQ-CNS-007 statement shown when a server or global target is chosen."""


def _consent_card(prompt: PendingPrompt, request: ConsentRequest) -> Row:
    answers, targets, lifetimes = consent_options(request)
    tool = request.tool
    unattributed = request.attribution == "unattributed"
    return {
        "id": prompt.request_id,
        "kind": prompt.kind,
        "created_at": prompt.created_at.isoformat(),
        "operation": request.operation.value,
        "asking": "unattributed" if unattributed else str(request.consumer),
        "unattributed": unattributed,
        "server_id": request.server_id,
        "server_name": request.server_name,
        "fqn": str(tool.fqn) if tool is not None else None,
        "tool_title": tool.title if tool is not None else None,
        "description": tool.description if tool is not None else None,
        "preview": request.preview.body,
        "preview_format": request.preview.format,
        "redacted_fields": list(request.preview.redacted_fields),
        "definition_changed": request.definition_changed,
        "answers": list(answers),
        "targets": list(targets),
        "deny_lifetimes": list(lifetimes),
        "scope_notice": {t: SCOPE_NOTICE[t] for t in targets if t in SCOPE_NOTICE},
        **_sampling_fields(request),
    }


def _sampling_fields(request: ConsentRequest) -> Row:
    """What a sampling consent card shows (REQ-HSH-002): messages (text editable), system
    prompt, model preferences and max tokens; empty for tool cards."""
    review = request.sampling
    if review is None:
        return {
            "is_sampling": False,
            "messages": [],
            "system_prompt": "",
            "max_tokens": None,
            "temperature": None,
            "model_hints": [],
            "priorities": "",
            "include_context": "",
        }
    params = review.params
    prefs = params.model_preferences
    priorities = (
        ", ".join(
            f"{label} {value:g}"
            for label, value in (
                ("cost", prefs.cost_priority),
                ("speed", prefs.speed_priority),
                ("intelligence", prefs.intelligence_priority),
            )
            if value is not None
        )
        if prefs is not None
        else ""
    )
    return {
        "is_sampling": True,
        "messages": [
            {**content_row(m.content, role=m.role), "index": i, "field": f"{MESSAGE_FIELD}{i}"}
            for i, m in enumerate(params.messages)
        ],
        "system_prompt": params.system_prompt or "",
        "max_tokens": params.max_tokens,
        "temperature": params.temperature,
        "model_hints": [h.name for h in (prefs.hints or []) if h.name] if prefs is not None else [],
        "priorities": priorities,
        "include_context": params.include_context or "",
    }


def _trust_card(prompt: PendingPrompt, request: TrustRequest) -> Row:
    risky = set(request.risky_env_keys)
    return {
        "id": prompt.request_id,
        "kind": prompt.kind,
        "created_at": prompt.created_at.isoformat(),
        "server_id": request.server_id,
        "origin_kind": request.origin.kind.value,
        "origin_path": str(request.origin.path) if request.origin.path is not None else None,
        "transport": request.transport,
        "command": request.command,
        "resolved_command": request.resolved_command,
        "args": list(request.args),
        "cwd": str(request.cwd) if request.cwd is not None else None,
        "env": [{"name": name, "risky": name in risky} for name in request.env_keys],
        "destination": request.destination,
        "url_template": request.url_template,
        "header_keys": list(request.header_keys),
    }


def _elicitation_card(prompt: PendingPrompt, request: ElicitationRequest) -> Row:
    fields, fallback = (
        form_fields(request.requested_schema) if request.mode == "form" else ([], False)
    )
    return {
        "id": prompt.request_id,
        "kind": prompt.kind,
        "created_at": prompt.created_at.isoformat(),
        "server_id": request.server_id,
        "server_name": request.server_name,
        "message": request.message,
        "unattributed": request.attribution == "unattributed",
        "fields": fields,
        "json_fallback": fallback,
        "errors": {str(k): str(v) for k, v in request.errors.items()},
        "url": request.url if is_openable_url(request.url) else "",
        "url_refused": request.mode == "url" and not is_openable_url(request.url),
    }


def _review_card(prompt: PendingPrompt, review: SamplingResponseReview) -> Row:
    result = review.result
    completion = content_row(result.content, role=result.role)
    return {
        "id": prompt.request_id,
        "kind": prompt.kind,
        "created_at": prompt.created_at.isoformat(),
        "server_id": review.server_id,
        "model": result.model,
        "stop_reason": result.stop_reason or "",
        "completion": completion,
        "editable": completion["kind"] == "text",
    }


def prompt_cards(pending: Iterable[PendingPrompt]) -> list[Row]:
    """One card per pending prompt, oldest first: consent (tool or sampling), trust,
    elicitation (form / url) and sampling-response review cards."""
    cards: list[Row] = []
    for prompt in pending:
        payload = prompt.payload
        if prompt.kind == "consent" and isinstance(payload, ConsentRequest):
            cards.append(_consent_card(prompt, payload))
        elif prompt.kind == "trust" and isinstance(payload, TrustRequest):
            cards.append(_trust_card(prompt, payload))
        elif prompt.kind.startswith("elicitation") and isinstance(payload, ElicitationRequest):
            cards.append(_elicitation_card(prompt, payload))
        elif prompt.kind == "sampling_review" and isinstance(payload, SamplingResponseReview):
            cards.append(_review_card(prompt, payload))
        else:
            server_id = getattr(payload, "server_id", None)
            cards.append(
                {
                    "id": prompt.request_id,
                    "kind": prompt.kind,
                    "created_at": prompt.created_at.isoformat(),
                    "server_id": server_id if isinstance(server_id, str) else None,
                }
            )
    return cards


# ------------------------------------------------------------------ list helpers


def page(
    rows: Sequence[Row],
    query: str,
    *,
    limit: int = PAGE_LIMIT,
    keys: Sequence[str] = SEARCH_KEYS,
) -> tuple[list[Row], int]:
    """``(shown, more)``: the first ``limit`` rows matching ``query`` (case-insensitive
    substring over ``keys``, default ``SEARCH_KEYS``) and how many further rows matched."""
    needle = query.strip().casefold()
    if needle:
        matched = [
            row
            for row in rows
            if any(isinstance(row.get(key), str) and needle in row[key].casefold() for key in keys)
        ]
    else:
        matched = list(rows)
    return matched[:limit], max(0, len(matched) - limit)


def replace_server_rows(rows: Sequence[Row], server_id: str, new: Sequence[Row]) -> list[Row]:
    """``rows`` with the rows of ``server_id`` (key ``server_id``) replaced by ``new`` at
    the position of the first old one (appended when there was none)."""
    out: list[Row] = []
    placed = False
    for row in rows:
        if row.get("server_id") == server_id:
            if not placed:
                out.extend(new)
                placed = True
            continue
        out.append(row)
    if not placed:
        out.extend(new)
    return out


# ------------------------------------------------------------------ schema forms (F6-05)

JSON_FIELD = "__json__"
"""Raw form key of the whole-arguments JSON text area (fallback or "edit as JSON")."""
FIELD_KINDS = (
    "string",
    "password",
    "enum",
    "integer",
    "number",
    "boolean",
    "string_list",
    "enum_list",
    "json",
)
_COMPOSITE_KEYS = frozenset(
    {"oneOf", "anyOf", "allOf", "not", "if", "then", "else", "$ref", "patternProperties"}
)
_REQUIRED = "this field is required"
_INT_RE = re.compile(r"[+-]?\d+")
_TRUE = frozenset({"on", "true", "1", "yes"})
_FALSE = frozenset({"off", "false", "0", "no", ""})


class _FieldError(ValueError):
    pass


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _titled(options: object) -> tuple[list[str], list[str]] | None:
    """``[{const, title}, ...]`` → (values, labels); ``None`` unless every entry is one."""
    if not isinstance(options, list) or not options:
        return None
    values: list[str] = []
    labels: list[str] = []
    for item in options:
        if not isinstance(item, Mapping) or not isinstance(item.get("const"), str):
            return None
        values.append(item["const"])
        labels.append(_text(item.get("title")) or item["const"])
    return values, labels


def _enum(prop: Mapping[str, Any]) -> tuple[list[str], list[str]] | None:
    """String options of ``enum`` (+ ``enumNames``) or titled ``oneOf`` / ``anyOf``."""
    enum = prop.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum or not all(isinstance(v, str) for v in enum):
            return None
        names = prop.get("enumNames")
        labels = (
            list(names)
            if isinstance(names, list)
            and len(names) == len(enum)
            and all(isinstance(n, str) for n in names)
            else list(enum)
        )
        return list(enum), labels
    for key in ("oneOf", "anyOf"):
        if key in prop:
            return _titled(prop[key])
    return None


def _field(name: str, prop: object, required: bool) -> Row:
    field: Row = {
        "name": name,
        "label": name,
        "description": "",
        "kind": "json",
        "required": required,
        "options": [],
        "option_labels": [],
    }
    if not isinstance(prop, Mapping):
        return field
    field["label"] = _text(prop.get("title")) or name
    field["description"] = _text(prop.get("description"))
    kind = prop.get("type")
    options = _enum(prop) if kind in (None, "string") else None
    if options is not None:
        field["kind"] = "enum"
        field["options"], field["option_labels"] = options
    elif kind == "string" and not (_COMPOSITE_KEYS & set(prop)):
        field["kind"] = "password" if prop.get("format") == "password" else "string"
    elif kind in ("integer", "number", "boolean") and not (_COMPOSITE_KEYS & set(prop)):
        field["kind"] = kind
    elif kind == "array" and isinstance(prop.get("items"), Mapping):
        items = prop["items"]
        item_options = _enum(items) if items.get("type") in (None, "string") else None
        if item_options is not None:
            field["kind"] = "enum_list"
            field["options"], field["option_labels"] = item_options
        elif items.get("type") == "string" and not (_COMPOSITE_KEYS & set(items)):
            field["kind"] = "string_list"
    return field


def form_fields(schema: object) -> tuple[list[Row], bool]:
    """``(fields, json_fallback)`` of the form generated from an ``inputSchema`` (or an
    elicitation ``requestedSchema``). A top-level schema outside the subset (not a plain
    object schema) is one JSON text area: ``([], True)``."""
    if (
        not isinstance(schema, Mapping)
        or schema.get("type", "object") != "object"
        or _COMPOSITE_KEYS & set(schema)
    ):
        return [], True
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return [], True
    required_list = schema.get("required")
    required = (
        {r for r in required_list if isinstance(r, str)}
        if isinstance(required_list, list)
        else set()
    )
    fields = [
        _field(name, prop, name in required)
        for name, prop in properties.items()
        if isinstance(name, str)
    ]
    return fields, False


def _lines(value: object) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value if v.strip()]
    raise _FieldError("one value per line")


def _number(value: object, kind: str) -> int | float | None:
    if isinstance(value, bool):
        raise _FieldError(f"must be {'an integer' if kind == 'integer' else 'a number'}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and (kind == "number" or value.is_integer()):
            return int(value) if kind == "integer" else value
        raise _FieldError(f"must be {'an integer' if kind == 'integer' else 'a number'}")
    text = value.strip() if isinstance(value, str) else None
    if text is None:
        raise _FieldError(f"must be {'an integer' if kind == 'integer' else 'a number'}")
    if text == "":
        return None
    try:
        if _INT_RE.fullmatch(text):
            return int(text)
        if kind == "integer":
            raise ValueError
        parsed = float(text)
    except ValueError:
        raise _FieldError(f"must be {'an integer' if kind == 'integer' else 'a number'}") from None
    if not math.isfinite(parsed):
        raise _FieldError("must be a number")
    return parsed


def _coerce_field(field: Row, value: object) -> tuple[bool, Any]:
    """``(present, value)``; ``_FieldError`` for a value that cannot be coerced."""
    kind = field["kind"]
    if kind == "boolean":
        if value is None:
            return (True, False) if field["required"] else (False, None)
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return True, value.strip().lower() in _TRUE
        raise _FieldError("must be on or off")
    if value is None:
        return False, None
    if kind in ("string", "password", "enum"):
        if not isinstance(value, str):
            raise _FieldError("must be text")
        if value.strip() == "":
            return False, None
        if kind == "enum" and value not in field["options"]:
            raise _FieldError("choose one of the options")
        return True, value
    if kind in ("integer", "number"):
        number = _number(value, kind)
        return (number is not None), number
    if kind in ("string_list", "enum_list"):
        items = _lines(value)
        if kind == "enum_list" and any(item not in field["options"] for item in items):
            raise _FieldError("choose one of the options")
        return bool(items), items
    if isinstance(value, str):
        if value.strip() == "":
            return False, None
        try:
            return True, json.loads(value)
        except (ValueError, RecursionError):
            raise _FieldError("invalid JSON") from None
    return True, value


def coerce_json(text: object) -> tuple[dict[str, Any], dict[str, str]]:
    """The whole arguments from the JSON text area: an object, or a form-level error."""
    if not isinstance(text, str):
        return {}, {"": "invalid JSON"}
    if text.strip() == "":
        return {}, {}
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return {}, {"": "invalid JSON"}
    if not isinstance(value, dict):
        return {}, {"": "arguments must be a JSON object"}
    return value, {}


def coerce_form(schema: object, raw: object) -> tuple[dict[str, Any], dict[str, str]]:
    """``(arguments, field errors)`` from raw form values (strings, ``"on"``, lists).

    Never raises. Empty optional fields are omitted; a raw ``JSON_FIELD`` (or a schema
    outside the subset) sends the JSON text area as the whole arguments. Only the subset's
    shape is checked here — the Invoker validates against the full schema (REQ-INV-002).
    """
    values: Mapping[Any, Any] = raw if isinstance(raw, Mapping) else {}
    fields, fallback = form_fields(schema)
    if fallback or JSON_FIELD in values:
        return coerce_json(values.get(JSON_FIELD, ""))
    args: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field in fields:
        name = field["name"]
        try:
            present, value = _coerce_field(field, values.get(name))
        except _FieldError as exc:
            errors[name] = str(exc)
            continue
        if present:
            args[name] = value
        elif field["required"]:
            errors[name] = _REQUIRED
    return args, errors


_REQUIRED_RE = re.compile(r"^'([^']*)' is a required property")


def issue_errors(details: object) -> dict[str, str]:
    """Field → first message of ``InvocationError.details.issues`` (or ``SchemaIssue``
    dumps): the first segment of the issue path; a root ``required`` issue names its
    property; anything else is form-level (key ``""``)."""
    issues = details.get("issues") if isinstance(details, Mapping) else None
    if not isinstance(issues, list):
        return {}
    errors: dict[str, str] = {}
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        message = _text(issue.get("message"))
        field = _text(issue.get("path")).lstrip("/").split("/", 1)[0]
        if not field and issue.get("keyword") == "required":
            match = _REQUIRED_RE.match(message)
            field = match.group(1) if match else ""
        errors.setdefault(field, message)
    return errors


# ------------------------------------------------------------------ catalog details and results

IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
"""Image MIME types shown inline as ``data:`` URLs (no SVG: it is a document)."""
AUDIO_TYPES = frozenset(
    {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg", "audio/webm", "audio/mp4"}
)
"""Audio MIME types played from ``data:`` URLs."""
MAX_INLINE_BASE64 = 8 * 1024 * 1024
"""Larger media is shown as a placeholder (bounded rendering)."""
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_TEMPLATE_RE = re.compile(r"\{([^{}]*)\}")


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def tool_detail(entry: ToolEntry) -> Row:
    """A tool's detail panel: its row, the description and annotations as untrusted
    plain-text hints (REQ-CAT-011), input / output schema as JSON text and the form."""
    fields, fallback = form_fields(entry.input_schema)
    hints = (
        entry.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        if entry.annotations is not None
        else {}
    )
    return {
        **_tool_row(entry),
        "description": entry.description or "",
        "annotations": [{"name": str(k), "value": json.dumps(v)} for k, v in sorted(hints.items())],
        "input_schema": _json_text(entry.input_schema),
        "output_schema": _json_text(entry.output_schema) if entry.output_schema else "",
        "fields": fields,
        "json_fallback": fallback,
    }


def template_variables(uri_template: str) -> list[str]:
    """The RFC 6570 variable names of ``uri_template``, in order (no duplicates)."""
    names: dict[str, None] = {}
    for expression in _TEMPLATE_RE.findall(uri_template):
        for spec in expression.lstrip("+#./;?&").split(","):
            name = spec.split(":", 1)[0].rstrip("*").strip()
            if name:
                names.setdefault(name, None)
    return list(names)


def _block(kind: str, text: str = "", **extra: str) -> Row:
    return {
        "kind": kind,
        "text": text,
        "src": extra.get("src", ""),
        "mime_type": extra.get("mime_type", ""),
        "uri": extra.get("uri", ""),
        "role": extra.get("role", ""),
    }


def _media(kind: str, data: object, mime_type: object, allowed: frozenset[str]) -> Row:
    mime = mime_type.lower() if isinstance(mime_type, str) else ""
    if (
        isinstance(data, str)
        and mime in allowed
        and len(data) <= MAX_INLINE_BASE64
        and _BASE64_RE.fullmatch(data)
    ):
        return _block(kind, src=f"data:{mime};base64,{data}", mime_type=mime)
    return _block("placeholder", f"[{kind} {mime or 'unknown type'} not shown]", mime_type=mime)


def _resource_contents(contents: object) -> Row:
    uri = str(getattr(contents, "uri", ""))
    mime = getattr(contents, "mime_type", None) or ""
    text = getattr(contents, "text", None)
    if isinstance(text, str):
        return _block("text", text, uri=uri, mime_type=mime)
    blob = getattr(contents, "blob", None)
    if isinstance(blob, str) and mime.lower() in IMAGE_TYPES:
        row = _media("image", blob, mime, IMAGE_TYPES)
        row["uri"] = uri
        return row
    size = len(blob) * 3 // 4 if isinstance(blob, str) else 0
    return _block("placeholder", f"[binary {mime or 'data'}, ~{size} bytes]", uri=uri)


def content_row(block: object, *, role: str = "") -> Row:
    """One content block as a view row (plain text; images / audio only as ``data:`` URLs
    of an allowed MIME type; anything else is a placeholder naming its type)."""
    kind = getattr(block, "type", None)
    if kind == "text":
        row = _block("text", _text(getattr(block, "text", "")))
    elif kind == "image":
        row = _media(
            "image", getattr(block, "data", None), getattr(block, "mime_type", None), IMAGE_TYPES
        )
    elif kind == "audio":
        row = _media(
            "audio", getattr(block, "data", None), getattr(block, "mime_type", None), AUDIO_TYPES
        )
    elif kind == "resource_link":
        uri = str(getattr(block, "uri", ""))
        name = _text(getattr(block, "name", ""))
        row = _block("resource_link", f"{name} — {uri}" if name else uri, uri=uri)
    elif kind == "resource":
        row = _resource_contents(getattr(block, "resource", None))
    else:
        row = _block("placeholder", f"[{kind or 'unknown'} content not shown]")
    row["role"] = role
    return row


def content_rows(blocks: Iterable[object]) -> list[Row]:
    """Rows of a list of content blocks (tool results, sampling messages)."""
    return [content_row(b) for b in blocks]


def resource_rows(contents: Iterable[object]) -> list[Row]:
    """Rows of a ``resources/read`` result (REQ-CAT-005)."""
    return [_resource_contents(c) for c in contents]


def prompt_rows(messages: Iterable[object]) -> list[Row]:
    """Rows of a ``prompts/get`` result, each tagged with its role (REQ-CAT-007)."""
    return [
        content_row(getattr(m, "content", None), role=_text(getattr(m, "role", "")))
        for m in messages
    ]


def progress_row(progress: object) -> Row:
    """One progress notification (REQ-INV-005)."""
    value = getattr(progress, "progress", 0.0)
    total = getattr(progress, "total", None)
    message = getattr(progress, "message", None)
    shown = f"{value:g}/{total:g}" if isinstance(total, int | float) else f"{value:g}"
    return {
        "progress": value,
        "total": total,
        "message": message or "",
        "text": f"{shown} {message}".strip() if message else shown,
    }


def result_view(result: InvocationResult) -> Row:
    """The result panel (REQ-INV-006): outcome badge, error code and message, content
    blocks and ``structuredContent`` as JSON text."""
    error = result.error
    return {
        "id": result.id,
        "fqn": str(result.fqn),
        "consumer": str(result.consumer),
        "outcome": result.outcome.value,
        "ok": result.ok,
        "error_code": error.code if error is not None else "",
        "error_message": error.message if error is not None else "",
        "blocks": content_rows(result.content),
        "structured": _json_text(result.structured_content)
        if result.structured_content is not None
        else "",
        "duration_ms": result.duration_ms,
    }


# ------------------------------------------------------------------ audit filters (F6-07)

AUDIT_FILTERS = ("since", "until", "server_id", "fqn", "consumer", "outcome", "operation")
"""The Audit view's filters (REQ-INV-011 plus operation)."""
RULE_SEARCH_KEYS = ("id", "target", "consumer", "effect", "lifetime", "operation", "origin")
"""Row keys the Permissions filter matches."""
AUDIT_PAGE_SIZE = 50
ANY = "any"
"""A choice filter's "no filter" value."""


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def audit_query(
    filters: Mapping[str, str], cursor: str | None = None, *, limit: int | None = None
) -> tuple[AuditQuery | None, str]:
    """``(query, "")`` for the Audit view's filter values (empty → unset), or
    ``(None, message)`` naming the filter that does not parse."""
    values = {
        k: v.strip()
        for k, v in filters.items()
        if isinstance(v, str) and v.strip() and v.strip() != ANY
    }
    parsed: dict[str, Any] = {}
    parsers: dict[str, Callable[[str], Any]] = {
        "since": _instant,
        "until": _instant,
        "server_id": str,
        "fqn": Fqn.parse,
        "consumer": Consumer.parse,
        "outcome": InvocationOutcome,
        "operation": OperationKind,
    }
    for key in AUDIT_FILTERS:
        if key not in values:
            continue
        try:
            parsed[key] = parsers[key](values[key])
        except (ValueError, TypeError):
            return None, f"invalid {key.replace('_', ' ')} filter"
    try:
        size = limit if limit is not None else AUDIT_PAGE_SIZE
        return AuditQuery(**parsed, cursor=cursor or None, limit=size), ""
    except ValueError:
        return None, "invalid filter"


# ------------------------------------------------------------------ elicitation answers (F6-06)


def is_openable_url(url: object) -> bool:
    """Whether the UI may open ``url`` with an external redirect: ``http`` / ``https``
    with a hostname only (a second layer after core's own check; ``javascript:``,
    ``data:``, ``file:`` and host-less URLs are never opened or shown as links)."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(host)


def elicitation_answer(
    request: ElicitationRequest, action: str, raw: object = None
) -> tuple[ElicitationResponse | None, dict[str, str]]:
    """``(response, {})`` for a card answer, or ``(None, field errors)`` when accepted
    form content does not satisfy the requested schema (nothing reaches the Server,
    REQ-HSH-013). ``ValueError`` for an action the card does not offer."""
    if action in ("decline", "cancel"):
        return ElicitationResponse(action=action), {}
    if action != "accept":
        raise ValueError(f"unknown elicitation action {action!r}")
    if request.mode == "url":
        if not is_openable_url(request.url):
            return ElicitationResponse(action="decline"), {}  # never opened: declined
        return ElicitationResponse(action="accept"), {}
    schema = request.requested_schema or {"type": "object", "properties": {}}
    content, errors = coerce_form(schema, raw)
    if errors:
        return None, errors
    try:
        validation = compile_schema(schema).validate(content)
    except SchemaCompileError:
        return None, {"": "the requested form cannot be validated"}
    if not validation.ok:
        return None, issue_errors({"issues": [i.model_dump() for i in validation.issues]}) or {
            "": "the answer does not match the requested form"
        }
    try:
        return ElicitationResponse(action="accept", content=content), {}
    except ValueError:
        return None, {"": "the answer does not match the requested form"}
