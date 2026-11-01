"""The per-tab watcher: ``apply_events`` and token de-duplication (F6-02; DD-011, S-5 §4).

Driven by fake streams and sinks — no Reflex runtime, no Host process.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.config.models import RegistryDiff
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.events import (
    CatalogChanged,
    EventBus,
    EventsDropped,
    RegistryChanged,
    ResourceUpdated,
    ServerLog,
    ServerStateChanged,
)
from reflex_mcphost.ui.prompter import PromptChange
from reflex_mcphost.ui.state import WatcherRegistry, apply_events, watch_host

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 1, tzinfo=UTC)


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.started = anyio.Event()

    async def refresh_all(self) -> None:
        self.calls.append(("refresh_all", None))
        self.started.set()

    async def server_changed(self, server_id: str) -> None:
        self.calls.append(("server_changed", server_id))

    async def catalog_changed(self, server_id: str, lists: tuple[str, ...]) -> None:
        self.calls.append(("catalog_changed", (server_id, lists)))

    async def server_log(self, server_id: str, entry: LogEntry) -> None:
        self.calls.append(("server_log", (server_id, entry.text)))

    async def servers_changed(self, server_ids: tuple[str, ...]) -> None:
        self.calls.append(("servers_changed", server_ids))

    async def prompts_changed(self) -> None:
        self.calls.append(("prompts_changed", None))


async def _source(*items: object) -> AsyncIterator[object]:
    for item in items:
        yield item


async def test_only_the_affected_server_is_recomputed() -> None:
    """REQ-CON-010, REQ-NFR-004: each event recomputes only its Server; registry diffs name
    the added, removed and changed Servers; unrelated events are ignored."""
    sink = RecordingSink()
    entry = LogEntry(level="info", logger="stderr", timestamp=NOW, text="line")
    await apply_events(
        _source(
            ServerStateChanged(server_id="a", old=ServerState.CONNECTING, new=ServerState.READY),
            CatalogChanged(server_id="b", lists=("tools",)),
            ServerLog(server_id="a", entry=entry),
            RegistryChanged(diff=RegistryDiff(added=("c",), removed=("d",), changed=("a",))),
            ResourceUpdated(server_id="a", uri="file:///x"),
        ),
        sink,
    )
    assert sink.calls == [
        ("server_changed", "a"),
        ("catalog_changed", ("b", ("tools",))),
        ("server_log", ("a", "line")),
        ("servers_changed", ("c", "d", "a")),
    ]


async def test_dropped_events_trigger_a_full_recompute() -> None:
    """REQ-CON-010, DD-014: after ``events.dropped`` the whole view is recomputed."""
    sink = RecordingSink()
    await apply_events(_source(EventsDropped(count=3)), sink)
    assert sink.calls == [("refresh_all", None)]


async def test_prompt_changes_re_render_the_cards() -> None:
    """REQ-UI-005, Flow D step 4 (F6-03): every registry change — including its
    ``dropped`` marker — re-renders the cards from ``pending()``."""
    sink = RecordingSink()
    await apply_events(_source(PromptChange("added", "r1"), PromptChange("dropped", count=2)), sink)
    assert sink.calls == [("prompts_changed", None), ("prompts_changed", None)]


class FakeRuntime:
    def __init__(self, host: object | None) -> None:
        self._host = host
        self.phase = "running" if host is not None else "starting"

    @property
    def host(self) -> Any:
        if self._host is None:
            raise RuntimeError("not running")
        return self._host


class FakeHost:
    def __init__(self) -> None:
        self.events = EventBus()


async def _commit(fields: dict[str, Any]) -> None:
    pass


async def test_second_watch_for_a_token_starts_no_subscriber_and_restart_works() -> None:
    """REQ-UI-007, DD-011 (S-5 pitfall): watchers are de-duplicated per client token in a
    process-local registry; after cancellation the token is released and a restart for
    the same token subscribes again."""
    host, watchers = FakeHost(), WatcherRegistry()
    sinks: list[RecordingSink] = []

    def view(host: object, commit: object, prompts: object) -> RecordingSink:
        sinks.append(RecordingSink())
        return sinks[-1]

    async def run() -> None:
        await watch_host(
            "tok-123456", _commit, runtime=FakeRuntime(host), watchers=watchers, view=view
        )

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while not sinks:
                await anyio.lowlevel.checkpoint()
            await sinks[0].started.wait()
            await run()  # same token: returns at once, no second subscriber
            assert host.events.subscriber_count == 1
            assert watchers.active("tok-123456")
            tg.cancel_scope.cancel()
        assert not watchers.active("tok-123456")
        assert host.events.subscriber_count == 0
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            while len(sinks) < 2:
                await anyio.lowlevel.checkpoint()
            await sinks[1].started.wait()
            assert host.events.subscriber_count == 1
            host.events.publish(CatalogChanged(server_id="a", lists=("tools",)))
            while len(sinks[1].calls) < 2:
                await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()
    assert sinks[1].calls[-1] == ("catalog_changed", ("a", ("tools",)))
    assert len(sinks) == 2


async def test_no_host_commits_a_banner_and_releases_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-UI-007: with no running Host the watcher sets the "Host starting" banner, raises
    nothing and leaves the token free; start/stop are logged at INFO with the token suffix
    only."""
    committed: list[dict[str, Any]] = []

    async def commit(fields: dict[str, Any]) -> None:
        committed.append(fields)

    watchers = WatcherRegistry()
    await watch_host("tok-abcdefgh", commit, runtime=FakeRuntime(None), watchers=watchers)
    assert committed == [{"banner": "Host starting…"}]
    assert not watchers.active("tok-abcdefgh")

    host = FakeHost()
    caplog.set_level("INFO", logger="reflex_mcphost.ui.state")
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            sink = RecordingSink()
            tg.start_soon(
                lambda: watch_host(
                    "tok-abcdefgh",
                    commit,
                    runtime=FakeRuntime(host),
                    watchers=watchers,
                    view=lambda h, c, p: sink,
                )
            )
            await sink.started.wait()
            tg.cancel_scope.cancel()
    messages = [r.getMessage() for r in caplog.records]
    assert any("watcher started" in m and "cdefgh" in m for m in messages)
    assert any("watcher stopped" in m for m in messages)
    assert not any("tok-abcdefgh" in m for m in messages)
