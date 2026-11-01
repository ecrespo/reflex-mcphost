"""``ConnectionManager.append_log``: the MCP Apps bridge's log seam (F7-01; API §4.5, D8)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from anyio.abc import TaskGroup

from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.manager import ConnectionManager
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.connection.stderr import LogEntry, LogRing
from reflex_mcphost.core.errors import HostClosedError, UnknownServerError
from reflex_mcphost.core.events import EventBus, ServerLog
from reflex_mcphost.core.secrets import MASK, default_registry, register_secret

pytestmark = pytest.mark.anyio
APP_SECRET = "app-log-secret-seed-0101"  # noqa: S105 - test sentinel


class FakeSupervisor:
    """A started supervisor's surface: status and a log ring publishing ``server.log``."""

    def __init__(self, definition: ServerDefinition, events: EventBus) -> None:
        self.definition = definition
        self.server_id = definition.id
        self.status = ServerStatus(
            server_id=definition.id, state=ServerState.READY, since=datetime.now(UTC)
        )
        self.logs = LogRing(
            3, on_entry=lambda e: events.publish(ServerLog(server_id=definition.id, entry=e))
        )

    def start(self, task_group: TaskGroup) -> None:
        pass

    async def aclose(self) -> None:
        pass


@pytest.fixture
def seeded() -> Iterator[str]:
    register_secret(APP_SECRET)
    yield APP_SECRET
    default_registry.forget(APP_SECRET)


@pytest.fixture
async def manager() -> AsyncIterator[tuple[ConnectionManager, EventBus]]:
    events = EventBus()

    def factory(definition: ServerDefinition, on_change: Callable[[], None]) -> Any:
        return FakeSupervisor(definition, events)

    async with anyio.create_task_group() as tg:
        mgr = ConnectionManager(events=events)
        mgr.attach(tg, factory)
        mgr.populate(
            [
                ServerDefinition(id="apps", transport=StdioTransport(command="x")),
                ServerDefinition(id="off", transport=StdioTransport(command="x"), enabled=False),
            ],
            frozenset(),
        )
        yield mgr, events
        tg.cancel_scope.cancel()


def _entry(text: str, level: str = "info") -> LogEntry:
    return LogEntry(level=level, logger="mcp-app", timestamp=datetime.now(UTC), text=text)  # type: ignore[arg-type]


async def test_append_log_redacts_and_publishes(
    manager: tuple[ConnectionManager, EventBus], seeded: str
) -> None:
    """REQ-APP-012, REQ-HSH-030 (Art. 5): the entry lands in the Server's ring after the
    redaction filter (a seeded secret is masked) and ``server.log`` is published."""
    mgr, events = manager
    async with events.subscribe() as stream:
        mgr.append_log("apps", _entry(f"tools/call → success {seeded}", "warning"))
        with anyio.fail_after(2):
            event = await anext(stream)
    (logged,) = mgr.logs("apps")
    assert logged.text == f"tools/call → success {MASK}"
    assert (logged.level, logged.logger) == ("warning", "mcp-app")
    assert isinstance(event, ServerLog)
    assert event.server_id == "apps"
    assert event.entry == logged
    assert seeded not in repr(event)


async def test_append_log_ring_is_bounded(manager: tuple[ConnectionManager, EventBus]) -> None:
    """REQ-APP-012: the per-Server ring keeps its bound (oldest dropped first)."""
    mgr, _ = manager
    for i in range(5):
        mgr.append_log("apps", _entry(f"ping → ok {i}"))
    assert [e.text for e in mgr.logs("apps")] == ["ping → ok 2", "ping → ok 3", "ping → ok 4"]


async def test_append_log_errors(manager: tuple[ConnectionManager, EventBus]) -> None:
    """REQ-APP-012 (API §4.5): an unknown Server → ``UnknownServerError``; a parked
    (disabled) Server has no ring, the entry is dropped; after ``aclose()`` →
    ``HostClosedError``."""
    mgr, _ = manager
    with pytest.raises(UnknownServerError):
        mgr.append_log("nope", _entry("ping → ok"))
    mgr.append_log("off", _entry("ping → ok"))
    assert mgr.logs("off") == []
    await mgr.aclose()
    with pytest.raises(HostClosedError):
        mgr.append_log("apps", _entry("ping → ok"))
