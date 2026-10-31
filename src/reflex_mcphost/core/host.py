"""``Host``: the composition root and lifecycle facade (API §4.1, Tech Design Flow A).

``__init__`` performs no I/O. ``start()`` runs Flow A: load the configuration
(``HostConfig.aload()`` when none was given), open the SQLite DB, load the install HMAC key
through the secret store, load the registry, then spawn one ``ServerSupervisor`` per
enabled definition without problems in the Host task group and return without waiting for
any of them (progress is observable through ``events``). Registry diffs reconcile the
supervisors (REQ-REG-013). ``aclose()`` stops every supervisor concurrently under a 15 s
overall deadline (REQ-CON-009), shielded from outer cancellation, then closes the DB.

The Host task group is entered in ``start()`` and exited in ``aclose()``: both must run in
the same task — ``async with Host(...)`` guarantees it (anyio cancel-scope rule, DD-011).
Members of later phases (``catalog``, ``consent``, ``invoker``, ``audit``, ``toolset``) do
not exist yet.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from functools import partial
from types import TracebackType
from typing import Self

import anyio
import anyio.to_thread
from anyio.abc import TaskGroup
from keyring.backend import KeyringBackend

from reflex_mcphost.core.config.models import HostConfig, RegistryDiff, ServerDefinition
from reflex_mcphost.core.config.paths import Platform
from reflex_mcphost.core.connection.era_cache import EraCache
from reflex_mcphost.core.connection.manager import ConnectionManager
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import HostClosedError, MCPHostError
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.hmac_key import load_or_create_install_key
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.prompter import Prompter
from reflex_mcphost.core.registry import ServerRegistry
from reflex_mcphost.core.secret_store import SecretStore
from reflex_mcphost.core.trust import TrustStore

__all__ = ["SHUTDOWN_DEADLINE_S", "Host"]

SHUTDOWN_DEADLINE_S = 15.0
"""REQ-CON-009: every Client is disconnected within this overall budget."""
_log = get_logger(__name__)


class Host:
    """Owns the user session: registry, connections and events (API §4.1).

    ``platform``, ``env`` and ``keyring_backend`` replace the OS facts, the process
    environment used for ``${VAR}`` expansion and the keyring backend (tests, embedding);
    by default the current platform, ``os.environ`` and ``keyring.get_keyring()`` are used.
    """

    def __init__(
        self,
        config: HostConfig | None = None,
        *,
        prompter: Prompter | None = None,
        sampling_provider: object | None = None,
        platform: Platform | None = None,
        env: Mapping[str, str] | None = None,
        keyring_backend: KeyringBackend | None = None,
    ) -> None:
        self._config = config
        self._prompter = prompter
        self._sampling_provider = sampling_provider  # used from Phase 4 (REQ-HSH-001)
        self._platform = platform
        self._env = env
        self._keyring_backend = keyring_backend
        self.events = EventBus()
        """Host events (API §4.11)."""
        self.connections = ConnectionManager(events=self.events)
        """Per-Server connection control (API §4.5)."""
        self._registry: ServerRegistry | None = None
        self._db: Database | None = None
        self._trust: TrustStore | None = None
        self._era_cache: EraCache | None = None
        self._stack: AsyncExitStack | None = None
        self._task_group: TaskGroup | None = None
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------ members

    @property
    def registry(self) -> ServerRegistry:
        """Effective Server definitions (API §4.3); available once ``start()`` ran."""
        if self._closed:
            raise HostClosedError()
        if self._registry is None:
            raise RuntimeError("Host.start() has not run")
        return self._registry

    @property
    def config(self) -> HostConfig:
        """The configuration in use; available once ``start()`` ran (or given)."""
        if self._config is None:
            raise RuntimeError("Host.start() has not run")
        return self._config

    @property
    def database(self) -> Database:
        """The Host database (Host-internal: stores of later phases)."""
        if self._db is None:
            raise RuntimeError("Host.start() has not run")
        return self._db

    @property
    def prompter(self) -> Prompter | None:
        return self._prompter

    def set_prompter(self, prompter: Prompter | None) -> None:
        """Replace the prompter; the next trust check (each connection attempt) uses it."""
        self._prompter = prompter

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Flow A steps 1-5; returns without waiting for any Server to be ``ready``.

        Raises ``ConfigError`` for an explicitly given invalid ``mcphost.toml`` and
        ``MCPHostError`` if the database (or the install key it protects) cannot be opened.
        """
        if self._closed:
            raise HostClosedError()
        if self._started:
            raise RuntimeError("Host already started")
        self._started = True
        try:
            await self._start()
        except BaseException:
            await self._teardown()
            raise

    async def _start(self) -> None:
        env = dict(os.environ) if self._env is None else dict(self._env)
        if self._config is None:
            self._config = await HostConfig.aload(env=env)
        config = self._config
        try:
            self._db = await anyio.to_thread.run_sync(open_database, config.data_dir)
            store = await anyio.to_thread.run_sync(
                partial(
                    SecretStore, config.data_dir, self._db, keyring_backend=self._keyring_backend
                )
            )
            key = await load_or_create_install_key(store)
        except MCPHostError:
            raise
        except Exception as exc:
            raise MCPHostError(
                f"cannot open the Host database in {config.data_dir} "
                f"({type(exc).__name__}); the Host cannot run without it"
            ) from exc
        self._trust = TrustStore(self._db, key)
        self._era_cache = EraCache(self._db, key)
        registry = ServerRegistry(config, platform=self._platform, env=env, events=self.events)
        await registry.load()
        self._registry = registry
        stack = AsyncExitStack()
        self._task_group = await stack.enter_async_context(anyio.create_task_group())
        self._stack = stack
        self.connections.attach(self._task_group, self._make_supervisor)
        self.connections.populate(registry.definitions(), registry.config_errors())
        registry.on_diff.append(self._reconcile)

    async def aclose(self) -> None:
        """Disconnect every Client concurrently within 15 s, then close the DB. Idempotent;
        shielded from the caller's cancellation (REQ-CON-009)."""
        if self._closed:
            return
        await self._teardown()

    async def _teardown(self) -> None:
        # Shielded steps are plain scopes inside this task: the Host task group itself is
        # exited unshielded (it was entered outside them; anyio scopes are LIFO).
        self._closed = True
        if self._registry is not None and self._reconcile in self._registry.on_diff:
            self._registry.on_diff.remove(self._reconcile)
        with anyio.CancelScope(shield=True) as scope:
            scope.deadline = anyio.current_time() + SHUTDOWN_DEADLINE_S
            await self.connections.aclose()
        if scope.cancelled_caught:
            _log.error(
                "shutdown exceeded %.0f s; cancelling remaining supervisors", SHUTDOWN_DEADLINE_S
            )
        if self._stack is not None and self._task_group is not None:
            self._task_group.cancel_scope.cancel()
            await self._stack.aclose()
            self._stack = None
        if self._db is not None:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._db.close)

    # ------------------------------------------------------------------ wiring

    def _make_supervisor(
        self, definition: ServerDefinition, on_change: Callable[[], None]
    ) -> ServerSupervisor:
        return ServerSupervisor(
            definition,
            events=self.events,
            trust_check=self._trust_check,
            log_buffer_size=self.config.log_buffer_size,
            era_cache=self._era_cache,
            on_change=on_change,
        )

    async def _trust_check(self, definition: ServerDefinition) -> bool:
        if self._trust is None:  # pragma: no cover - supervisors exist only after start
            return False
        return await self._trust.require_trust(definition, self._prompter)

    async def _reconcile(self, diff: RegistryDiff) -> None:
        if self._closed or self._registry is None:
            return
        definitions = self._registry.definitions()
        if self._era_cache is not None and diff.removed + diff.changed:
            try:
                await self._era_cache.retain(definitions)  # DM §6
            except Exception as exc:
                _log.warning("could not prune the era cache (%s)", type(exc).__name__)
        await self.connections.reconcile(diff, definitions, self._registry.config_errors())
