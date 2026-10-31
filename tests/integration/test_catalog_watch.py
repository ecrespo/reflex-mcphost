"""Catalog watchers on both eras (F2-12; REQ-CAT-003, -012, -013; Flow B step 7)."""

from __future__ import annotations

from typing import Any

import anyio
import anyio.lowlevel
import mcp_types as types
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.catalog import TTL_FLOOR_S
from reflex_mcphost.core.connection.state import JITTER_MAX, JITTER_MIN
from reflex_mcphost.core.events import CatalogChanged
from tests.integration.connection.test_supervisor import TIMEOUT, stdio_def
from tests.integration.test_catalog_refresh import Running, catalog_running
from tests.unit.core.test_catalog import FakeClient, harness, serve, status, tool

pytestmark = pytest.mark.anyio


class GateSleep:
    """Records each requested delay and blocks until the test releases it (fake clock)."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self._released = 0
        self._changed = anyio.Event()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self._wake()
        ticket = len(self.delays)
        while self._released < ticket:
            await self._changed.wait()

    def _wake(self) -> None:
        self._changed.set()
        self._changed = anyio.Event()

    def release(self) -> None:
        self._released += 1
        self._wake()

    async def requested(self, count: int) -> None:
        with anyio.fail_after(TIMEOUT):
            while len(self.delays) < count:
                await self._changed.wait()


async def _add_tool_changes_only_tools(run: Running, server_id: str) -> None:
    await run.ready(server_id)
    before_counts = await run.counts(server_id)
    before = len(run.observed.of(server_id))
    await run.call(server_id, "add_tool", {"name": "fresh"})
    await run.observed.until(lambda: "fresh" in {t.name for t in run.catalog.tools(server_id)})
    await anyio.sleep(0.2)  # a second (wrong) event or re-fetch would show up by now
    new_events = run.observed.of(server_id)[before:]
    assert new_events == [CatalogChanged(server_id=server_id, lists=("tools",))]
    after_counts = await run.counts(server_id)
    assert after_counts["tools"] == before_counts["tools"] + 1
    for kind in ("resources", "resource_templates", "prompts"):
        assert after_counts[kind] == before_counts[kind], kind


async def test_modern_list_change_refetches_only_tools(modern_stdio: StdioServerParameters) -> None:
    """REQ-CAT-003 (modern): the listen stream's toolsListChanged re-fetches only the tools
    list and emits exactly one catalog.changed(lists=("tools",))."""
    async with catalog_running(stdio_def(modern_stdio)) as run:
        await _add_tool_changes_only_tools(run, "modern")


@pytest.mark.interop
async def test_legacy_list_change_refetches_only_tools(legacy_stdio: StdioServerParameters) -> None:
    """REQ-CAT-003 (legacy): notifications/tools/list_changed re-fetches only the tools list
    and emits exactly one catalog.changed(lists=("tools",))."""
    async with catalog_running(stdio_def(legacy_stdio, server_id="legacy")) as run:
        await _add_tool_changes_only_tools(run, "legacy")


async def test_closed_listen_stream_is_reopened_and_lists_refetched(
    modern_stdio: StdioServerParameters,
) -> None:
    """REQ-CAT-012: a listen stream the Server closes while ready is reopened after the
    REQ-CON-005 backoff and every list is re-fetched once it is open again; later list
    changes still arrive on the new stream."""
    sleep = GateSleep()
    async with catalog_running(stdio_def(modern_stdio), catalog_sleep=sleep) as run:
        await run.ready("modern")
        before = await run.counts("modern")
        await run.call("modern", "close_listen")
        await sleep.requested(1)
        (delay,) = sleep.delays  # backoff_delay(0): 2**0 * jitter in [0.5, 1.5]
        assert JITTER_MIN <= delay <= JITTER_MAX
        seen = len(run.observed.of("modern"))
        sleep.release()
        await run.observed.until(lambda: len(run.observed.of("modern")) > seen)
        refetch = run.observed.of("modern")[seen]
        assert set(refetch.lists) == {"tools", "resources", "resource_templates", "prompts"}
        after = await run.counts("modern")
        assert all(after[k] == before[k] + 1 for k in before), (before, after)
        await run.call("modern", "add_tool", {"name": "after_reopen"})
        await run.observed.until(
            lambda: "after_reopen" in {t.name for t in run.catalog.tools("modern")}
        )


async def test_unstable_listen_stream_keeps_growing_the_backoff(
    modern_stdio: StdioServerParameters,
) -> None:
    """REQ-CAT-012: a stream that is reopened and closed again at once does not reset the
    backoff (it must stay open LISTEN_STABLE_S first), so a Server cannot force a reopen
    and full refetch every second."""
    sleep = GateSleep()
    async with catalog_running(stdio_def(modern_stdio), catalog_sleep=sleep) as run:
        await run.ready("modern")
        await run.counts("modern")  # round trip: the listen stream is open by now
        await run.call("modern", "close_listen")
        await sleep.requested(1)
        seen = len(run.observed.of("modern"))
        sleep.release()
        await run.observed.until(lambda: len(run.observed.of("modern")) > seen)  # reopened
        await run.call("modern", "close_listen")
        await sleep.requested(2)
        first, second = sleep.delays
        assert JITTER_MIN <= first <= JITTER_MAX
        assert 2 * JITTER_MIN <= second <= 2 * JITTER_MAX  # backoff_delay(1), not reset
        sleep.release()


async def test_ttl_refresh_honours_the_30s_floor() -> None:
    """REQ-CAT-013 (SHOULD): a list the Server does not watch (no listChanged) is re-fetched
    when its ttlMs elapses, but never more often than every 30 s (fake clock)."""
    caps = types.ServerCapabilities(tools=types.ToolsCapability(list_changed=False))
    sleep = GateSleep()
    client = FakeClient(tools=[[tool("a")]], caps=caps, ttl_ms=5_000)
    async with harness(sleep=sleep) as (catalog, changes, tg):
        await serve(catalog, changes, tg, client, status("ttl", era="modern", caps=caps))
        await sleep.requested(1)
        assert sleep.delays == [TTL_FLOOR_S]
        client.ttl_ms = 45_000
        client.tool_pages = [[tool("a"), tool("b")]]
        sleep.release()
        await changes.wait(2)
        assert [t.name for t in catalog.tools("ttl")] == ["a", "b"]
        await sleep.requested(2)
        assert sleep.delays == [TTL_FLOOR_S, 45.0]
        client.ttl_ms = 0
        sleep.release()
        await changes.wait(3)
        await anyio.sleep(0.05)
        assert len(sleep.delays) == 2  # no TTL → no further refresh
    assert [m for m, _ in client.calls] == ["tools/list"] * 3


async def test_watched_lists_get_no_ttl_refresh() -> None:
    """REQ-CAT-013: a list covered by list-change notifications is not polled by TTL."""
    caps = types.ServerCapabilities(tools=types.ToolsCapability(list_changed=True))
    sleep = GateSleep()
    client: Any = FakeClient(tools=[[tool("a")]], caps=caps, ttl_ms=60_000)
    async with harness(sleep=sleep) as (catalog, changes, tg):
        await serve(catalog, changes, tg, client, status("w", caps=caps))
        for _ in range(5):
            await anyio.lowlevel.checkpoint()
        assert sleep.delays == []
