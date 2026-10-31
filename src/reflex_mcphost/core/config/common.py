"""Shared parsing helpers: problems, safe error text, expansion and definition building.

Messages built here never contain input values (Art. 5): pydantic errors are described by
location and error type only, variable errors by variable name and field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from reflex_mcphost.core.config.expand import (
    UnresolvedVariableError,
    UnsupportedVariableError,
    expand,
)
from reflex_mcphost.core.config.models import (
    VARIABLE_PROBLEMS,
    ConfigOrigin,
    ConfigProblem,
    ConfigProblemCode,
    HttpTransport,
    OAuthSettings,
    ParseOutcome,
    ServerDefinition,
    StdioTransport,
)
from reflex_mcphost.core.secrets import register_secret
from reflex_mcphost.core.types import parse_server_id

TransportKind = Literal["stdio", "http", "sse"]
_MAX_ID_IN_MESSAGE = 80


@dataclass
class RawEntry:
    """A format-neutral, not yet expanded Server entry."""

    kind: TransportKind
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    oauth: OAuthSettings | None = None
    options: dict[str, Any] = field(default_factory=dict)


def describe_validation_error(exc: ValidationError, *, prefix: str = "") -> str:
    """Field locations and error types only — never ``str(exc)``, which echoes input."""
    parts: list[str] = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(part) for part in (prefix, *err["loc"]) if part != "")
        parts.append(f"{loc or '<root>'}: {err['type']}")
    return "; ".join(parts)


def problem(
    origin: ConfigOrigin,
    code: ConfigProblemCode,
    message: str,
    server_id: str | None = None,
) -> ConfigProblem:
    return ConfigProblem(origin=origin, server_id=server_id, code=code, message=message)


def where(origin: ConfigOrigin) -> str:
    return str(origin.path) if origin.path is not None else "<api>"


def check_server_id(raw_id: str, origin: ConfigOrigin) -> ConfigProblem | None:
    """``invalid_server_id`` naming the (truncated) id, or ``None`` when valid (REQ-REG-012)."""
    try:
        parse_server_id(raw_id)
    except ValueError:
        shown = raw_id[:_MAX_ID_IN_MESSAGE]
        return problem(
            origin,
            ConfigProblemCode.INVALID_SERVER_ID,
            f"{where(origin)}: invalid server id {shown!r}: must match [A-Za-z0-9_.-]{{1,64}}",
        )
    return None


class _Expander:
    """Expands values, collecting variable problems; a failed value is kept raw."""

    def __init__(
        self,
        env: Mapping[str, str],
        cwd: Path | None,
        origin: ConfigOrigin,
        server_id: str,
    ) -> None:
        self._env = env
        self._cwd = cwd
        self._origin = origin
        self._server_id = server_id
        self.problems: list[ConfigProblem] = []

    def __call__(self, value: str, field_name: str) -> str:
        try:
            return expand(value, self._env, cwd=self._cwd)
        except UnresolvedVariableError as exc:
            code = ConfigProblemCode.UNRESOLVED_VARIABLE
            detail = f"variable {exc.name!r} is not set and has no default"
        except UnsupportedVariableError as exc:
            code = ConfigProblemCode.UNSUPPORTED_VARIABLE
            detail = f"unsupported variable form '${{{exc.name}}}'"
        self.problems.append(
            problem(
                self._origin,
                code,
                f"{where(self._origin)}: server {self._server_id!r}, {field_name}: {detail}",
                self._server_id,
            )
        )
        return value


def _secret(value: str) -> SecretStr:
    register_secret(value)
    return SecretStr(value)


def _transport(raw: RawEntry, ex: _Expander) -> dict[str, Any]:
    if raw.kind == "stdio":
        cwd = ex(raw.cwd, "cwd") if raw.cwd is not None else None
        return {
            "kind": "stdio",
            "command": ex(raw.command or "", "command"),
            "args": tuple(ex(a, f"args[{i}]") for i, a in enumerate(raw.args)),
            "env": {k: _secret(ex(v, f"env.{k}")) for k, v in raw.env.items()},
            "cwd": Path(cwd) if cwd is not None else None,
        }
    if raw.oauth is not None and raw.oauth.client_secret is not None:
        register_secret(raw.oauth.client_secret.get_secret_value())
    return {
        "kind": "http",
        "url": ex(raw.url or "", "url"),
        "headers": {k: _secret(ex(v, f"headers.{k}")) for k, v in raw.headers.items()},
        "oauth": raw.oauth,
        "legacy_sse": raw.kind == "sse",
    }


def build_definition(
    server_id: str,
    raw: RawEntry,
    origin: ConfigOrigin,
    *,
    env: Mapping[str, str],
    cwd: Path | None,
) -> tuple[ServerDefinition | None, list[ConfigProblem]]:
    """Expand and validate one entry. Variable errors keep the definition (REQ-REG-005);
    any other validation error drops it with ``invalid_definition``."""
    ex = _Expander(env, cwd, origin, server_id)
    fields = _transport(raw, ex)
    model: type[StdioTransport | HttpTransport] = (
        StdioTransport if fields["kind"] == "stdio" else HttpTransport
    )
    try:
        transport: StdioTransport | HttpTransport = model.model_validate(fields)
    except ValidationError as exc:
        if not ex.problems:
            return None, [_invalid(origin, server_id, describe_validation_error(exc))]
        # A raw placeholder may not be a valid value (e.g. a URL); the Server is kept for
        # its `config_error` state and never connected (REQ-REG-005).
        transport = model.model_construct(**fields)
    try:
        definition = ServerDefinition(
            id=server_id, transport=transport, origin=origin, **raw.options
        )
    except ValidationError as exc:
        return None, [_invalid(origin, server_id, describe_validation_error(exc))]
    return definition, ex.problems


def _invalid(origin: ConfigOrigin, server_id: str, detail: str) -> ConfigProblem:
    return problem(
        origin,
        ConfigProblemCode.INVALID_DEFINITION,
        f"{where(origin)}: server {server_id!r}: {detail}",
        server_id,
    )


def has_variable_problem(problems: list[ConfigProblem] | tuple[ConfigProblem, ...]) -> bool:
    return any(p.code in VARIABLE_PROBLEMS for p in problems)


# ------------------------------------------------------------------ JSON (third-party)

_STDIO_TYPES = frozenset({"stdio"})
_HTTP_TYPES = frozenset({"http", "streamable-http", "streamableHttp"})
_SSE_TYPES = frozenset({"sse"})


class _JsonEntry(BaseModel):
    """Fields we read from a third-party entry; unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}
    disabled: bool = False


