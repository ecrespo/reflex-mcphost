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
``catalog`` (API §4.6) is fed by each supervisor's ``on_ready`` hook and ``message_handler``
(Flow B steps 6-7) and is the ready gate of ``connections.wait_ready`` (``wait_loaded``);
removed Servers' entries are dropped on reconcile. ``consent`` (API §4.7) loads the
persistent rules right after the DB opens and always asks the current prompter.
``audit`` (API §4.9) opens on the same DB and, before any invocation, marks records left
``in_progress`` by a previous process as interrupted (DM §2.3). ``invoker`` (API §4.8) is the
single ``tools/call`` path (Art. 3); it and the catalog reads share one
``InputRequestRouter`` (``input_router``, Host-internal). ``toolset()`` (API §4.12) builds a
fresh ``core.toolset.Toolset`` over the catalog, the connection statuses and the Invoker.

Host-side handlers (F4-05): ``start()`` builds the elicitation, sampling and roots handler
cores (``core.handlers``), fills the router's slots with their modern adapters and hands
them to every supervisor, which registers the legacy callbacks a Server's
``CapabilityPlan`` declares (DD-003): elicitation iff the prompter ``supports_elicitation``,
sampling iff a ``SamplingProvider`` is set (constructor argument, else ``[sampling]
provider`` imported in a worker thread; an import failure is logged and disables sampling,
REQ-NFR-020), roots iff the Server's effective roots are non-empty. The router gates
modern input requests on the same plan. ``set_prompter`` and roots changes recompute the
plans: modern ``ready`` Servers are rebuilt in place (no state event), legacy ones keep
their capabilities until ``reconnect()``.

OAuth (Flow E, F4-08): every HTTP Server without static headers gets one ``TokenStore`` +
``OAuthController``, built in ``_make_supervisor``, handed to its transport factory and
registered with ``connections`` (``authorize`` / ``sign_out``); ``start()`` protects the SDK
auth loggers (delta D2).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from functools import partial
from types import TracebackType
from typing import Self

import anyio
import anyio.to_thread
from anyio.abc import TaskGroup
from keyring.backend import KeyringBackend

