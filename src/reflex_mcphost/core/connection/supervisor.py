"""``ServerSupervisor``: one long-lived task per Server owning its ``Client`` (DD-002, Flow B).

Per connection attempt the supervisor checks project trust (REQ-REG-011; before any process
or network activity), builds a fresh transport, enters ``async with Client(transport,
mode=...)``, records era / protocol version / serverInfo / capabilities, publishes
``ready``, then runs the ``on_ready`` hooks concurrently for as long as it stays ready (the
Catalog refresh and watchers of F2-11/F2-12 plug in here; a hook is cancelled when the
connection ends) until a command (stop / reconnect) or the transport's ``closed`` signal.
Hooks run *after* the ``ready`` transition (a deviation from Flow B step 6, recorded with
F2-11). ``message_handler`` receives the Server's notifications (legacy list changes,
F2-12); reads use the SDK auto-loop capped at 8 input rounds (DD-001). On exit it applies
the API §4.5 transition table:

=================================================  ==========================================
outcome                                            next state
=================================================  ==========================================
transport error / transport lost                   ``reconnecting`` after ``backoff_delay``;
                                                   ``failed`` at the 10th consecutive failure
unsupported protocol version                       ``failed`` (no retry)
executable not found                               ``failed`` ``command_not_found: <exe>``
project Server not trusted                         ``failed`` ``untrusted_project_server``
HTTP 401 with ``WWW-Authenticate``                 ``auth_required`` (parked; F4-07 authorizes)
legacy HTTP ``404`` on the session                 stays ``ready``: in-place rebuild
                                                   (REQ-CON-015); more than 3 rebuilds without
                                                   one successful request (or 30 s up) count
                                                   as transport lost
=================================================  ==========================================

``failed`` and ``auth_required`` park until ``reconnect()``, which also resets the failure
counter. Every transition is validated with ``can_transition`` and published as exactly one
``server.state_changed``. Failures never leave the supervisor's task (REQ-CON-011):
everything but cancellation is caught, logged without secrets, and turned into a state.
Clock, sleep and RNG are injectable for deterministic backoff tests.

Host-side handlers (F4-05, DD-003): every Client is built from the Server's current
``CapabilityPlan`` (``plan_of``): a callback only for each declared capability
(registering is declaring), ``logging_callback`` always (Server log notifications go to the
log ring) and ``log_level`` (modern: sent in every request's ``_meta``; legacy:
``logging/setLevel`` after ``initialize`` when the Server declares logging). Legacy
callbacks are attributed through ``inflight`` (REQ-HSH-045/047). When the plan of a
``ready`` *modern* Server changes (``request_rebuild``), its Client is rebuilt in place with
``mode=<negotiated version>, prior_discover=<current>``: the rebuild waits until the lease is
idle (in-flight calls are never cancelled), new ``lease()`` callers wait for the new Client,
the state stays ``ready`` and no ``server.state_changed`` is published (the REQ-CON-015
path). Legacy Servers keep their capabilities until ``reconnect()``. A stdio rebuild starts
a new Server process (a new transport is needed for a new Client).
"""

from __future__ import annotations

import random
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal, TypeVar

import anyio
import mcp_types as types
from anyio.abc import TaskGroup
from mcp import Client, MCPDeprecationWarning, MCPError
from mcp.client.session import MessageHandlerFnT
from mcp_types import UNSUPPORTED_PROTOCOL_VERSION
from mcp_types.version import LATEST_MODERN_VERSION, MODERN_PROTOCOL_VERSIONS
from pydantic import ValidationError

from reflex_mcphost.core.config.models import LogLevel, ServerDefinition
from reflex_mcphost.core.connection.era_cache import CachedEra, EraCache
from reflex_mcphost.core.connection.inflight import InFlightRegistry
from reflex_mcphost.core.connection.lease import ClientLease
from reflex_mcphost.core.connection.state import (
    MAX_FAILURES,
    ServerState,
    ServerStatus,
    backoff_delay,
    can_transition,
    era_of,
)
from reflex_mcphost.core.connection.stderr import LineSink, LogEntry, LogRing
from reflex_mcphost.core.connection.transports import (
    CommandNotFoundError,
    ObservedTransport,
    build_transport,
)
from reflex_mcphost.core.errors import ServerUnavailableError
from reflex_mcphost.core.events import EventBus, ServerLog, ServerStateChanged
from reflex_mcphost.core.handlers.capabilities import CapabilityPlan, HandlerSet
from reflex_mcphost.core.handlers.logging import logging_callback
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.trust import requires_trust