def _json_kind(entry: _JsonEntry) -> TransportKind | str:
    """The transport kind, or an error description."""
    has_command, has_url = entry.command is not None, entry.url is not None
    if entry.type is None:
        if has_command == has_url:
            return "exactly one of 'command' or 'url' is required"
        return "stdio" if has_command else "http"
    if entry.type in _STDIO_TYPES:
        kind: TransportKind = "stdio"
    elif entry.type in _HTTP_TYPES:
        kind = "http"
    elif entry.type in _SSE_TYPES:
        kind = "sse"
    else:
        return "unsupported 'type'"
    if kind == "stdio" and (not has_command or has_url):
        return "type 'stdio' requires 'command' and no 'url'"
    if kind != "stdio" and (not has_url or has_command):
        return f"type {entry.type!r} requires 'url' and no 'command'"
    return kind


def parse_servers_json(
    text: str,
    origin: ConfigOrigin,
    *,
    key: str,
    env: Mapping[str, str],
    cwd: Path | None,
) -> ParseOutcome:
    """Parse a ``{key: {id: entry}}`` JSON file. Never raises (REQ-REG-006).

    ``cwd`` enables ``${workspaceFolder}`` (VS Code only); other formats pass ``None``.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # message has positions only, never content
        return _parse_error(origin, f"invalid JSON: {exc}")
    if not isinstance(data, dict):
        return _parse_error(origin, "top level must be a JSON object")
    servers = data.get(key)
    if servers is None:
        return ParseOutcome()
    if not isinstance(servers, dict):
        return _parse_error(origin, f"{key!r} must be a JSON object")
    definitions: list[ServerDefinition] = []
    problems: list[ConfigProblem] = []
    for server_id, value in servers.items():
        bad_id = check_server_id(server_id, origin)
        if bad_id is not None:
            problems.append(bad_id)
            continue
        raw = _json_raw(value)
        if isinstance(raw, str):
            problems.append(_invalid(origin, server_id, raw))
            continue
        definition, found = build_definition(server_id, raw, origin, env=env, cwd=cwd)
        problems += found
        if definition is not None:
            definitions.append(definition)
    return ParseOutcome(tuple(definitions), tuple(problems))


def _json_raw(value: object) -> RawEntry | str:
    if not isinstance(value, dict):
        return "entry must be a JSON object"
    try:
        entry = _JsonEntry.model_validate(value)
    except ValidationError as exc:
        return describe_validation_error(exc)
    kind = _json_kind(entry)
    if kind not in ("stdio", "http", "sse"):
        return kind
    return RawEntry(
        kind=kind,  # type: ignore[arg-type]  # narrowed by the membership test above
        command=entry.command,
        args=entry.args,
        env=entry.env,
        cwd=entry.cwd,
        url=entry.url,
        headers=entry.headers,
        options={"enabled": not entry.disabled},
    )


def _parse_error(origin: ConfigOrigin, detail: str) -> ParseOutcome:
    return ParseOutcome(
        problems=(problem(origin, ConfigProblemCode.PARSE_ERROR, f"{where(origin)}: {detail}"),)
    )
