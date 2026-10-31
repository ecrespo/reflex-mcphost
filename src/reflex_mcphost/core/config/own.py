"""Own ``mcphost.toml`` files: the user-level file (strict) and the project file (filtered).

The user-level file (or ``--config PATH``) is strict: a malformed file, an unknown key or an
unsupported ``schema_version`` raises ``ConfigError`` so a typo never silently disables a
security setting (API §5.2). The project-level ``<cwd>/mcphost.toml`` never raises: it
contributes only ``schema_version`` and ``[servers.*]``; any other key is reported as
``project_setting_ignored`` (REQ-REG-014). Writing touches the user-level file only, with
``tomlkit`` (comments and order kept) and an atomic replace (DD-013).
"""

from __future__ import annotations

import os
import stat
import tempfile
import tomllib
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path
from typing import Annotated, Any

import tomlkit
import tomlkit.exceptions
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
)
from tomlkit import TOMLDocument
from tomlkit.items import Table

from reflex_mcphost.core.config.common import (
    RawEntry,
    build_definition,
    check_server_id,
    describe_validation_error,
    problem,
    where,
)
from reflex_mcphost.core.config.expand import VariableError, expand
from reflex_mcphost.core.config.models import (
    DEFAULT_OAUTH_CLIENT_METADATA_URL,
    DEFAULT_SECRET_ARG_PATTERN,
    ConfigOrigin,
    ConfigProblem,
    ConfigProblemCode,
    HostConfig,
    LogLevel,
    Mode,
    OAuthSettings,
    ParseOutcome,
    SamplingConfig,
    ServerDefinition,
    ServerOverride,
    SourceKind,
    SourcesConfig,
    StdioTransport,
)
from reflex_mcphost.core.config.paths import default_user_file
from reflex_mcphost.core.errors import ConfigError
from reflex_mcphost.core.types import parse_server_id

SCHEMA_VERSION = 1
PROJECT_KEYS = frozenset({"schema_version", "servers"})
USER_PRECEDENCE = 2


class _ServerTable(BaseModel):
    """A ``[servers.<id>]`` table (API §5.2)."""

    model_config = ConfigDict(extra="forbid")

    transport: Annotated[str, Field(pattern="^(stdio|http)$")]
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}
    oauth: OAuthSettings | None = None
    enabled: bool = True
    timeout_s: float | None = None
    roots: list[str] = []
    log_level: LogLevel | None = None
    review_sampling_responses: bool = False
    mode: Mode = "auto"


_STDIO_ONLY = ("command", "args", "env", "cwd")
_HTTP_ONLY = ("url", "headers", "oauth")
_OPTIONS = ("enabled", "timeout_s", "roots", "log_level", "review_sampling_responses", "mode")


class _HostTable(BaseModel):
    """``[host]`` (API §5.2)."""

    model_config = ConfigDict(extra="forbid")

    data_dir: str | None = None
    consent_timeout_s: Annotated[float, Field(gt=0)] = 120.0
    default_tool_timeout_s: Annotated[float, Field(gt=0)] = 60.0
    reset_timeout_on_progress: bool = False
    secret_arg_pattern: str = DEFAULT_SECRET_ARG_PATTERN
    legacy_sse_fallback: bool = False
    log_buffer_size: Annotated[int, Field(ge=1)] = 1000
    oauth_client_metadata_url: AnyHttpUrl = AnyHttpUrl(DEFAULT_OAUTH_CLIENT_METADATA_URL)


class _UserFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: StrictInt
    host: _HostTable = _HostTable()
    sources: SourcesConfig = SourcesConfig()
    sampling: SamplingConfig = SamplingConfig()
    servers: dict[str, dict[str, Any]] = {}
    overrides: dict[str, dict[str, Any]] = {}


def _raw_entry(table: Mapping[str, Any]) -> RawEntry | str:
    """Validate a server table into a ``RawEntry``, or return an error description."""
    try:
        entry = _ServerTable.model_validate(table)
    except ValidationError as exc:
        return describe_validation_error(exc)
    present = entry.model_fields_set
    if entry.transport == "stdio":
        wrong = [k for k in _HTTP_ONLY if k in present]
        if entry.command is None:
            return "transport 'stdio' requires 'command'"
    else:
        wrong = [k for k in _STDIO_ONLY if k in present]
        if entry.url is None:
            return "transport 'http' requires 'url'"
    if wrong:
        return f"keys {wrong} do not apply to transport {entry.transport!r}"
    return RawEntry(
        kind="stdio" if entry.transport == "stdio" else "http",
        command=entry.command,
        args=entry.args,
        env=entry.env,
        cwd=entry.cwd,
        url=entry.url,
        headers=entry.headers,
        oauth=entry.oauth,
        options={k: getattr(entry, k) for k in _OPTIONS if k in present},
    )


