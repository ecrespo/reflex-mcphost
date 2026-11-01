"""Catalog refresh against the fixture Servers, both eras (F2-11; Flow B steps 6-7).

``catalog_running`` wires a ``Catalog`` to real ``ServerSupervisor``s exactly like the Host
does (``on_ready`` hook + ``message_handler``); the other catalog test modules reuse it.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import anyio.lowlevel
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.catalog import DEFAULT_VISIBILITY, Catalog
from reflex_mcphost.core.config.models import ServerDefinition
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.events import CatalogChanged, EventBus, HostEvent, ResourceUpdated
from reflex_mcphost.core.mrtr import InputRequestRouter
from tests.integration.connection.test_supervisor import (
    TIMEOUT,
    FakeSleep,
    stdio_def,
    wait_state,
)
from tests.integration.test_host import MODERN_TABLE, Setup, started

pytestmark = pytest.mark.anyio
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
S = ServerState


@dataclass
class Observed:
    """Catalog events of one bus, with a wakeup for polling assertions."""

    changes: list[CatalogChanged] = field(default_factory=list)
    updates: list[ResourceUpdated] = field(default_factory=list)
    _changed: anyio.Event = field(default_factory=anyio.Event)

    def add(self, event: HostEvent) -> None:
        if isinstance(event, CatalogChanged):
            self.changes.append(event)
        elif isinstance(event, ResourceUpdated):
            self.updates.append(event)
        else:
            return
        self._changed.set()
        self._changed = anyio.Event()

    def of(self, server_id: str) -> list[CatalogChanged]:
        return [e for e in self.changes if e.server_id == server_id]

    async def until(self, condition: Callable[[], bool], within: float = TIMEOUT) -> None:
        with anyio.fail_after(within):
            while not condition():
                await self._changed.wait()


@dataclass
class Running:
    catalog: Catalog
    supervisors: dict[str, ServerSupervisor]
    observed: Observed
    router: InputRequestRouter

    async def ready(self, *server_ids: str) -> None:
        """Every Server is ready and its first full refresh was published."""
        for server_id in server_ids:
            await wait_state(self.supervisors[server_id], S.READY)
        await self.observed.until(
            lambda: all(
                any(set(e.lists) >= {"tools"} for e in self.observed.of(i)) for i in server_ids
            )
        )

    async def call(self, server_id: str, tool: str, args: dict[str, Any] | None = None) -> str:
        async with self.supervisors[server_id].lease() as client:
            result = await client.call_tool(tool, args or {})
        return str(result.content[0].text)  # type: ignore[union-attr]

    async def counts(self, server_id: str) -> dict[str, int]:
        counts: dict[str, int] = json.loads(await self.call(server_id, "list_counts"))
        return counts


@asynccontextmanager
async def catalog_running(
    *definitions: ServerDefinition, catalog_sleep: Any = None, **supervisor_kwargs: Any
) -> AsyncIterator[Running]:
    bus = EventBus()
    supervisors: dict[str, ServerSupervisor] = {}

    def supervisor_of(server_id: str) -> ServerSupervisor:
        try:
            return supervisors[server_id]
        except KeyError:
            raise UnknownServerError(server_id) from None

    kwargs: dict[str, Any] = {"sleep": catalog_sleep} if catalog_sleep is not None else {}
    router = InputRequestRouter()
    catalog = Catalog(events=bus, supervisor_of=supervisor_of, router=router, **kwargs)
    observed = Observed()
    supervisor_kwargs.setdefault("sleep", FakeSleep())
    async with anyio.create_task_group() as tg, bus.subscribe(buffer=4096) as events:

        async def collect() -> None:
            async for event in events:
                observed.add(event)

        tg.start_soon(collect)
        for definition in definitions:
            sup = ServerSupervisor(
                definition,
                events=bus,
                on_ready=[catalog.on_ready],
                message_handler=catalog.message_handler(definition.id),
                **supervisor_kwargs,
            )
            supervisors[definition.id] = sup
            sup.start(tg)
        try:
            yield Running(catalog, supervisors, observed, router)
        finally:
            with anyio.fail_after(TIMEOUT):
                for sup in supervisors.values():
                    await sup.aclose()
            await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()


async def test_modern_full_refresh(modern_stdio: StdioServerParameters) -> None:
    """REQ-CAT-001, REQ-CAT-014, REQ-CAT-002: on ready a modern Server's tools, resources,
    templates and prompts are all fetched; FQNs, MCP Apps metadata and the SDK's
    x-mcp-header filtering hold on the wire."""
    async with catalog_running(stdio_def(modern_stdio)) as run:
        await run.ready("modern")
        catalog = run.catalog
        names = [t.name for t in catalog.tools("modern")]
        assert names == sorted(names)
        assert {"echo", "ui_app", "ui_legacy"} <= set(names)
        assert "bad_header" not in names  # dropped by the SDK (REQ-CON-018)
        app = catalog.get_tool("modern::ui_app")
        assert (app.ui_resource_uri, app.visibility) == ("ui://fixture/app", ("app",))
        legacy_meta = catalog.get_tool("modern::ui_legacy")
        assert legacy_meta.ui_resource_uri == "ui://fixture/legacy"
        assert legacy_meta.visibility == DEFAULT_VISIBILITY
        assert catalog.get_tool("modern::echo").definition_hash.startswith("sha256:")
        assert [r.uri for r in catalog.resources("modern")] == ["fixture://blob", "fixture://hello"]
        assert [str(t.fqn) for t in catalog.resource_templates()] == ["modern::item"]
        assert [str(p.fqn) for p in catalog.prompts()] == ["modern::ask_prompt", "modern::greet"]
        assert not any(t.stale for t in catalog.tools())


@pytest.mark.interop
async def test_legacy_full_refresh(legacy_stdio: StdioServerParameters) -> None:
    """REQ-CAT-001: a legacy Server's lists are fetched on ready too."""
    async with catalog_running(stdio_def(legacy_stdio, server_id="legacy")) as run:
        await run.ready("legacy")
        catalog = run.catalog
        assert "echo" in {t.name for t in catalog.tools("legacy")}
        assert {r.uri for r in catalog.resources("legacy")} >= {"fixture://hello"}
        assert [t.uri_template for t in catalog.resource_templates("legacy")] == [
            "fixture://items/{item}"
        ]
        assert [p.name for p in catalog.prompts("legacy")] == ["greet"]