from reflex_mcphost.core.audit import AuditLog
from reflex_mcphost.core.auth.oauth import OAuthController, protect_sdk_auth_loggers
from reflex_mcphost.core.auth.token_store import TokenStore
from reflex_mcphost.core.catalog import Catalog
from reflex_mcphost.core.config.models import (
    HostConfig,
    HttpTransport,
    RegistryDiff,
    ServerDefinition,
)
from reflex_mcphost.core.config.paths import Platform
from reflex_mcphost.core.connection.era_cache import EraCache
from reflex_mcphost.core.connection.manager import ConnectionManager
from reflex_mcphost.core.connection.stderr import LineSink
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.connection.transports import ObservedTransport, build_transport
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import HostClosedError, MCPHostError
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.handlers.capabilities import CapabilityPlan, HandlerSet
from reflex_mcphost.core.handlers.elicitation import ElicitationHandler
from reflex_mcphost.core.handlers.roots import RootsHandler, is_file_root
from reflex_mcphost.core.handlers.sampling import (
    SamplingHandler,
    SamplingProvider,
    SamplingSettings,
)
from reflex_mcphost.core.hmac_key import load_or_create_install_key
from reflex_mcphost.core.invoker import Invoker
from reflex_mcphost.core.logging import get_logger, protect_sdk_loggers
from reflex_mcphost.core.mrtr import ELICITATION, ROOTS, SAMPLING, InputRequestRouter
from reflex_mcphost.core.prompter import Prompter
from reflex_mcphost.core.registry import ServerRegistry
from reflex_mcphost.core.secret_store import SecretStore
from reflex_mcphost.core.toolset import Toolset
from reflex_mcphost.core.trust import TrustStore
from reflex_mcphost.core.types import Consumer

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
        sampling_provider: SamplingProvider | None = None,
        platform: Platform | None = None,
        env: Mapping[str, str] | None = None,
        keyring_backend: KeyringBackend | None = None,
    ) -> None:
        self._config = config
        self._prompter = prompter
        self._sampling_provider = sampling_provider  # REQ-HSH-003; [sampling] in start()
        self._handlers = HandlerSet()
        self._platform = platform
        self._env = env
        self._keyring_backend = keyring_backend
        self.events = EventBus()
        """Host events (API §4.11)."""
        self.connections = ConnectionManager(
            events=self.events,
            ready_gate=lambda server_id, client: self.catalog.wait_loaded(server_id, client),
        )
        """Per-Server connection control (API §4.5)."""
        self.connections.roots_writer = self._write_roots
        self.input_router = InputRequestRouter(gate=self._declared_methods)
        """Host-internal: input-request handlers shared by invocations and reads (F3-10),
        gated per Server on its capability plan (F4-05)."""
        self.catalog = Catalog(
            events=self.events, supervisor_of=self.connections.supervisor, router=self.input_router
        )
        """Tools, resources, templates and prompts of every Server (API §4.6)."""
        self._consent = ConsentPolicy(
            events=self.events, prompter=lambda: self._prompter, tool_lookup=self.catalog.get_tool
        )
        self._registry: ServerRegistry | None = None
        self._audit: AuditLog | None = None
        self._invoker: Invoker | None = None
        self._db: Database | None = None
        self._trust: TrustStore | None = None
        self._era_cache: EraCache | None = None
        self._secrets: SecretStore | None = None
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
    def consent(self) -> ConsentPolicy:
        """The consent gate (API §4.7); available once ``start()`` ran."""
        if self._closed:
            raise HostClosedError()
        if self._consent.store is None:
            raise RuntimeError("Host.start() has not run")
        return self._consent

    @property
    def audit(self) -> AuditLog:
        """The audit trail (API §4.9); available once ``start()`` ran."""
        if self._closed:
            raise HostClosedError()
        if self._audit is None:
            raise RuntimeError("Host.start() has not run")
        return self._audit

    @property
    def invoker(self) -> Invoker:
        """The single ``tools/call`` path (API §4.8); available once ``start()`` ran."""
        if self._closed:
            raise HostClosedError()
        if self._invoker is None:
            raise RuntimeError("Host.start() has not run")
        return self._invoker

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

    @property
    def sampling_provider(self) -> SamplingProvider | None:
        """The sampling provider in use (``None``: sampling not declared, REQ-HSH-006)."""
        return self._sampling_provider

    def set_prompter(self, prompter: Prompter | None) -> None:
        """Replace the prompter; the next trust check (each connection attempt) and the next
        consent prompt use it. The capability plans are recomputed: a modern ``ready``
        Server is rebuilt in place so its next request declares (or no longer declares)
        elicitation; legacy Servers change only after ``reconnect()`` (API §4.1, DD-003)."""
        self._prompter = prompter
        self._replan()

    def toolset(
        self,
        consumer: Consumer,
        *,
        include: Sequence[str] = ("*",),
        exclude: Sequence[str] = (),
    ) -> Toolset:
        """A new agent bridge for ``consumer`` (API §4.1, §4.12; REQ-TLS-001/003).

        ``include`` / ``exclude`` are FQN globs (with ``::``) or server-id globs; exclude
        wins. Any Consumer kind is accepted; consent is evaluated for it. One Toolset per
        call (no caching); ``RuntimeError`` before ``start()``, ``HostClosedError`` after
        ``aclose()``.
        """
        self.invoker  # noqa: B018 - lifecycle guard
        return Toolset(
            catalog=self.catalog,
            status_of=self.connections.status,
            invoker_of=lambda: self.invoker,
            consumer=consumer,
            include=include,
            exclude=exclude,
        )

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
        protect_sdk_auth_loggers()  # delta D2: SDK auth logs carry no secret text
        protect_sdk_loggers()  # F4-09b: SDK transport logs carry no message payloads
        try:
            self._db = await anyio.to_thread.run_sync(open_database, config.data_dir)
            store = await anyio.to_thread.run_sync(
                partial(
                    SecretStore, config.data_dir, self._db, keyring_backend=self._keyring_backend
                )
            )
            self._secrets = store
            key = await load_or_create_install_key(store)
            audit = AuditLog(self._db, key)
            await audit.mark_interrupted()
            self._audit = audit
            await self._consent.attach(
                ConsentStore(self._db),
                timeout_s=config.consent_timeout_s,
                secret_arg_pattern=config.secret_arg_pattern,
            )
            self._install_handlers(config, audit)
        except MCPHostError:
            raise
        except Exception as exc:
            raise MCPHostError(
                f"cannot open the Host database in {config.data_dir} "
                f"({type(exc).__name__}); the Host cannot run without it"
            ) from exc
        self._trust = TrustStore(self._db, key)
        self._era_cache = EraCache(self._db, key)
        if self._sampling_provider is None and config.sampling.provider:
            self._sampling_provider = await _load_provider(config.sampling.provider)
        registry = ServerRegistry(config, platform=self._platform, env=env, events=self.events)
        await registry.load()
        self._registry = registry
        stack = AsyncExitStack()
        self._task_group = await stack.enter_async_context(anyio.create_task_group())
        self._stack = stack
        self._invoker = Invoker(
            catalog=self.catalog,
            supervisor_of=self.connections.supervisor,
            consent=self._consent,
            audit=audit,
            events=self.events,
            router=self.input_router,
            task_group=self._task_group,
            default_timeout_s=config.default_tool_timeout_s,
            reset_timeout_on_progress=config.reset_timeout_on_progress,
        )
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
        if self._invoker is not None:
            self._invoker.close()
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
        # F4-08 (batch N): OAuth controller per HTTP Server without static headers
        oauth = self._oauth_for(definition)
        self.connections.register_oauth(definition.id, oauth)

        def transport_factory(d: ServerDefinition, on_stderr: LineSink) -> ObservedTransport:
            return build_transport(d, on_stderr=on_stderr, oauth=oauth)

        return ServerSupervisor(
            definition,
            events=self.events,
            transport_factory=transport_factory,
            trust_check=self._trust_check,
            log_buffer_size=self.config.log_buffer_size,
            era_cache=self._era_cache,
            on_change=on_change,
            on_ready=[self.catalog.on_ready],
            message_handler=self.catalog.message_handler(definition.id),
            handlers=self._handlers,
            plan_of=partial(self._plan_of, definition.id),
        )

    # ------------------------------------------------------------------ handlers (F4-05)

    def _install_handlers(self, config: HostConfig, audit: AuditLog) -> None:
        elicitation = ElicitationHandler(
            lambda: self._prompter, self._server_name, timeout_s=config.consent_timeout_s
        )
        sampling = SamplingHandler(
            consent=self._consent,
            audit=audit,
            provider_of=lambda: self._sampling_provider,
            prompter_of=lambda: self._prompter,
            settings_of=self._sampling_settings,
            server_name_of=self._server_name,
        )
        roots = RootsHandler(self._roots_of)
        self.input_router.set_handler(ELICITATION, elicitation.as_input_handler())
        self.input_router.set_handler(SAMPLING, sampling.as_input_handler())
        self.input_router.set_handler(ROOTS, roots.as_input_handler())
        self._handlers = HandlerSet(elicitation=elicitation, sampling=sampling, roots=roots)

    def _plan_of(self, server_id: str) -> CapabilityPlan:
        """What the Host declares to ``server_id`` now (DD-003; log level added by the
        supervisor)."""
        prompter = self._prompter
        return CapabilityPlan(
            elicitation=prompter is not None
            and getattr(prompter, "supports_elicitation", False) is True,
            sampling=self._sampling_provider is not None,
            roots=any(is_file_root(uri) for uri in self._roots_of(server_id)),
        )

    def _declared_methods(self, server_id: str) -> frozenset[str]:
        return self._plan_of(server_id).methods

    def _replan(self) -> None:
        """Rebuild every modern ``ready`` Server whose plan changed (DD-003)."""
        if self._closed or not self._started:
            return
        for status in self.connections.statuses():
            try:
                supervisor = self.connections.supervisor(status.server_id)
            except MCPHostError:
                continue
            supervisor.request_rebuild()

    def _roots_of(self, server_id: str) -> tuple[str, ...]:
        if self._registry is None:
            return ()
        try:
            return self._registry.get(server_id).roots
        except MCPHostError:
            return ()

    async def _write_roots(self, server_id: str, roots: tuple[str, ...]) -> None:
        await self.registry.set_roots(server_id, roots)

    def _sampling_settings(self, server_id: str) -> SamplingSettings:
        config = self.config
        timeout_s, review = config.default_tool_timeout_s, False
        if self._registry is not None:
            try:
                definition = self._registry.get(server_id)
            except MCPHostError:
                pass
            else:
                timeout_s = definition.timeout_s or timeout_s
                review = definition.review_sampling_responses
        return SamplingSettings(
            timeout_s=timeout_s, review=review, review_timeout_s=config.consent_timeout_s
        )

    def _server_name(self, server_id: str) -> str:
        try:
            info = self.connections.status(server_id).server_info
        except MCPHostError:
            return server_id
        return (info.title or info.name or server_id) if info is not None else server_id

    def _oauth_for(self, definition: ServerDefinition) -> OAuthController | None:
        """REQ-AUTH-009: no OAuth for stdio or for HTTP Servers with static headers."""
        transport = definition.transport
        if not isinstance(transport, HttpTransport) or transport.headers:
            return None
        if self._secrets is None:  # pragma: no cover - supervisors exist only after start
            return None
        cimd_url = str(self.config.oauth_client_metadata_url)
        store = TokenStore(
            self._secrets,
            definition.id,
            resource=str(transport.url),  # RFC 8707: the entry is bound to this Server URL
            oauth=transport.oauth,
            cimd_url=cimd_url,
        )
        return OAuthController(definition, store, cimd_url=cimd_url)

    async def _forget_moved_credentials(
        self, changed: Sequence[str], definitions: Sequence[ServerDefinition]
    ) -> None:
        """RFC 8707 / DM §3: a changed definition whose URL is no longer the resource its
        stored OAuth entry is bound to gets the ``oauth:{id}`` entry deleted (the store also
        refuses foreign-resource entries on read)."""
        by_id = {d.id: d for d in definitions}
        for server_id in changed:
            controller = self.connections.oauth_controller(server_id)
            definition = by_id.get(server_id)
            if controller is None or definition is None:
                continue
            transport = definition.transport
            url = str(transport.url) if isinstance(transport, HttpTransport) else None
            if url is not None and TokenStore.canonical_resource(url) == controller.store.resource:
                continue
            try:
                await controller.store.clear()
            except Exception as exc:
                _log.warning("could not delete stored OAuth credentials (%s)", type(exc).__name__)

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
        await self._forget_moved_credentials(diff.changed, definitions)  # F4-08 (batch N)
        await self.connections.reconcile(diff, definitions, self._registry.config_errors())
        for server_id in diff.removed:
            self.catalog.forget(server_id)


async def _load_provider(spec: str) -> SamplingProvider | None:
    """``[sampling] provider = "package.module:factory"`` → ``factory()`` (API §5.2). The
    import runs in a worker thread (Art. 8); any failure is logged with its exception type
    and disables sampling — the Host keeps running (REQ-NFR-020)."""
    module_name, _, attr = spec.partition(":")
    try:
        if not module_name or not attr:
            raise ValueError("expected 'package.module:factory'")
        module = await anyio.to_thread.run_sync(importlib.import_module, module_name)
        provider: object = getattr(module, attr)()
        if not isinstance(provider, SamplingProvider):
            raise TypeError("the factory did not return a SamplingProvider")
    except Exception as exc:
        _log.error(
            "sampling disabled: provider %r could not be loaded (%s)", spec, type(exc).__name__
        )
        return None
    return provider
