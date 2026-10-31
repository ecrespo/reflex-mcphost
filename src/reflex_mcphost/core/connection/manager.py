"""``ConnectionManager``: the per-Server connection surface of the Host (API §4.5).

Holds one entry per effective definition, in registry order: a running ``ServerSupervisor``
(DD-002) for enabled definitions without problems, or a parked status for ``disabled`` /
``config_error`` ones (nothing is launched for those). The Host attaches its task group,
populates the entries at start and reconciles them on every registry diff (REQ-REG-013):
added → connect, removed → stop, changed → restart (or park). Supervisors stop
concurrently. ``authorize``, ``sign_out``, ``set_log_level`` and ``set_roots`` arrive in
Phase 4.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

import anyio
from anyio.abc import TaskGroup

from reflex_mcphost.core.config.models import LogLevel, RegistryDiff, ServerDefinition
from reflex_mcphost.core.connection.state import ServerState, ServerStatus, can_transition
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.errors import (
    HostClosedError,
    ServerUnavailableError,
    UnknownServerError,
)
from reflex_mcphost.core.events import EventBus, ServerStateChanged
from reflex_mcphost.core.logging import get_logger

__all__ = ["ConnectionManager", "SupervisorFactory"]

S = ServerState
SupervisorFactory = Callable[[ServerDefinition, Callable[[], None]], ServerSupervisor]
"""Builds the supervisor of a definition; the callback must be passed as ``on_change``."""

_UNAVAILABLE = frozenset({S.FAILED, S.CONFIG_ERROR, S.DISABLED, S.AUTH_REQUIRED})
_PHASE4 = "arrives in Phase 4 (OAuth, logging and roots)"
_log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ConnectionManager:
    """Connections of every registered Server (API §4.5). Not thread-safe (API §3.1)."""

    def __init__(self, *, events: EventBus, clock: Callable[[], datetime] = _utcnow) -> None:
        self._events = events
        self._clock = clock
        self._supervisors: dict[str, ServerSupervisor] = {}
        self._parked: dict[str, ServerStatus] = {}
        self._order: list[str] = []
        self._task_group: TaskGroup | None = None
        self._factory: SupervisorFactory | None = None
        self._closed = False
        self._changed = anyio.Event()

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
        """Resolve once ``ready``. ``TimeoutError`` after ``timeout`` seconds;
        ``ServerUnavailableError`` if the state is or becomes ``failed``, ``config_error``,
        ``disabled`` or ``auth_required``."""
        with anyio.fail_after(timeout):
            while True:
                changed = self._changed
                status = self.status(server_id)
                if status.state is S.READY:
                    return status
                if status.state in _UNAVAILABLE:
                    raise ServerUnavailableError(server_id, status.state)
                await changed.wait()

    async def authorize(self, server_id: str) -> None:
        """OAuth flow for a Server in ``auth_required`` (REQ-AUTH-002)."""
        raise NotImplementedError(f"authorize {_PHASE4}")

    async def sign_out(self, server_id: str) -> None:
        """Delete stored tokens and disconnect (REQ-AUTH-007)."""
        raise NotImplementedError(f"sign_out {_PHASE4}")

    async def set_log_level(self, server_id: str, level: LogLevel) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-031)."""
        raise NotImplementedError(f"set_log_level {_PHASE4}")

    async def set_roots(self, server_id: str, roots: Sequence[str]) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-021/022)."""
        raise NotImplementedError(f"set_roots {_PHASE4}")

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
