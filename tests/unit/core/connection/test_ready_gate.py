"""``ConnectionManager.wait_ready`` and its Host-injected ready gate (API §4.5, Flow B).

The manager runs on fake supervisors whose state and Client the test drives; the gate is
either a controllable fake or the real ``Catalog.wait_loaded``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from anyio.abc import TaskGroup

from reflex_mcphost.core.catalog import Catalog
from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.lease import ClientLease
from reflex_mcphost.core.connection.manager import ConnectionManager
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.errors import ServerUnavailableError, UnknownServerError
from reflex_mcphost.core.events import EventBus
from tests.unit.core.test_catalog import ALL_CAPS, FakeClient, tool

pytestmark = pytest.mark.anyio
S = ServerState
TIMEOUT = 5.0


class FakeSupervisor:
    """What ``ConnectionManager`` reads from a supervisor; ``set`` drives it."""

    def __init__(self, definition: ServerDefinition, on_change: Callable[[], None]) -> None:
        self.definition = definition
        self.server_id = definition.id
        self._on_change = on_change
        self.status = ServerStatus(server_id=definition.id, state=S.CONNECTING, since=_now())
        self.current_lease: ClientLease | None = None

    def start(self, task_group: TaskGroup) -> None:
        pass

    async def aclose(self) -> None:
        pass

    def set(self, state: ServerState, client: Any = None) -> None:
        if self.current_lease is not None and client is None:
            self.current_lease.invalidate()
            self.current_lease = None
        if client is not None:
            self.current_lease = ClientLease(self.server_id, client)
        self.status = ServerStatus(
            server_id=self.server_id,
            state=state,
            since=_now(),
            era="legacy" if state is S.READY else None,
            capabilities=ALL_CAPS if state is S.READY else None,
        )
        self._on_change()


def _now() -> datetime:
    return datetime.now(UTC)


class Gate:
    """A ready gate that resolves for a Client once ``release(client)`` is called."""

    def __init__(self) -> None:
        self.released: set[int] = set()
        self.asked: list[tuple[str, Any]] = []
        self._changed = anyio.Event()

    def release(self, client: Any) -> None:
        self.released.add(id(client))
        self._changed.set()
        self._changed = anyio.Event()

    async def __call__(self, server_id: str, client: Any) -> None:
        self.asked.append((server_id, client))
        while id(client) not in self.released:
            await self._changed.wait()


@pytest.fixture
async def setup() -> Any:
    gate = Gate()
    supervisors: dict[str, FakeSupervisor] = {}

    def factory(definition: ServerDefinition, on_change: Callable[[], None]) -> Any:
        supervisors[definition.id] = FakeSupervisor(definition, on_change)
        return supervisors[definition.id]

    async with anyio.create_task_group() as tg:
        mgr = ConnectionManager(events=EventBus(), ready_gate=gate)
        mgr.attach(tg, factory)
        definition = ServerDefinition(id="a", transport=StdioTransport(command="x"))
        mgr.populate([definition], frozenset())
        yield mgr, supervisors["a"], gate, tg
        tg.cancel_scope.cancel()


async def test_wait_ready_waits_for_the_gate_of_the_current_client(setup: Any) -> None:
    """API §4.5: ``ready`` alone does not resolve wait_ready; the gate for the live Client
    does, and the returned status is ``ready``."""
    mgr, sup, gate, tg = setup
    client = object()
    got: list[ServerStatus] = []
    done = anyio.Event()

    async def waiter() -> None:
        got.append(await mgr.wait_ready("a", timeout=TIMEOUT))
        done.set()

    tg.start_soon(waiter)
    sup.set(S.READY, client)
    await anyio.wait_all_tasks_blocked()
    assert got == []
    assert gate.asked == [("a", client)]
    gate.release(client)
    with anyio.fail_after(TIMEOUT):
        await done.wait()
    assert got[0].state is S.READY


async def test_wait_ready_times_out_when_the_gate_never_completes(setup: Any) -> None:
    """API §4.5: the gate is awaited within the same ``timeout``: TimeoutError."""
    mgr, sup, _gate, _tg = setup
    sup.set(S.READY, object())
    with pytest.raises(TimeoutError):
        await mgr.wait_ready("a", timeout=0.2)


async def test_wait_ready_raises_if_the_server_fails_while_gated(setup: Any) -> None:
    """API §4.5: ServerUnavailableError when the state becomes ``failed`` while waiting
    for the gate."""
    mgr, sup, _gate, tg = setup
    sup.set(S.READY, object())

    async def fail_later() -> None:
        await anyio.wait_all_tasks_blocked()
        sup.set(S.FAILED)

    tg.start_soon(fail_later)
    with anyio.fail_after(TIMEOUT), pytest.raises(ServerUnavailableError):
        await mgr.wait_ready("a", timeout=TIMEOUT)


async def test_wait_ready_follows_a_reconnect_to_the_new_session(setup: Any) -> None:
    """API §4.5: a connection that drops and comes back while waiting is gated on the new
    session's Client; releasing the old one does not resolve wait_ready."""
    mgr, sup, gate, tg = setup
    old, new = object(), object()
    got: list[ServerStatus] = []
    done = anyio.Event()

    async def waiter() -> None:
        got.append(await mgr.wait_ready("a", timeout=TIMEOUT))
        done.set()

    sup.set(S.READY, old)
    tg.start_soon(waiter)
    await anyio.wait_all_tasks_blocked()
    sup.set(S.RECONNECTING)
    sup.set(S.CONNECTING)
    sup.set(S.READY, new)
    await anyio.wait_all_tasks_blocked()
    gate.release(old)
    await anyio.wait_all_tasks_blocked()
    assert got == []
    gate.release(new)
    with anyio.fail_after(TIMEOUT):
        await done.wait()
    assert gate.asked[-1] == ("a", new)


