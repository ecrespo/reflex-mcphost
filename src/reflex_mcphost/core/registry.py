"""Server registry: merge every config source into effective definitions (API §4.3).

Sources follow the PRD RF-REG total order; the first source defining an id wins in full (no
field merge) and later ones are reported as shadowed (REQ-REG-003). ``[overrides.<id>]``
apply after the merge (REQ-REG-010). Third-party files are only ever read (Art. 6); all file
I/O runs in worker threads (Art. 8). Mutations write the user-level ``mcphost.toml`` only
(REQ-REG-009) and then run the same reconcile path as ``reload()``.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import anyio
import tomlkit
from tomlkit import TOMLDocument

from reflex_mcphost.core.config import claude, cursor, mcp_json, own, vscode
from reflex_mcphost.core.config.common import problem
from reflex_mcphost.core.config.models import (
    VARIABLE_PROBLEMS,
    ConfigOrigin,
    ConfigProblem,
    ConfigProblemCode,
    HostConfig,
    ParseOutcome,
    RegistryDiff,
    ServerDefinition,
    ServerOverride,
    ShadowedDefinition,
    SourceKind,
)
from reflex_mcphost.core.config.paths import Platform, SourceSpec, discover_sources
from reflex_mcphost.core.errors import ConfigError, UnknownServerError
from reflex_mcphost.core.events import EventBus, RegistryChanged
from reflex_mcphost.core.logging import get_logger

__all__ = ["OnDiff", "RegistryDiff", "ServerRegistry"]

OnDiff = Callable[[RegistryDiff], Awaitable[None]]
"""Reconcile hook: called after every reload and mutation with the resulting diff."""

_Parser = Callable[..., ParseOutcome]
_PARSERS: Mapping[SourceKind, _Parser] = {
    SourceKind.MCPHOST_PROJECT: own.parse_project,
    SourceKind.MCP_JSON_PROJECT: mcp_json.parse,
    SourceKind.CURSOR_PROJECT: cursor.parse,
    SourceKind.VSCODE_PROJECT: vscode.parse,
    SourceKind.EXTRA: mcp_json.parse_extra,
    SourceKind.CURSOR_USER: cursor.parse,
    SourceKind.VSCODE_USER: vscode.parse,
    SourceKind.CLAUDE_DESKTOP: claude.parse,
}
_OVERRIDABLE = ("enabled", "timeout_s", "roots", "mode")
THIRD_PARTY_MESSAGE = "third-party definition; use set_enabled or overrides"

_log = get_logger(__name__)


@dataclass(frozen=True)
class _State:
    definitions: dict[str, ServerDefinition] = field(default_factory=dict)
    shadowed: tuple[ShadowedDefinition, ...] = ()
    problems: tuple[ConfigProblem, ...] = ()
    config_errors: frozenset[str] = frozenset()


def _read_files(paths: Sequence[Path]) -> dict[Path, bytes | OSError | None]:
    """Blocking reads (run in a worker thread); ``None`` = file absent. Opened ``rb`` only."""
    out: dict[Path, bytes | OSError | None] = {}
    for path in paths:
        try:
            out[path] = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            out[path] = None
        except OSError as exc:
            out[path] = exc
    return out


def _origin(spec: SourceSpec) -> ConfigOrigin:
    return ConfigOrigin(kind=spec.kind, path=spec.path, precedence=spec.precedence)


def _parse_error(origin: ConfigOrigin, detail: str) -> ParseOutcome:
    return ParseOutcome(
        problems=(problem(origin, ConfigProblemCode.PARSE_ERROR, f"{origin.path}: {detail}"),)
    )


def _apply_override(definition: ServerDefinition, override: ServerOverride) -> ServerDefinition:
    updates = {k: v for k in _OVERRIDABLE if (v := getattr(override, k)) is not None}
    return definition.model_copy(update=updates) if updates else definition


def _diff(old: _State, new: _State) -> RegistryDiff:
    before, after = old.definitions, new.definitions
    common = before.keys() & after.keys()
    changed = {
        i
        for i in common
        if before[i] != after[i] or ((i in old.config_errors) != (i in new.config_errors))
    }
    return RegistryDiff(
        added=tuple(sorted(after.keys() - before.keys())),
        removed=tuple(sorted(before.keys() - after.keys())),
        changed=tuple(sorted(changed)),
        unchanged=tuple(sorted(common - changed)),
    )


class ServerRegistry:
    """Effective Server definitions merged from every source (API §4.3).

    The constructor performs no I/O; call ``await load()`` first. ``on_diff`` holds the
    reconcile callbacks run (in order) after every ``reload()`` and mutation.
    """

    def __init__(
        self,
        config: HostConfig,
        *,
        platform: Platform | None = None,
        env: Mapping[str, str] | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._config = config
        self._platform = platform if platform is not None else Platform.current()
        self._env: Mapping[str, str] = dict(os.environ) if env is None else env
        self._events = events
        self._state = _State()
        self._lock = anyio.Lock()
        self.on_diff: list[OnDiff] = []

    # ------------------------------------------------------------------ sync reads

    @property
    def config(self) -> HostConfig:
        """The user-level configuration the registry currently uses."""
        return self._config

    def definitions(self) -> list[ServerDefinition]:
        """Effective definitions (overrides applied), in precedence order."""
        return list(self._state.definitions.values())

    def get(self, server_id: str) -> ServerDefinition:
        try:
            return self._state.definitions[server_id]
        except KeyError:
            raise UnknownServerError(server_id) from None

    def shadowed(self) -> list[ShadowedDefinition]:
        """Definitions hidden by a higher-precedence source (REQ-REG-003)."""
        return list(self._state.shadowed)

    def problems(self) -> list[ConfigProblem]:
        """Every problem found in every source, in precedence order (REQ-REG-005/006)."""
        return list(self._state.problems)

    def config_errors(self) -> frozenset[str]:
        """Ids whose effective definition has a variable problem → ``config_error``."""
        return self._state.config_errors

    # ------------------------------------------------------------------ loading

    async def load(self) -> None:
        """Read every source and build the effective view (no diff, no event)."""
        async with self._lock:
            self._state = await self._build(self._config)

    async def reload(self) -> RegistryDiff:
        """Re-read all sources (the user file too); publish ``registry.changed`` and run the
        ``on_diff`` reconcile hooks (REQ-REG-013). An invalid user file raises
        ``ConfigError`` and leaves the registry unchanged."""
        async with self._lock:
            return await self._reload_locked()

    async def _reload_locked(self) -> RegistryDiff:
        config = await anyio.to_thread.run_sync(
            partial(
                own.load_user,
                self._config.config_path,
                cwd=self._config.cwd,
                env=self._env,
                missing_ok=True,
            )
        )
        state = await self._build(config)
        diff = _diff(self._state, state)
        self._config, self._state = config, state
        if self._events is not None:
            self._events.publish(RegistryChanged(diff=diff, problems=state.problems))
        for callback in tuple(self.on_diff):
            await callback(diff)
        return diff

    async def _build(self, config: HostConfig) -> _State:
        specs = discover_sources(
            config.cwd,
            user_file=config.config_path,
            sources=config.sources,
            platform=self._platform,
        )
        to_read = [s.path for s in specs if s.kind is not SourceKind.MCPHOST_USER]
        contents = await anyio.to_thread.run_sync(_read_files, to_read)
        winners: dict[str, ServerDefinition] = {}
        shadowed: list[ShadowedDefinition] = []
        problems: list[ConfigProblem] = []
        for spec in specs:
            outcome = self._outcome(spec, config, contents.get(spec.path))
            problems += outcome.problems
            for definition in outcome.definitions:
                winner = winners.get(definition.id)
                if winner is None:
                    winners[definition.id] = definition
                else:
                    shadowed.append(
                        ShadowedDefinition(definition=definition, shadowed_by=winner.origin)
                    )
        flagged = frozenset(
            p.server_id
            for p in problems
            if p.code in VARIABLE_PROBLEMS
            and p.server_id is not None
            and p.server_id in winners
            and winners[p.server_id].origin == p.origin
        )
        effective = {
            i: _apply_override(d, config.overrides[i]) if i in config.overrides else d
            for i, d in winners.items()
        }
        for p in problems:
            _log.warning("config problem: %s", p.message, extra={"server_id": p.server_id})
        return _State(effective, tuple(shadowed), tuple(problems), frozenset(flagged))

    def _outcome(
        self, spec: SourceSpec, config: HostConfig, data: bytes | OSError | None
    ) -> ParseOutcome:
        if spec.kind is SourceKind.MCPHOST_USER:
            return ParseOutcome(tuple(config.servers.values()), config.problems)
        origin = _origin(spec)
        if data is None:
            return ParseOutcome()
        if isinstance(data, OSError):
            return _parse_error(origin, f"cannot read file: {data.strerror or type(data).__name__}")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return _parse_error(origin, f"not valid UTF-8 ({exc.reason})")
        return _PARSERS[spec.kind](text, origin, env=self._env, cwd=config.cwd)

    # ------------------------------------------------------------------ mutations

    async def add(self, definition: ServerDefinition) -> None:
        """Add an own definition to the user-level file (REQ-REG-009), then reconcile."""
        table = own.definition_to_table(definition)
        server_id = definition.id

        def edit(doc: TOMLDocument) -> None:
            servers = own.super_table(doc, "servers")
            if server_id in servers:
                raise ConfigError(
                    f"server {server_id!r} is already defined in {self._config.config_path}; "
                    "use update"
                )
            servers[server_id] = own.new_server_table(table)

        await self._mutate(edit)

    async def update(self, definition: ServerDefinition) -> None:
        """Replace an own definition in full (REQ-REG-009). Third-party ids raise
        ``ConfigError``; unknown ids raise ``UnknownServerError``."""
        table = own.definition_to_table(definition)
        server_id = definition.id

        def edit(doc: TOMLDocument) -> None:
            servers = own.super_table(doc, "servers")
            if server_id not in servers:
                raise UnknownServerError(server_id)
            own.merge_server_table(servers[server_id], table, self._env)

        await self._mutate(edit, own_id=server_id)

    async def remove(self, server_id: str) -> None:
        """Remove an own definition (REQ-REG-009); third-party ids raise ``ConfigError``."""

        def edit(doc: TOMLDocument) -> None:
            servers = own.super_table(doc, "servers")
            if server_id not in servers:
                raise UnknownServerError(server_id)
            del servers[server_id]

        await self._mutate(edit, own_id=server_id)

    async def set_enabled(self, server_id: str, enabled: bool) -> None:
        """Enable/disable a Server. Own effective definitions are edited in their table;
        any other (third-party or project) gets ``[overrides.<id>] enabled`` (REQ-REG-010)."""
        effective = self._state.definitions.get(server_id)
        if effective is None and server_id not in self._config.servers:
            raise UnknownServerError(server_id)
        in_own_table = effective is None or effective.origin.kind is SourceKind.MCPHOST_USER

        def edit(doc: TOMLDocument) -> None:
            if in_own_table:
                servers = own.super_table(doc, "servers")
                if server_id not in servers:
                    raise UnknownServerError(server_id)
                table = servers[server_id]
                if enabled:
                    table.pop("enabled", None)
                else:
                    table["enabled"] = False
            overrides = own.super_table(doc, "overrides")
            if in_own_table:
                if server_id in overrides:
                    overrides[server_id].pop("enabled", None)
                return
            if server_id not in overrides:
                overrides[server_id] = tomlkit.table()
            overrides[server_id]["enabled"] = enabled

        await self._mutate(edit)

    async def _mutate(self, edit: own.DocumentEdit, *, own_id: str | None = None) -> None:
        """Write the user-level file (worker thread) and run the reload reconcile path."""
        async with self._lock:
            if (
                own_id is not None
                and own_id not in self._config.servers
                and own_id in self._state.definitions
            ):
                raise ConfigError(THIRD_PARTY_MESSAGE)
            await anyio.to_thread.run_sync(
                partial(
                    own.edit_user_file,
                    self._config.config_path,
                    edit,
                    cwd=self._config.cwd,
                    env=self._env,
                )
            )
            await self._reload_locked()