__all__ = [
    "OnReady",
    "ServerSupervisor",
    "TransportFactory",
    "TrustCheck",
]

S = ServerState
T = TypeVar("T")

TrustCheck = Callable[[ServerDefinition], Awaitable[bool]]
"""Returns ``True`` only if a project definition may be launched/contacted (fail closed)."""
TransportFactory = Callable[[ServerDefinition, LineSink], ObservedTransport]
OnReady = Callable[[Client, ServerStatus], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]
PlanLookup = Callable[[], CapabilityPlan]
"""The Host's current capability plan for this Server (without the runtime log level)."""

UNTRUSTED = "untrusted_project_server"
HOST_LOGGER = "host"
"""``LogEntry.logger`` of entries the Host writes about a Server (not the Server's own)."""
SHUTDOWN_BUDGET_S = 10.0
"""Upper bound for one Client exit; the SDK's stdio escalation needs at most ~6.5 s (S-2)."""
AUTH_REQUIRED = "auth_required"
INTERNAL_ERROR = "internal_error"
_MAX_REASON = 200
INPUT_REQUIRED_MAX_ROUNDS = 8
"""DD-001: cap on the SDK's MRTR auto-loop for catalog reads and prompt gets."""
MAX_SESSION_REBUILDS = 3
"""Consecutive legacy-session rebuilds tolerated without a successful request (REQ-CON-015)."""
SESSION_STABLE_S = 30.0
"""A Client up this long also proves its session works (resets the rebuild count)."""
SESSION_TERMINATED = "transport_error: session_terminated"
_log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _default_factory(definition: ServerDefinition, on_stderr: LineSink) -> ObservedTransport:
    return build_transport(definition, on_stderr=on_stderr)


@asynccontextmanager
async def _shielded_exit(client_cm: Client) -> AsyncIterator[Client]:
    """``async with client_cm`` whose exit is shielded from outer cancellation (REQ-CON-008/
    009): stdio escalation and the legacy HTTP ``DELETE`` always run, bounded by
    ``SHUTDOWN_BUDGET_S``. Entry stays cancellable. The scope wraps the Client (anyio scopes
    must exit in LIFO order) and turns its shield on only when the body ends."""
    with anyio.CancelScope() as scope:
        async with client_cm as client:
            # Shielded from here on: a cancellation already queued on the task when its
            # exit starts would otherwise cut the stdio escalation or the HTTP DELETE. The
            # body ends through the supervisor's `interrupt` event instead (`_race`).
            scope.shield = True
            try:
                yield client
            finally:
                scope.deadline = anyio.current_time() + SHUTDOWN_BUDGET_S


async def _race(fn: Callable[[], Awaitable[object]], *events: anyio.Event) -> bool:
    """Run ``fn`` until it returns (``True``) or any of ``events`` is set (``False``)."""
    finished = False
    async with anyio.create_task_group() as tg:

        async def until(event: anyio.Event) -> None:
            await event.wait()
            tg.cancel_scope.cancel()

        for event in events:
            tg.start_soon(until, event)
        await fn()
        finished = True
        tg.cancel_scope.cancel()
    return finished


@dataclass(frozen=True)
class _Plan:
    """How one Client is built (Flow B step 2)."""

    mode: str
    prior_discover: types.DiscoverResult | None = None
    cached: bool = False  # from a fresh era-cache entry: an era-indicating error re-detects
    discover: bool = False  # forced modern without a cached DiscoverResult: fetch it
    internal: bool = False  # modern capability rebuild: nothing to detect, check or store


class _StaleEraError(Exception):
    """A connect under a cached era failed with an era-indicating error."""