def _server(
    server_id: str,
    table: object,
    origin: ConfigOrigin,
    *,
    env: Mapping[str, str],
) -> tuple[ServerDefinition | None, list[ConfigProblem]]:
    """One ``[servers.<id>]`` table → definition and problems (``invalid_definition`` on error)."""
    raw = _raw_entry(table) if isinstance(table, Mapping) else "must be a table"
    if isinstance(raw, str):
        return None, [
            problem(
                origin,
                ConfigProblemCode.INVALID_DEFINITION,
                f"{where(origin)}: server {server_id!r}: {raw}",
                server_id,
            )
        ]
    return build_definition(server_id, raw, origin, env=env, cwd=None)


# ------------------------------------------------------------------ project file


def parse_project(
    text: str, origin: ConfigOrigin, *, env: Mapping[str, str], cwd: Path
) -> ParseOutcome:
    """Parse ``<cwd>/mcphost.toml``: ``schema_version`` + ``[servers.*]`` only. Never raises."""
    del cwd
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:  # message has positions only, never content
        return _project_error(origin, f"invalid TOML: {exc}")
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        return _project_error(
            origin, f"missing or unsupported schema_version (expected {SCHEMA_VERSION})"
        )
    problems = [
        problem(
            origin,
            ConfigProblemCode.PROJECT_SETTING_IGNORED,
            f"{where(origin)}: project-level key {key!r} is ignored "
            "(only schema_version and [servers.*] are read)",
        )
        for key in data
        if key not in PROJECT_KEYS
    ]
    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        return _project_error(origin, "'servers' must be a table")
    definitions: list[ServerDefinition] = []
    for server_id, table in servers.items():
        bad_id = check_server_id(server_id, origin)
        if bad_id is not None:
            problems.append(bad_id)
            continue
        definition, found = _server(server_id, table, origin, env=env)
        problems += found
        if definition is not None:
            definitions.append(definition)
    return ParseOutcome(tuple(definitions), tuple(problems))


def _project_error(origin: ConfigOrigin, detail: str) -> ParseOutcome:
    return ParseOutcome(
        problems=(problem(origin, ConfigProblemCode.PARSE_ERROR, f"{where(origin)}: {detail}"),)
    )


# ------------------------------------------------------------------ user file


def user_origin(path: Path | None) -> ConfigOrigin:
    return ConfigOrigin(kind=SourceKind.MCPHOST_USER, path=path, precedence=USER_PRECEDENCE)


