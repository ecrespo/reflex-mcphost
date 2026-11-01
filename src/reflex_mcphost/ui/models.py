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

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from reflex_mcphost.core.config.models import (
    ConfigProblem,
    HttpTransport,
    ServerDefinition,
    ShadowedDefinition,
    StdioTransport,
)
from reflex_mcphost.core.consent.models import ConsentRequest
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.prompter import TrustRequest
from reflex_mcphost.ui.prompter import PendingPrompt, consent_options

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
    """Audit rows (REQ-UI-006); the argument hash is not shown."""
    return [
        {
            "id": r.invocation_id,
            "invocation_id": r.invocation_id,
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


def prompt_cards(pending: Iterable[PendingPrompt]) -> list[Row]:
    """One card per pending prompt, oldest first. Consent and trust cards are complete;
    sampling-review and elicitation cards carry kind and Server only (F6-06)."""
    cards: list[Row] = []
    for prompt in pending:
        payload = prompt.payload
        if prompt.kind == "consent" and isinstance(payload, ConsentRequest):
            cards.append(_consent_card(prompt, payload))
        elif prompt.kind == "trust" and isinstance(payload, TrustRequest):
            cards.append(_trust_card(prompt, payload))
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


def page(rows: Sequence[Row], query: str, *, limit: int = PAGE_LIMIT) -> tuple[list[Row], int]:
    """``(shown, more)``: the first ``limit`` rows matching ``query`` (case-insensitive
    substring over ``SEARCH_KEYS``) and how many further rows matched."""
    needle = query.strip().casefold()
    if needle:
        matched = [
            row
            for row in rows
            if any(
                isinstance(row.get(key), str) and needle in row[key].casefold()
                for key in SEARCH_KEYS
            )
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