def _era_indicating(exc: BaseException, transport: ObservedTransport | None) -> bool:
    """Any JSON-RPC error except a lost connection (the SDK probe's denylist, `_probe`)."""
    exc = _leaf(exc)
    if transport is not None and (transport.closed.is_set() or transport.auth_challenge):
        return False
    return isinstance(exc, MCPError) and exc.code != types.CONNECTION_CLOSED


@dataclass(frozen=True)
class _Outcome:
    kind: Literal["lost", "transient", "fatal", "auth"]
    reason: str


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_REASON else text[: _MAX_REASON - 1] + "…"


def _unsupported_reason(exc: MCPError) -> str:
    try:
        data = types.UnsupportedProtocolVersionErrorData.model_validate(exc.error.data)
    except ValidationError:
        return "unsupported_protocol_version"
    supported = ", ".join(data.supported) or "none"
    return _clip(f"unsupported_protocol_version: requested {data.requested}, supported {supported}")


def _classify(exc: BaseException, transport: ObservedTransport | None) -> _Outcome:
    exc = _leaf(exc)
    if isinstance(exc, CommandNotFoundError):
        return _Outcome("fatal", _clip(f"command_not_found: {exc.executable}"))
    if transport is not None and transport.auth_challenge:
        return _Outcome("auth", AUTH_REQUIRED)
    if isinstance(exc, MCPError) and exc.code == UNSUPPORTED_PROTOCOL_VERSION:
        return _Outcome("fatal", _unsupported_reason(exc))
    prefix = "Unsupported protocol version from the server: "
    if isinstance(exc, RuntimeError) and str(exc).startswith(prefix):
        offered = str(exc).removeprefix(prefix)
        return _Outcome("fatal", _clip(f"unsupported_protocol_version: server offered {offered}"))
    return _Outcome("transient", f"transport_error: {type(exc).__name__}")