def parse_user(text: str, path: Path, *, cwd: Path, env: Mapping[str, str]) -> HostConfig:
    """Parse the user-level file strictly; raise ``ConfigError`` on any invalid content."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None
    try:
        parsed = _UserFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {describe_validation_error(exc)}") from None
    if parsed.schema_version != SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: unsupported schema_version {parsed.schema_version} "
            f"(expected {SCHEMA_VERSION})"
        )
    origin = user_origin(path)
    problems: list[ConfigProblem] = []
    servers: dict[str, ServerDefinition] = {}
    for server_id, table in parsed.servers.items():
        bad_id = check_server_id(server_id, origin)
        if bad_id is not None:
            problems.append(bad_id)
            continue
        definition, found = _server(server_id, table, origin, env=env)
        if definition is None:
            raise ConfigError(found[0].message)
        servers[server_id] = definition
        problems += found
    overrides: dict[str, ServerOverride] = {}
    for server_id, table in parsed.overrides.items():
        bad_id = check_server_id(server_id, origin)
        if bad_id is not None:
            problems.append(bad_id)
            continue
        try:
            overrides[server_id] = ServerOverride.model_validate(table)
        except ValidationError as exc:
            prefix = f"overrides.{server_id}"
            raise ConfigError(f"{path}: {describe_validation_error(exc, prefix=prefix)}") from None
    host = parsed.host.model_dump(exclude={"data_dir"})
    if parsed.host.data_dir is not None:
        host["data_dir"] = Path(parsed.host.data_dir).expanduser()
    return HostConfig(
        **host,
        sources=parsed.sources,
        sampling=parsed.sampling,
        servers=servers,
        overrides=overrides,
        config_path=path,
        cwd=cwd,
        problems=tuple(problems),
    )


def load_user(
    path: Path | None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    missing_ok: bool = False,
) -> HostConfig:
    """Read and parse the user-level file (blocking; call through a thread in async code).

    ``path=None`` → the default location, where a missing file means defaults. An explicit
    missing ``path`` raises ``ConfigError`` unless ``missing_ok``.
    """
    if path is None:
        path, missing_ok = default_user_file(), True
    cwd = cwd if cwd is not None else Path.cwd()
    env = env if env is not None else dict(os.environ)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if not missing_ok:
            raise ConfigError(f"{path}: config file not found") from None
        return HostConfig(config_path=path, cwd=cwd)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file: {exc.strerror}") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8 ({exc.reason})") from None
    return parse_user(text, path, cwd=cwd, env=env)


# ------------------------------------------------------------------ writing (DD-013)

_EXPANDABLE_SCALARS = ("command", "cwd", "url")
_EXPANDABLE_MAPS = ("env", "headers")
DocumentEdit = Callable[[TOMLDocument], None]


def definition_to_table(definition: ServerDefinition) -> dict[str, Any]:
    """Plain-data ``[servers.<id>]`` table for an own definition (API §5.2).

    Raises ``ConfigError`` for definitions the own format cannot express.
    """
    try:
        parse_server_id(definition.id)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    t = definition.transport
    out: dict[str, Any] = {}
    if isinstance(t, StdioTransport):
        out["transport"] = "stdio"
        out["command"] = t.command
        if t.args:
            out["args"] = list(t.args)
        if t.env:
            out["env"] = {k: v.get_secret_value() for k, v in t.env.items()}
        if t.cwd is not None:
            out["cwd"] = str(t.cwd)
    else:
        if t.legacy_sse:
            raise ConfigError(
                f"server {definition.id!r}: legacy HTTP+SSE is not supported in mcphost.toml"
            )
        out["transport"] = "http"
        out["url"] = str(t.url)
        if t.headers:
            out["headers"] = {k: v.get_secret_value() for k, v in t.headers.items()}
        if t.oauth is not None:
            oauth: dict[str, Any] = {}
            if t.oauth.client_id is not None:
                oauth["client_id"] = t.oauth.client_id
            if t.oauth.client_secret is not None:
                oauth["client_secret"] = t.oauth.client_secret.get_secret_value()
            if t.oauth.scopes:
                oauth["scopes"] = list(t.oauth.scopes)
            out["oauth"] = oauth
    if not definition.enabled:
        out["enabled"] = False
    if definition.timeout_s is not None:
        out["timeout_s"] = definition.timeout_s
    if definition.roots:
        out["roots"] = list(definition.roots)
    if definition.log_level is not None:
        out["log_level"] = definition.log_level
    if definition.review_sampling_responses:
        out["review_sampling_responses"] = True
    if definition.mode != "auto":
        out["mode"] = definition.mode
    return out


def _plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def _expands_to(template: Any, value: str, env: Mapping[str, str]) -> bool:
    if not isinstance(template, str):
        return False
    try:
        return expand(str(template), env) == value
    except VariableError:
        return str(template) == value


def _item(value: Any) -> Any:
    """tomlkit item for a new value: dicts become inline tables, ``oauth`` a sub-table."""
    if isinstance(value, dict):
        inline = tomlkit.inline_table()
        inline.update(value)
        return inline
    return value


def _keep_templates(key: str, old: Any, new: Any, env: Mapping[str, str]) -> Any:
    """``new`` with every value whose old ``${VAR}`` template still expands to it replaced by
    the template, so an update never writes an expanded secret back (Art. 5)."""
    if key in _EXPANDABLE_SCALARS:
        return str(old) if _expands_to(old, new, env) else new
    if key == "args" and isinstance(old, list) and len(old) == len(new):
        return [str(o) if _expands_to(o, n, env) else n for o, n in zip(old, new, strict=True)]
    if key in _EXPANDABLE_MAPS and isinstance(old, Mapping):
        return {k: str(old[k]) if _expands_to(old.get(k), v, env) else v for k, v in new.items()}
    return new


def merge_server_table(
    existing: MutableMapping[str, Any], new: Mapping[str, Any], env: Mapping[str, str]
) -> None:
    """Update ``existing`` (a tomlkit table) in place: unchanged items, comments and order
    are kept; templates that still expand to the new value are kept."""
    for key in [k for k in existing if k not in new]:
        del existing[key]
    for key, value in new.items():
        old = _plain(existing.get(key))
        wanted = _keep_templates(key, old, value, env) if key in existing else value
        if old != wanted:
            if key == "oauth":
                sub = tomlkit.table()
                sub.update(wanted)
                existing[key] = sub
            else:
                existing[key] = _item(wanted)


def new_server_table(new: Mapping[str, Any]) -> Table:
    table = tomlkit.table()
    for key, value in new.items():
        if key == "oauth":
            sub = tomlkit.table()
            sub.update(value)
            table[key] = sub
        else:
            table[key] = _item(value)
    return table


def super_table(doc: TOMLDocument, name: str) -> MutableMapping[str, Any]:
    """``doc[name]`` as a dotted-header parent table (``[servers.x]``, not ``[servers]``)."""
    if name not in doc:
        doc[name] = tomlkit.table(is_super_table=True)
    table = doc[name]
    if not isinstance(table, MutableMapping):
        raise ConfigError(f"{name!r} must be a table")
    return table


def _read_document(path: Path) -> TOMLDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        doc = tomlkit.document()
        doc["schema_version"] = SCHEMA_VERSION
        return doc
    try:
        return tomlkit.parse(text)
    except tomlkit.exceptions.ParseError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8: temp file in the same directory → fsync → ``os.replace``.

    A new file gets mode 0600; an existing file keeps its mode. On any failure the old
    file is left untouched and the temp file removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        tmp.chmod(mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def edit_user_file(path: Path, edit: DocumentEdit, *, cwd: Path, env: Mapping[str, str]) -> None:
    """Apply ``edit`` to the user-level file, validate the result strictly, write atomically.

    Blocking: call through ``anyio.to_thread``. Nothing is written if ``edit`` raises or the
    resulting file would be invalid (``ConfigError``).
    """
    doc = _read_document(path)
    edit(doc)
    text = tomlkit.dumps(doc)
    parse_user(text, path, cwd=cwd, env=env)
    atomic_write(path, text)
