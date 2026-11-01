"""EventBus (API §4.11, DD-014)."""

from __future__ import annotations

import anyio
import pytest

from reflex_mcphost.core.events import (
    CatalogChanged,
    EventBus,
    EventsDropped,
    HostEvent,
    ResourceUpdated,
    host_event_adapter,
)

pytestmark = pytest.mark.anyio


def _ev(i: int) -> ResourceUpdated:
    return ResourceUpdated(server_id="s", uri=f"file:///{i}")


async def test_fan_out_to_all_subscribers() -> None:
    """API §4.11: every subscriber receives every event in order."""
    bus = EventBus()
    async with bus.subscribe() as a, bus.subscribe() as b:
        for i in range(3):
            bus.publish(_ev(i))
        got_a = [await a.__anext__() for _ in range(3)]
        got_b = [await b.__anext__() for _ in range(3)]
    assert got_a == got_b == [_ev(0), _ev(1), _ev(2)]


async def test_overflow_drops_oldest_with_one_dropped_event() -> None:
    """DD-014: on overflow the newest events survive, preceded by one events.dropped."""
    bus = EventBus()
    async with bus.subscribe(buffer=4) as sub:
        for i in range(10):
            bus.publish(_ev(i))  # never blocks
        received: list[HostEvent] = []
        with anyio.move_on_after(0.1):
            async for ev in sub:
                received.append(ev)
    assert received[0] == EventsDropped(count=6)
    assert received[1:] == [_ev(i) for i in range(6, 10)]
    assert sum(isinstance(e, EventsDropped) for e in received) == 1


async def test_slow_subscriber_does_not_block_others() -> None:
    """API §4.11: a full subscriber never blocks the publisher or another subscriber."""
    bus = EventBus()
    async with bus.subscribe(buffer=1) as _slow, bus.subscribe(buffer=100) as fast:
        for i in range(50):
            bus.publish(_ev(i))  # synchronous: it cannot await on a full subscriber
        got = [await fast.__anext__() for _ in range(50)]
    assert got == [_ev(i) for i in range(50)]


async def test_streams_closed_after_unsubscribe() -> None:
    """DD-014: leaving the context closes the stream and removes the subscriber."""
    bus = EventBus()
    async with bus.subscribe() as sub:
        pass
    assert bus.subscriber_count == 0
    bus.publish(_ev(0))  # no error with no subscribers
    with pytest.raises(StopAsyncIteration):
        await sub.__anext__()


async def test_new_episode_after_drain_gets_a_new_dropped_event() -> None:
    """DD-014: one events.dropped per overflow episode."""
    bus = EventBus()
    async with bus.subscribe(buffer=1) as sub:
        bus.publish(_ev(0))
        bus.publish(_ev(1))
        assert await sub.__anext__() == EventsDropped(count=1)
        assert await sub.__anext__() == _ev(1)
        bus.publish(_ev(2))
        bus.publish(_ev(3))
        bus.publish(_ev(4))
        assert await sub.__anext__() == EventsDropped(count=2)
        assert await sub.__anext__() == _ev(4)


def test_discriminated_union_round_trip() -> None:
    """API §4.11: HostEvent is a discriminated union on `type`."""
    ev = CatalogChanged(server_id="s", lists=("tools", "prompts"))
    data = ev.model_dump(mode="json")
    assert data["type"] == "catalog.changed"
    assert host_event_adapter.validate_python(data) == ev
