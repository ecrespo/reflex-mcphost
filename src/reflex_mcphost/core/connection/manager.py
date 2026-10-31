"""``ConnectionManager``: the per-Server connection surface of the Host (API §4.5).

Holds one entry per effective definition, in registry order: a running ``ServerSupervisor``
(DD-002) for enabled definitions without problems, or a parked status for ``disabled`` /
``config_error`` ones (nothing is launched for those). The Host attaches its task group,
populates the entries at start and reconciles them on every registry diff (REQ-REG-013):
added → connect, removed → stop, changed → restart (or park). Supervisors stop
concurrently. ``wait_ready`` also awaits an optional Host-injected *ready gate* for the live
Client (the Host passes ``Catalog.wait_loaded``: the initial full refresh of that
connection), so this layer never imports the Catalog (Art. 4). ``authorize`` /
``sign_out`` (Flow E) drive the Host-registered ``OAuthController`` of an HTTP Server:
``authorize`` binds the loopback listener in a Host-task-group task that lives as long as
the flow, kicks the supervisor (``auth_required`` → ``connecting``) and returns once the
authorization URL is known; the flow resolves when the attempt leaves ``connecting``.
``set_log_level`` and ``set_roots`` arrive in Phase 4.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import get_args

import anyio
from anyio.abc import TaskGroup, TaskStatus
from mcp import Client, MCPError

from reflex_mcphost.core.auth.callback_server import LoopbackCallbackServer
from reflex_mcphost.core.auth.flow import AuthorizationFlow
from reflex_mcphost.core.auth.oauth import OAuthController
from reflex_mcphost.core.config.models import LogLevel, RegistryDiff, ServerDefinition
from reflex_mcphost.core.connection.state import ServerState, ServerStatus, can_transition
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.errors import (
    AuthorizationError,
    AuthorizationReason,
    CapabilityNotSupportedError,
    HostClosedError,
    ServerUnavailableError,
    UnknownServerError,
)
from reflex_mcphost.core.events import EventBus, ServerStateChanged
from reflex_mcphost.core.handlers.roots import require_file_roots
from reflex_mcphost.core.logging import get_logger

__all__ = ["ConnectionManager", "ReadyGate", "RootsWriter", "SupervisorFactory"]

S = ServerState
SupervisorFactory = Callable[[ServerDefinition, Callable[[], None]], ServerSupervisor]
"""Builds the supervisor of a definition; the callback must be passed as ``on_change``."""
RootsWriter = Callable[[str, tuple[str, ...]], Awaitable[None]]
"""``(server_id, roots)`` → persists the roots (the Host passes ``ServerRegistry.set_roots``)."""
_SEND_ERRORS: tuple[type[Exception], ...] = (
    ServerUnavailableError,
    MCPError,
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    ConnectionError,
)
ReadyGate = Callable[[str, Client], Awaitable[None]]
"""``(server_id, client)`` → returns once the Host has settled that connection (Flow B
step 6: the Catalog's initial full refresh); must return even if the settling failed."""

_UNAVAILABLE = frozenset({S.FAILED, S.CONFIG_ERROR, S.DISABLED, S.AUTH_REQUIRED})
FLOW_GRACE_S = 5.0
"""After the flow deadline, how long the attempt may take to report back before the flow
is resolved as ``timeout`` regardless."""
_log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _passes(gate: ReadyGate, server_id: str, client: Client, changed: anyio.Event) -> bool:
    """Run ``gate`` for ``client`` until it returns (``True``) or ``changed`` is set
    (``False``: some status changed, the caller re-reads the state)."""
    passed = False
    async with anyio.create_task_group() as tg:

        async def run() -> None:
            nonlocal passed
            await gate(server_id, client)
            passed = True
            tg.cancel_scope.cancel()

        tg.start_soon(run)
        await changed.wait()
        tg.cancel_scope.cancel()
    return passed


class ConnectionManager:
    """Connections of every registered Server (API §4.5). Not thread-safe (API §3.1)."""

    def __init__(
        self,
        *,
        events: EventBus,
        clock: Callable[[], datetime] = _utcnow,
        ready_gate: ReadyGate | None = None,
    ) -> None:
        self._events = events
        self._ready_gate = ready_gate
        self._clock = clock
        self._supervisors: dict[str, ServerSupervisor] = {}
        self._parked: dict[str, ServerStatus] = {}
        self._order: list[str] = []
        self._task_group: TaskGroup | None = None
        self._factory: SupervisorFactory | None = None
        self._closed = False
        self._changed = anyio.Event()
        self._oauth: dict[str, OAuthController] = {}
        self._flows: dict[str, AuthorizationFlow] = {}

    # ------------------------------------------------------------------ sync reads

    def status(self, server_id: str) -> ServerStatus:
        """Current status; ``UnknownServerError`` for an id not in the registry."""
        self._check_open()
        supervisor = self._supervisors.get(server_id)
        if supervisor is not None:
            return supervisor.status
        try:
            return self._parked[server_id]
        except KeyError:
            raise UnknownServerError(server_id) from None

    def statuses(self) -> list[ServerStatus]:
        """Every Server's status, in registry (precedence) order."""
        return [self.status(i) for i in self._order]

    def logs(self, server_id: str) -> list[LogEntry]:
        """Snapshot of the Server's log ring (stdio stderr; Server log notifications from
        Phase 4). Parked Servers have none."""
        self.status(server_id)
        supervisor = self._supervisors.get(server_id)
        return supervisor.logs.snapshot() if supervisor is not None else []

    def supervisor(self, server_id: str) -> ServerSupervisor:
        """The running supervisor of ``server_id`` (Host-internal: catalog, invoker)."""
        status = self.status(server_id)
        supervisor = self._supervisors.get(server_id)
        if supervisor is None:
            raise ServerUnavailableError(server_id, status.state)
        return supervisor

    # ------------------------------------------------------------------ commands

    async def connect(self, server_id: str) -> None:
        """Start the lifecycle; no-op if already running or parked (disabled/config_error);
        returns once the attempt is launched, not completed (REQ-CON-001)."""
        self.status(server_id)
        supervisor = self._supervisors.get(server_id)
        if supervisor is not None:
            await supervisor.connect()

    async def disconnect(self, server_id: str) -> None:
        """Graceful stop (SDK shutdown, REQ-CON-008/014); stays ``disconnected``."""
        self.status(server_id)
        supervisor = self._supervisors.get(server_id)
        if supervisor is not None:
            await supervisor.stop()

    async def reconnect(self, server_id: str) -> None:
        """``disconnect`` + ``connect``; resets backoff and the failure counter (REQ-CON-006)."""
        self.status(server_id)
        supervisor = self._supervisors.get(server_id)
        if supervisor is not None:
            await supervisor.reconnect()

    async def wait_ready(self, server_id: str, *, timeout: float) -> ServerStatus:  # noqa: ASYNC109 - API §4.5 signature
        """Resolve once ``ready`` *and* the ready gate passed for the live Client (the
        Catalog's initial full refresh, successful or not), all within ``timeout``
        seconds, else ``TimeoutError``. ``ServerUnavailableError`` if the state is or
        becomes ``failed``, ``config_error``, ``disabled`` or ``auth_required``. A
        connection that drops and returns meanwhile is gated on its new Client."""
        with anyio.fail_after(timeout):
            while True:
                changed = self._changed
                status = self.status(server_id)
                if status.state in _UNAVAILABLE:
                    raise ServerUnavailableError(server_id, status.state)
                gate = self._ready_gate
                if status.state is S.READY and gate is None:
                    return status
                client = self._live_client(server_id) if status.state is S.READY else None
                if gate is None or client is None:
                    await changed.wait()
                elif await _passes(gate, server_id, client, changed):
                    after = self.status(server_id)
                    if after.state is S.READY and self._live_client(server_id) is client:
                        return after

    def _live_client(self, server_id: str) -> Client | None:
        supervisor = self._supervisors.get(server_id)
        lease = supervisor.current_lease if supervisor is not None else None
        return lease.client if lease is not None and lease.valid else None

    async def authorize(self, server_id: str) -> AuthorizationFlow:
        """Start the OAuth flow of a Server in ``auth_required`` (REQ-AUTH-002, Flow E).

        Binds the loopback listener, starts a connection attempt in which the SDK runs the
        flow (state → ``connecting``) and returns once the authorization URL is known (or
        the attempt ended without needing one). A second call during a flow returns the
        same flow. ``AuthorizationError("not_auth_required")`` otherwise."""
        status = self.status(server_id)
        running = self._flows.get(server_id)
        if running is not None and not running.done:
            return running
        controller = self._oauth.get(server_id)
        supervisor = self._supervisors.get(server_id)
        if controller is None or supervisor is None or status.state is not S.AUTH_REQUIRED:
            raise AuthorizationError(server_id, "not_auth_required")
        if self._task_group is None:  # pragma: no cover - supervisors exist only attached
            raise RuntimeError("ConnectionManager is not attached to a Host task group")
        flow = controller.new_flow()
        self._flows[server_id] = flow
        await self._task_group.start(self._run_flow, server_id, controller, flow)
        await supervisor.reconnect()  # auth_required → connecting; the SDK runs the flow
        await flow.settled.wait()
        return flow

    async def _run_flow(
        self,
        server_id: str,
        controller: OAuthController,
        flow: AuthorizationFlow,
        *,
        task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """Owns the listener for the flow's lifetime and resolves the flow (Flow E 2-5)."""
        started = False
        outcome: tuple[AuthorizationReason | None] = ("timeout",)
        try:
            port = await controller.preferred_port()
            async with LoopbackCallbackServer(port=port) as listener:
                controller.arm(flow, listener)
                task_status.started()
                started = True
                limit = flow.deadline + FLOW_GRACE_S - anyio.current_time()
                with anyio.move_on_after(max(0.0, limit)):
                    outcome = (await self._watch_flow(server_id, flow),)
            controller.disarm(flow)
            flow.finish(outcome[0])  # resolved only once the listener is closed
        except Exception as exc:
            _log.warning("authorization flow error (%s)", type(exc).__name__)
            flow.finish("flow_error")
            if not started:
                task_status.started()
        finally:
            controller.disarm(flow)
            flow.finish("cancelled")  # no-op unless the Host is shutting down

    async def _watch_flow(
        self, server_id: str, flow: AuthorizationFlow
    ) -> AuthorizationReason | None:
        """How ``flow`` ends, once its attempt left ``connecting``: ``ready`` → success
        (``None``), any other state → the recorded failure (``cancelled`` for a stop)."""
        attempted = False
        while True:
            changed = self._changed
            try:
                state = self.status(server_id).state
            except (UnknownServerError, HostClosedError):
                return "cancelled"
            if state is S.CONNECTING:
                attempted = True
            elif attempted or state is not S.AUTH_REQUIRED:
                if state is S.READY:
                    return None
                if state is S.DISCONNECTED and flow.failure is None:
                    return "cancelled"
                return flow.failure or "flow_error"
            await changed.wait()

    async def sign_out(self, server_id: str) -> None:
        """Cancel a running flow, disconnect, delete stored tokens and client credentials
        (REQ-AUTH-007). Idempotent; ``UnknownServerError`` for unknown ids."""
        self.status(server_id)
        flow = self._flows.pop(server_id, None)
        if flow is not None and not flow.done:
            await flow.cancel()
        supervisor = self._supervisors.get(server_id)
        if supervisor is not None:
            await supervisor.stop()
        controller = self._oauth.get(server_id)
        if controller is not None:
            await controller.sign_out()

    def register_oauth(self, server_id: str, controller: OAuthController | None) -> None:
        """Host-internal (``_make_supervisor``): the Server's OAuth controller, if any."""
        if controller is None:
            self._oauth.pop(server_id, None)
        else:
            self._oauth[server_id] = controller

    def oauth_controller(self, server_id: str) -> OAuthController | None:
        """Host-internal: the OAuth controller of ``server_id`` (``None``: no OAuth)."""
        return self._oauth.get(server_id)

    async def set_log_level(self, server_id: str, level: LogLevel) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-031). Legacy: sends
        ``logging/setLevel``. Modern: stores the level (memory) and rebuilds the Client so
        every later request carries ``io.modelcontextprotocol/logLevel`` in its ``_meta``.
        ``ValueError`` for an unknown level, ``ServerUnavailableError`` unless ``ready``,
        ``CapabilityNotSupportedError`` unless the Server declared ``logging``."""
        if level not in get_args(LogLevel):
            raise ValueError(f"unknown log level {level!r}")
        supervisor = self.supervisor(server_id)
        status = supervisor.status
        if status.state is not S.READY:
            raise ServerUnavailableError(server_id, status.state)
        if status.capabilities is None or status.capabilities.logging is None:
            raise CapabilityNotSupportedError(server_id, "logging")
        await supervisor.set_log_level(level)

    roots_writer: RootsWriter | None = None
    """Host-injected: persists a Server's roots (``ServerRegistry.set_roots``)."""

    async def set_roots(self, server_id: str, roots: Sequence[str]) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-021/022). Persists the roots
        (own definition or override; no reconnect). A ``ready`` legacy Server whose Client
        declared roots gets ``notifications/roots/list_changed``; a modern one reads them
        on its next roots request (its Client is rebuilt if the roots went between empty
        and non-empty). ``ValueError`` for any root that is not an absolute ``file://``
        URI; ``UnknownServerError`` for an unknown id."""
        values = require_file_roots(roots)
        self.status(server_id)
        writer = self.roots_writer
        if writer is None:
            raise RuntimeError("ConnectionManager is not attached to a Host")
        await writer(server_id, values)
        supervisor = self._supervisors.get(server_id)
        if supervisor is None:
            return
        try:
            await supervisor.roots_changed()
        except _SEND_ERRORS as exc:
            _log.warning(
                "roots of %r saved; list_changed not delivered (%s)", server_id, type(exc).__name__
            )

    # ------------------------------------------------------------------ Host-internal

    def attach(self, task_group: TaskGroup, factory: SupervisorFactory) -> None:
        """Called once by ``Host.start``: where supervisors run and how they are built."""
        self._task_group = task_group
        self._factory = factory

    def populate(
        self, definitions: Iterable[ServerDefinition], config_errors: frozenset[str]
    ) -> None:
        """Flow A step 4: one entry per definition; launches the runnable ones."""
        order: list[str] = []
        for definition in definitions:
            self._place(definition, config_errors, announce=False)
            order.append(definition.id)
        self._order = order
        self._notify()

    async def reconcile(
        self,
        diff: RegistryDiff,
        definitions: Sequence[ServerDefinition],
        config_errors: frozenset[str],
    ) -> None:
        """Apply a registry diff (REQ-REG-013): stop removed, restart/park changed, start
        added; unchanged Servers are not touched."""
        if self._closed:
            return
        by_id = {d.id: d for d in definitions}
        async with anyio.create_task_group() as tg:
            for server_id in (*diff.removed, *diff.changed):
                supervisor = self._supervisors.pop(server_id, None)
                if supervisor is not None:
                    tg.start_soon(supervisor.aclose)
        for server_id in diff.removed:
            self._parked.pop(server_id, None)
            self._oauth.pop(server_id, None)
        for server_id in (*diff.changed, *diff.added):
            if server_id in by_id:
                self._place(by_id[server_id], config_errors, announce=True)
        self._order = [d.id for d in definitions]
        self._notify()

    async def aclose(self) -> None:
        """Stop every supervisor concurrently (REQ-CON-009); later calls raise
        ``HostClosedError``. The Host bounds this with its 15 s deadline."""
        supervisors = list(self._supervisors.values())
        async with anyio.create_task_group() as tg:
            for supervisor in supervisors:
                tg.start_soon(supervisor.aclose)
        self._closed = True
        self._notify()

    # ------------------------------------------------------------------ helpers

    def _check_open(self) -> None:
        if self._closed:
            raise HostClosedError()

    def _notify(self) -> None:
        self._changed.set()
        self._changed = anyio.Event()

    def _place(
        self, definition: ServerDefinition, config_errors: frozenset[str], *, announce: bool
    ) -> None:
        """Create the entry of ``definition``; ``announce`` publishes the parked-state edge
        (``disconnected`` ↔ ``disabled`` / ``config_error``) a reload causes."""
        server_id = definition.id
        previous = self._parked.pop(server_id, None)
        old = previous.state if previous is not None else S.DISCONNECTED
        parked = (
            S.DISABLED
            if not definition.enabled
            else S.CONFIG_ERROR
            if server_id in config_errors
            else None
        )
        if parked is not None:
            self._parked[server_id] = ServerStatus(
                server_id=server_id, state=parked, since=self._clock()
            )
            if announce and old is not parked:
                self._publish(server_id, old, parked)
            return
        if announce and old is not S.DISCONNECTED:
            self._publish(server_id, old, S.DISCONNECTED)
        if self._task_group is None or self._factory is None:
            raise RuntimeError("ConnectionManager is not attached to a Host task group")
        supervisor = self._factory(definition, self._notify)
        self._supervisors[server_id] = supervisor
        supervisor.start(self._task_group)

    def _publish(self, server_id: str, old: ServerState, new: ServerState) -> None:
        if not can_transition(old, new):  # pragma: no cover - guarded by the callers
            _log.error("invalid parked transition %s -> %s", old, new)
            return
        self._events.publish(ServerStateChanged(server_id=server_id, old=old, new=new))