@posix_only
async def test_killed_server_goes_stale_others_stay_fresh(
    modern_stdio: StdioServerParameters,
) -> None:
    """REQ-CAT-004: killing one Server marks its entries stale (still visible) while the
    other Server's entries stay fresh; the reconnect refresh makes them fresh again."""
    one, two = stdio_def(modern_stdio, server_id="one"), stdio_def(modern_stdio, server_id="two")
    async with catalog_running(one, two, rng=lambda: 0.5) as run:
        await run.ready("one", "two")
        pid = int(await run.call("one", "pid"))
        seen = len(run.observed.of("one"))
        os.kill(pid, signal.SIGKILL)
        await run.observed.until(lambda: any(t.stale for t in run.catalog.tools("one")))
        assert run.catalog.tools("one")
        assert not any(t.stale for t in run.catalog.tools("two"))
        await wait_state(run.supervisors["one"], S.READY)
        await run.observed.until(
            lambda: (
                len(run.observed.of("one")) >= seen + 2
                and not any(t.stale for t in run.catalog.tools("one"))
            )
        )


@pytest.fixture
def setup(third_party_files: Any, tmp_path: Path) -> Setup:
    return Setup(third_party_files, tmp_path / "data", {"PATH": "/usr/bin:/bin"})


async def test_host_wires_the_catalog(setup: Setup) -> None:
    """REQ-CAT-001, REQ-CAT-004: `Host.catalog` is filled on ready; removing the Server
    from the registry drops its entries."""
    host = setup.host(setup.config({"modern": MODERN_TABLE}))
    async with started(host):
        await host.connections.wait_ready("modern", timeout=TIMEOUT)
        with anyio.fail_after(TIMEOUT):
            while not host.catalog.tools("modern"):  # noqa: ASYNC110 - polled snapshot
                await anyio.sleep(0.02)
        assert host.catalog.get_tool("modern::echo").server_id == "modern"
        setup.write({})
        await host.registry.reload()
        assert host.catalog.tools("modern") == []
