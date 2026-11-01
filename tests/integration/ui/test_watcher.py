"""The watcher against a real Host, modern and legacy stdio (F6-02; DD-011, REQ-CON-010).

``watch_host`` runs with a runtime holding the test's Host and a plain commit function
standing in for ``async with self`` on a tab's ``HostState``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.host import Host
from reflex_mcphost.ui.state import WatcherRegistry, watch_host
from tests.integration.toolset.rig import TIMEOUT, Setup, add_tool, era_server, host_for, setup

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures


class Tab:
    """What one tab's HostState would hold, updated by the watcher's commits."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}
        self._changed = anyio.Event()

    async def commit(self, fields: dict[str, Any]) -> None:
        self.fields.update(fields)
        self._changed.set()
        self._changed = anyio.Event()

    async def until(self, predicate: Callable[[dict[str, Any]], bool]) -> None:
        with anyio.fail_after(TIMEOUT):
            while not predicate(self.fields):
                await self._changed.wait()


class Runtime:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.phase = "running"


@asynccontextmanager
async def watching(host: Host, tab_id: str = "tab-000001") -> AsyncIterator[Tab]:
    tab = Tab()
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            lambda: watch_host(
                tab_id, tab.commit, runtime=Runtime(host), watchers=WatcherRegistry()
            )
        )
        yield tab
        tg.cancel_scope.cancel()


def _server(fields: dict[str, Any], server_id: str) -> dict[str, Any] | None:
    return next((r for r in fields.get("servers", []) if r["id"] == server_id), None)


async def test_rows_reach_ready_and_follow_catalog_changes(
    setup: Setup, era_server: tuple[str, str]
) -> None:
    """REQ-CON-010, REQ-UI-003 (rows), REQ-UI-004 (catalog rows), both eras: the Server's
    row reaches ``ready`` with the fixture's capabilities; after the fixture registers a
    tool, the catalog rows gain it once ``catalog.changed`` arrived."""
    async with host_for(setup, era_server) as (host, server_id), watching(host) as tab:

        def ready(fields: dict[str, Any]) -> bool:
            row = _server(fields, server_id)
            return row is not None and row["state"] == "ready"

        await tab.until(ready)
        row = _server(tab.fields, server_id)
        assert row is not None
        assert "tools" in row["capabilities"]
        assert row["era"] == server_id  # ids are "modern" / "legacy"
        assert tab.fields["banner"] == ""
        fqn = f"{server_id}::late_tool"
        assert fqn not in {t["fqn"] for t in tab.fields["tools"]}
        await add_tool(host, server_id, "late_tool")
        await tab.until(lambda f: fqn in {t["fqn"] for t in f["tools"]})
        await host.connections.disconnect(server_id)
        await tab.until(lambda f: (_server(f, server_id) or {}).get("state") == "disconnected")