async def test_wait_ready_without_a_gate_resolves_on_ready() -> None:
    """API §4.5: a manager built without a gate (no Catalog) resolves on ``ready``."""
    supervisors: dict[str, FakeSupervisor] = {}

    def factory(definition: ServerDefinition, on_change: Callable[[], None]) -> Any:
        supervisors[definition.id] = FakeSupervisor(definition, on_change)
        return supervisors[definition.id]

    async with anyio.create_task_group() as tg:
        mgr = ConnectionManager(events=EventBus())
        mgr.attach(tg, factory)
        mgr.populate([ServerDefinition(id="a", transport=StdioTransport(command="x"))], frozenset())
        supervisors["a"].set(S.READY, object())
        with anyio.fail_after(TIMEOUT):
            assert (await mgr.wait_ready("a", timeout=TIMEOUT)).state is S.READY


# ------------------------------------------------------------------ Catalog as the gate


def _no_supervisor(server_id: str) -> Any:
    raise UnknownServerError(server_id)


class SlowClient(FakeClient):
    """``tools/list`` blocks until ``go`` is set."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.go = anyio.Event()

    async def list_tools(self, *, cursor: str | None = None, cache_mode: str = "use") -> Any:
        await self.go.wait()
        return await super().list_tools(cursor=cursor, cache_mode=cache_mode)


@pytest.mark.parametrize("fail", [frozenset(), frozenset({"tools/list"})])
async def test_catalog_gate_resolves_after_the_initial_refresh(fail: frozenset[str]) -> None:
    """REQ-CAT-001 — API §4.5: with ``Catalog.wait_loaded`` as the gate, wait_ready resolves
    only after the initial full refresh of the current Client — also when that refresh
    fails (the failed list is simply absent)."""
    catalog = Catalog(events=EventBus(), supervisor_of=_no_supervisor)
    client = SlowClient(tools=[[tool("echo")]], fail=fail)
    supervisors: dict[str, FakeSupervisor] = {}

    def factory(definition: ServerDefinition, on_change: Callable[[], None]) -> Any:
        supervisors[definition.id] = FakeSupervisor(definition, on_change)
        return supervisors[definition.id]

    got: list[ServerStatus] = []

    done = anyio.Event()
    async with anyio.create_task_group() as tg:
        mgr = ConnectionManager(events=EventBus(), ready_gate=catalog.wait_loaded)
        mgr.attach(tg, factory)
        mgr.populate([ServerDefinition(id="a", transport=StdioTransport(command="x"))], frozenset())
        sup = supervisors["a"]
        sup.set(S.READY, client)
        tg.start_soon(catalog.on_ready, client, sup.status)

        async def waiter() -> None:
            got.append(await mgr.wait_ready("a", timeout=TIMEOUT))
            done.set()

        tg.start_soon(waiter)
        await anyio.wait_all_tasks_blocked()
        assert got == []
        client.go.set()
        with anyio.fail_after(TIMEOUT):
            await done.wait()
        names = [t.name for t in catalog.tools("a")]
        assert names == ([] if fail else ["echo"])
        tg.cancel_scope.cancel()


async def test_catalog_gate_ignores_a_previous_session() -> None:
    """API §4.5: the Catalog's loaded mark belongs to one Client; a new Client is not
    loaded until its own refresh ends, and the mark goes away with the session."""
    catalog = Catalog(events=EventBus(), supervisor_of=_no_supervisor)
    first, second = FakeClient(tools=[[tool("echo")]]), SlowClient(tools=[[tool("echo")]])
    ready = ServerStatus(
        server_id="a", state=S.READY, since=_now(), era="legacy", capabilities=ALL_CAPS
    )
    async with anyio.create_task_group() as tg:
        scope = anyio.CancelScope()

        async def session() -> None:
            with scope:
                await catalog.on_ready(first, ready)  # type: ignore[arg-type]

        tg.start_soon(session)
        with anyio.fail_after(TIMEOUT):
            await catalog.wait_loaded("a", first)  # type: ignore[arg-type]
        scope.cancel()
        await anyio.wait_all_tasks_blocked()
        tg.start_soon(catalog.on_ready, second, ready)
        done = anyio.Event()

        async def wait_second() -> None:
            await catalog.wait_loaded("a", second)  # type: ignore[arg-type]
            done.set()

        tg.start_soon(wait_second)
        await anyio.wait_all_tasks_blocked()
        assert not done.is_set()
        second.go.set()
        with anyio.fail_after(TIMEOUT):
            await done.wait()
        tg.cancel_scope.cancel()