class ServerSupervisor:
    """Supervises the connection of one Server (DD-002). Not thread-safe (API §3.1)."""

    def __init__(
        self,
        definition: ServerDefinition,
        *,
        events: EventBus,
        trust_check: TrustCheck | None = None,
        log_buffer_size: int = 1000,
        transport_factory: TransportFactory = _default_factory,
        on_ready: Sequence[OnReady] = (),
        sleep: Sleep = anyio.sleep,
        clock: Callable[[], datetime] = _utcnow,
        rng: Callable[[], float] = random.random,
        era_cache: EraCache | None = None,
        on_change: Callable[[], None] | None = None,
        message_handler: MessageHandlerFnT | None = None,
        handlers: HandlerSet | None = None,
        plan_of: PlanLookup | None = None,
    ) -> None:
        self._definition = definition
        self._handlers = handlers or HandlerSet()
        self._plan_of = plan_of
        self.inflight = InFlightRegistry(definition.id)
        """Legacy attribution of server-initiated requests (REQ-HSH-045/047)."""
        self._log_level: LogLevel | None = None  # runtime set_log_level (memory, DM §8)
        self._client_plan: CapabilityPlan | None = None  # plan of the live Client
        self._rebuild_wanted = anyio.Event()
        self._rebuild_gate: anyio.Event | None = None  # set while new leases must wait
        self._rebuild_committed = False  # the live Client is being replaced (lease idle)
        self._message_handler = message_handler
        self._session_confirmed = False
        self._era_cache = era_cache
        self._on_change = on_change
        self._events = events
        self._trust_check = trust_check
        self._factory = transport_factory
        self._on_ready = tuple(on_ready)
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self._log = ContextAdapter(_log, server_id=definition.id)
        self._ring = LogRing(log_buffer_size, on_entry=self._publish_log, clock=clock)
        self._status = ServerStatus(server_id=definition.id, state=S.DISCONNECTED, since=clock())
        self._lease: ClientLease | None = None
        self._failures = 0
        # commands: flags + a kick event; the loop acknowledges by sequence number
        self._want_running = True
        self._reset = False
        self._closing = False
        self._kick = anyio.Event()
        self._interrupt = anyio.Event()  # per attempt; see _interruptible
        self._changed = anyio.Event()
        self._sent = 0
        self._handled = 0
        self._started = False
        self._done = anyio.Event()

    # ------------------------------------------------------------------ public surface

    @property
    def server_id(self) -> str:
        return self._definition.id

    @property
    def definition(self) -> ServerDefinition:
        return self._definition

    @property
    def status(self) -> ServerStatus:
        return self._status

    @property
    def logs(self) -> LogRing:
        """The per-Server log ring (stdio stderr; Server log notifications in Phase 4)."""
        return self._ring

    @property
    def current_lease(self) -> ClientLease | None:
        return self._lease

    def start(self, task_group: TaskGroup) -> None:
        """Spawn the supervisor task; the first attempt starts immediately."""
        if self._started:
            raise RuntimeError(f"supervisor {self.server_id!r} already started")
        self._started = True
        task_group.start_soon(self._run, name=f"supervisor:{self.server_id}")

    async def connect(self) -> None:
        """Start the lifecycle if stopped; no-op otherwise (returns once launched)."""
        if not self._want_running:
            self._want_running = True
            await self._command()

    async def stop(self) -> None:
        """Exit the Client block (SDK shutdown) and stay ``disconnected``."""
        self._want_running = False
        await self._command()

    async def reconnect(self) -> None:
        """``disconnect`` + ``connect``; resets backoff and the failure counter."""
        self._want_running = True
        self._reset = True
        await self._command()

    async def aclose(self) -> None:
        """Stop and end the supervisor task."""
        self._closing = True
        self._want_running = False
        self._kick_now()
        if self._started:
            await self._done.wait()

    async def wait_for(self, *states: ServerState) -> ServerStatus:
        """Return the status once its state is one of ``states``."""
        while self._status.state not in states:
            await self._changed.wait()
        return self._status

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Client]:
        """The live Client; ``ServerUnavailableError`` unless ``ready``. During a modern
        capability rebuild it waits for the new Client."""
        while (gate := self._rebuild_gate) is not None:
            await gate.wait()
        lease = self._lease
        if lease is None or not lease.valid:
            raise ServerUnavailableError(self.server_id, self._status.state)
        async with lease.acquire() as client:
            yield client

    # ------------------------------------------------------------------ capabilities (F4-05)

    @property
    def plan(self) -> CapabilityPlan:
        """The plan the next Client of this Server gets (DD-003)."""
        base = self._plan_of() if self._plan_of is not None else CapabilityPlan()
        return replace(base, log_level=self._log_level or self._definition.log_level)

    @property
    def client_plan(self) -> CapabilityPlan | None:
        """The plan of the live Client (``None`` when not connected)."""
        return self._client_plan if self._lease is not None else None

    @property
    def rebuilding(self) -> bool:
        """The live Client is being replaced by a modern capability rebuild (the Catalog
        keeps the entries: the state stays ``ready``)."""
        return self._rebuild_committed

    def request_rebuild(self) -> bool:
        """Rebuild the modern Client if its plan changed (DD-003); legacy Servers keep their
        capabilities until ``reconnect()``. ``True`` if a rebuild was started. Sync: new
        ``lease()`` callers wait for the new Client from now on."""
        lease = self._lease
        status = self._status
        if (
            lease is None
            or not lease.valid
            or status.state is not S.READY
            or status.era != "modern"
            or self._client_plan == self.plan
        ):
            return False
        if self._rebuild_gate is None:
            self._rebuild_gate = anyio.Event()
        self._rebuild_wanted.set()
        return True

    def _release_gate(self) -> None:
        self._rebuild_committed = False
        gate, self._rebuild_gate = self._rebuild_gate, None
        if gate is not None:
            gate.set()

    async def set_log_level(self, level: LogLevel) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-031). Store the runtime level;
        legacy → ``logging/setLevel`` on the live session; modern → rebuild so the new
        Client sends the level in every request's ``_meta``."""
        self._log_level = level
        if self._status.era == "modern":
            self.request_rebuild()
            return
        async with self.lease() as client:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", MCPDeprecationWarning)
                await client.session.set_logging_level(level)
        if self._client_plan is not None:
            self._client_plan = replace(self._client_plan, log_level=level)

    async def roots_changed(self) -> None:
        """*Deprecated (SEP-2577), compatibility* (REQ-HSH-021): a ``ready`` legacy Server
        whose Client declared roots gets ``notifications/roots/list_changed``; a modern one
        is rebuilt if its roots went between empty and non-empty (it reads them per
        request)."""
        if self._status.state is not S.READY:
            return
        if self._status.era == "modern":
            self.request_rebuild()
            return
        plan = self.client_plan
        if plan is None or not plan.roots:
            self._log.info("roots changed; this connection did not declare roots (reconnect)")
            return
        async with self.lease() as client:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", MCPDeprecationWarning)
                await client.session.send_roots_list_changed()

    # ------------------------------------------------------------------ commands

    def _kick_now(self) -> None:
        self._kick.set()
        self._kick = anyio.Event()

    def _notify(self) -> None:
        self._changed.set()
        self._changed = anyio.Event()
        if self._on_change is not None:
            self._on_change()

    async def _command(self) -> None:
        self._sent += 1
        seq = self._sent
        self._kick_now()
        if not self._started:
            return
        while self._handled < seq and not self._done.is_set():
            await self._changed.wait()  # `_done` also notifies

    # ------------------------------------------------------------------ state

    def _publish(self, old: ServerState, new: ServerState, reason: str | None) -> None:
        self._events.publish(
            ServerStateChanged(server_id=self.server_id, old=old, new=new, reason=reason)
        )

    def _transition(self, new: ServerState, reason: str | None = None, **fields: Any) -> None:
        old = self._status.state
        if not can_transition(old, new):
            raise RuntimeError(f"invalid transition {old} -> {new}")
        update = {"state": new, "reason": reason, "since": self._clock(), **fields}
        self._status = self._status.model_copy(update=update)
        self._publish(old, new, reason)
        self._notify()

    def _update(self, **fields: Any) -> None:
        self._status = self._status.model_copy(update=fields)
        self._notify()

    def _force_failed(self) -> None:
        """Last-resort state after an internal error (bypasses the table on purpose)."""
        old = self._status.state
        if old is S.FAILED and self._status.reason == INTERNAL_ERROR:
            return
        update = {"state": S.FAILED, "reason": INTERNAL_ERROR, "since": self._clock()}
        self._status = self._status.model_copy(update=update)
        if old is not S.FAILED:
            self._publish(old, S.FAILED, INTERNAL_ERROR)
        self._notify()

    def _publish_log(self, entry: LogEntry) -> None:
        self._events.publish(ServerLog(server_id=self.server_id, entry=entry))

    # ------------------------------------------------------------------ loop

    async def _run(self) -> None:
        try:
            while True:
                kick = self._kick
                try:
                    if not await self._supervise_once():
                        return
                except Exception:
                    self._log.exception("supervisor error; server marked failed")
                    self._force_failed()
                    self._acknowledge()
                    if self._closing:
                        return
                    await kick.wait()
        finally:
            if self._lease is not None:
                self._lease.invalidate()
                self._lease = None
            self._done.set()
            self._notify()

    def _acknowledge(self) -> None:
        if self._handled != self._sent:
            self._handled = self._sent
            self._notify()

    async def _supervise_once(self) -> bool:
        """One step of the state machine; ``False`` ends the task."""
        kick = self._kick
        handled = self._sent
        state = self._status.state
        if not self._want_running:
            if state is not S.DISCONNECTED:
                self._transition(S.DISCONNECTED)
            self._handled = handled
            self._notify()
            if self._closing:
                return False
            await kick.wait()
            return True
        if self._reset:
            self._reset = False
            self._failures = 0
            if state is not S.CONNECTING:
                self._transition(S.CONNECTING, consecutive_failures=0, next_retry_at=None)
        elif state in (S.FAILED, S.AUTH_REQUIRED):
            self._handled = handled
            self._notify()
            await kick.wait()
            return True
        elif state is S.DISCONNECTED:
            self._transition(S.CONNECTING)
        self._handled = handled
        self._notify()

        outcome = await self._interruptible(self._attempt, kick)
        if outcome is None:  # a command interrupted the attempt
            if self._status.state is S.READY:
                self._transition(S.DISCONNECTED)
            return True
        await self._apply(outcome, kick)
        return True

    async def _interruptible(self, fn: Callable[[], Awaitable[T]], kick: anyio.Event) -> T | None:
        """Run ``fn`` until it returns or ``kick`` is set (then ``None``).

        ``fn`` is cancelled, except inside a Client block, which is shielded: there the
        ``interrupt`` event — set on ``kick`` *and* when an outer cancellation reaches the
        watcher — makes the block end by itself so its exit runs undisturbed (F2-09)."""
        result: T | None = None
        interrupt = self._interrupt = anyio.Event()
        async with anyio.create_task_group() as tg:

            async def watch() -> None:
                try:
                    await kick.wait()
                finally:
                    interrupt.set()
                tg.cancel_scope.cancel()

            tg.start_soon(watch)
            result = await fn()
            tg.cancel_scope.cancel()
        return result if not kick.is_set() else None

    async def _apply(self, outcome: _Outcome, kick: anyio.Event) -> None:
        if outcome.kind == "fatal":
            self._log.error("connection failed permanently: %s", outcome.reason)
            self._transition(S.FAILED, outcome.reason, next_retry_at=None)
            return
        if outcome.kind == "auth":
            self._log.warning("server requires authorization")
            self._transition(S.AUTH_REQUIRED, AUTH_REQUIRED, next_retry_at=None)
            return
        self._failures += 1
        if self._failures >= MAX_FAILURES:
            self._log.error("giving up after %d consecutive failures", self._failures)
            self._transition(
                S.FAILED,
                outcome.reason,
                consecutive_failures=self._failures,
                next_retry_at=None,
            )
            return
        delay = backoff_delay(self._failures - 1, rng=self._rng)
        now = self._clock()
        fields = {
            "consecutive_failures": self._failures,
            "next_retry_at": now + timedelta(seconds=delay),
        }
        self._log.warning(
            "connection attempt %d failed (%s); retrying in %.1f s",
            self._failures,
            outcome.reason,
            delay,
        )
        if self._status.state is S.RECONNECTING:
            self._update(reason=outcome.reason, **fields)
        else:
            self._transition(S.RECONNECTING, outcome.reason, **fields)
        await self._interruptible(lambda: self._sleep(delay), kick)
        if self._status.state is S.RECONNECTING:
            self._update(next_retry_at=None)

    async def _trusted(self) -> bool:
        if not requires_trust(self._definition):
            return True
        if self._trust_check is None:
            self._log.warning("project server needs trust and no trust check is configured")
            return False
        try:
            return await self._trust_check(self._definition) is True
        except Exception as exc:
            self._log.warning("trust check raised %s; server untrusted", type(exc).__name__)
            return False

    async def _attempt(self) -> _Outcome:
        """One connection attempt; returns how it ended (never raises but cancellation)."""
        if not await self._trusted():
            return _Outcome("fatal", UNTRUSTED)
        plan = await self._plan()
        try:
            return await self._connect(plan)
        except _StaleEraError:
            # Flow B step 9: drop the entry and re-detect at once; not a failure
            self._log.info("cached era rejected by the server; re-detecting with auto")
            await self._cache_drop()
            return await self._connect(_Plan("auto"))

    async def _connect(self, plan: _Plan) -> _Outcome:
        try:
            return await self._connect_loop(plan)
        finally:
            self._client_plan = None
            self._release_gate()

    async def _connect_loop(self, plan: _Plan) -> _Outcome:
        rebuild = False
        rebuilds = 0
        while True:
            transport: ObservedTransport | None = None
            serving = False
            self._session_confirmed = False
            up_since = anyio.current_time()
            caps = self.plan
            try:
                transport = self._factory(self._definition, self._ring.add)
                client_cm = Client(
                    transport,
                    mode=plan.mode,
                    prior_discover=plan.prior_discover,
                    message_handler=self._message_handler,
                    input_required_max_rounds=INPUT_REQUIRED_MAX_ROUNDS,
                    logging_callback=logging_callback(self._ring),
                    **self._handlers.client_kwargs(caps, self.inflight.attribute),
                )
                interrupt = self._interrupt
                async with _shielded_exit(client_cm) as client:
                    if not await _race(partial(self._prepare, client, plan, caps), interrupt):
                        return _Outcome("lost", "stopped")  # stop/cancel during prepare
                    serving = True
                    self._client_plan = caps
                    ended = await self._serve(
                        client, transport, rebuild=rebuild, confirm=not plan.internal
                    )
                    if ended == "rebuild":
                        version = client.protocol_version
                        discover = client.session.discover_result
            except Exception as exc:
                if plan.cached and not serving and _era_indicating(exc, transport):
                    raise _StaleEraError from exc
                return _classify(exc, transport)
            if ended == "stop":
                return _Outcome("lost", "stopped")  # discarded: the command is handled
            if ended == "lost":
                return _Outcome("lost", "transport_lost")
            if ended == "rebuild":  # DD-003: same era, no negotiation, state stays ready
                self._log.info("client capabilities changed; rebuilding the modern client")
                plan, rebuild = _Plan(version, discover, internal=True), True
                continue
            # REQ-CON-015 / Analyze P2-01: the SDK does not re-initialize after a legacy 404
            # on the session; rebuild the Client in place (DD-003 pattern): state stays
            # ready, no server.state_changed, no failure counted. Bounded (security review):
            # a Server that kills every session counts as lost after 3 futile rebuilds.
            if self._session_confirmed or anyio.current_time() - up_since >= SESSION_STABLE_S:
                rebuilds = 0
            rebuilds += 1
            if rebuilds > MAX_SESSION_REBUILDS:
                self._log.warning(
                    "legacy HTTP session terminated %d times in a row; reconnecting", rebuilds
                )
                return _Outcome("transient", SESSION_TERMINATED)
            self._log.warning("legacy HTTP session terminated by the server; re-initializing")
            self._ring.add(
                "legacy HTTP session terminated by the server (404); re-initializing",
                level="warning",
                logger=HOST_LOGGER,
            )
            plan, rebuild = _Plan("legacy"), True

    # ------------------------------------------------------------------ era (F2-08)

    async def _plan(self) -> _Plan:
        """Flow B step 2: forced mode, fresh era-cache entry, or the SDK's ``auto`` probe."""
        forced = self._definition.mode
        if forced == "legacy":
            return _Plan("legacy")
        cached = await self._cache_get()
        modern = cached.discover_result if cached is not None else None
        if forced == "modern":
            return _Plan(LATEST_MODERN_VERSION, modern, discover=modern is None)
        if cached is None:
            return _Plan("auto")
        if modern is not None:
            version = cached.protocol_version
            pin = version if version in MODERN_PROTOCOL_VERSIONS else LATEST_MODERN_VERSION
            return _Plan(pin, modern, cached=True)
        return _Plan("legacy", cached=True)

    async def _prepare(self, client: Client, plan: _Plan, caps: CapabilityPlan) -> None:
        """Inside a fresh Client block, before ``ready``: complete or check the era, then
        (legacy) send the configured log level."""
        if plan.internal:
            return
        await self._prepare_era(client, plan)
        if caps.log_level is not None and era_of(client.protocol_version) == "legacy":
            await self._legacy_set_level(client, caps.log_level)

    async def _legacy_set_level(self, client: Client, level: LogLevel) -> None:
        """REQ-HSH-031 (legacy): ``logging/setLevel`` after ``initialize`` when declared."""
        server_caps = client.server_capabilities
        if server_caps is None or server_caps.logging is None:
            self._log.info("log level configured but the server declares no logging")
            return
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", MCPDeprecationWarning)
                await client.session.set_logging_level(level)
        except MCPError as exc:
            self._log.warning("logging/setLevel failed (JSON-RPC %d)", exc.code)

    async def _prepare_era(self, client: Client, plan: _Plan) -> None:
        if plan.discover:  # forced modern: the Server's real DiscoverResult, never initialize
            raw = await client.session.send_discover(client.protocol_version)
            client.session.adopt(types.DiscoverResult.model_validate(raw))
        elif plan.cached and plan.prior_discover is not None:
            # A pinned Client sends nothing on entry; one cheap declared request proves the
            # cached modern era still holds (an era-indicating error re-detects).
            caps = client.server_capabilities
            if caps.tools is not None:
                await client.list_tools(cache_mode="refresh")
            elif caps.prompts is not None:
                await client.list_prompts(cache_mode="refresh")
            elif caps.resources is not None:
                await client.list_resources(cache_mode="refresh")
        if not plan.cached:
            await self._cache_put(client)

    async def _cache_get(self) -> CachedEra | None:
        if self._era_cache is None:
            return None
        try:
            return await self._era_cache.get(self._definition)
        except Exception as exc:
            self._log.warning("era cache unavailable (%s); probing", type(exc).__name__)
            return None

    async def _cache_put(self, client: Client) -> None:
        if self._era_cache is None:
            return
        version = client.protocol_version
        discover = client.session.discover_result if era_of(version) == "modern" else None
        try:
            await self._era_cache.put(
                self._definition, protocol_version=version, discover_result=discover
            )
        except Exception as exc:
            self._log.warning("could not store the detected era (%s)", type(exc).__name__)

    async def _cache_drop(self) -> None:
        if self._era_cache is None:
            return
        try:
            await self._era_cache.drop(self._definition)
        except Exception as exc:
            self._log.warning("could not drop the cached era (%s)", type(exc).__name__)

    async def _confirm_session(self, client: Client) -> None:
        """One cheap request on a rebuilt legacy Client: success resets the rebuild count.
        ``ping`` exists in the legacy era only, which is the only era that rebuilds."""
        try:
            await client.session.send_ping()
        except Exception as exc:
            self._log.info("ping on the rebuilt session failed (%s)", type(exc).__name__)
            return
        self._session_confirmed = True

    async def _serve(
        self,
        client: Client,
        transport: ObservedTransport,
        *,
        rebuild: bool = False,
        confirm: bool = True,
    ) -> Literal["lost", "session", "stop", "rebuild"]:
        """Inside the Client block: publish ready (or, for an internal rebuild, refresh the
        connection fields in place), run hooks, park until the transport ends, a legacy
        HTTP session is terminated, or a modern capability rebuild finds the lease idle."""
        lease = ClientLease(self.server_id, client, state_of=lambda: self._status.state)
        self._lease = lease
        self._release_gate()  # leases wait for this Client during a rebuild
        rebuilding = False
        try:
            version = client.protocol_version
            fields: dict[str, Any] = {
                "protocol_version": version,
                "era": era_of(version),
                "server_info": client.server_info,
                "capabilities": client.server_capabilities,
                "consecutive_failures": 0,
                "next_retry_at": None,
            }
            self._failures = 0
            if rebuild and self._status.state is S.READY:
                self._update(**fields)
            else:
                self._transition(S.READY, **fields)
            interrupt = self._interrupt
            status = self._status

            async def guarded(hook: OnReady) -> None:
                try:
                    await hook(client, status)
                except Exception as exc:
                    self._log.error("on_ready hook raised %s", type(exc).__name__)

            async def hooks_while_ready() -> None:
                if rebuild and confirm:
                    await self._confirm_session(client)
                async with anyio.create_task_group() as tg:
                    for hook in self._on_ready:
                        tg.start_soon(guarded, hook)
                await anyio.sleep_forever()

            async def until_rebuild() -> None:
                nonlocal rebuilding
                self._rebuild_wanted = anyio.Event()
                if self._rebuild_gate is not None:  # requested while this Client was built
                    self._rebuild_wanted.set()
                async with anyio.create_task_group() as tg:
                    tg.start_soon(hooks_while_ready)
                    while True:
                        await self._rebuild_wanted.wait()
                        self._rebuild_wanted = anyio.Event()
                        if self._rebuild_gate is not None and self._client_plan != self.plan:
                            break
                        self._release_gate()  # the plan changed back: nothing to rebuild
                    await lease.wait_idle()  # in-flight calls finish on this Client
                    rebuilding = self._rebuild_committed = True
                    tg.cancel_scope.cancel()

            ends = (transport.closed, transport.session_terminated, interrupt)
            await _race(until_rebuild, *ends)
            if interrupt.is_set():
                return "stop"
            if rebuilding and not transport.closed.is_set():
                return "rebuild"
            if transport.session_terminated.is_set() and not transport.closed.is_set():
                return "session"
            self._log.warning("transport closed")
            return "lost"
        finally:
            lease.invalidate()
            if self._lease is lease:
                self._lease = None
            if not rebuilding:
                self._release_gate()
