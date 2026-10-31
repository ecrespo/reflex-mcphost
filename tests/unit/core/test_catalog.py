"""``Catalog`` snapshots against a fake Client (F2-11; API §4.6, Flow B steps 6-7)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import anyio
import mcp_types as types
import pytest
from mcp import Client, MCPError

from reflex_mcphost.core.catalog import (
    DEFAULT_VISIBILITY,
    Catalog,
    ToolEntry,
    definition_hash,
)
from reflex_mcphost.core.connection.state import Era, ServerState, ServerStatus
from reflex_mcphost.core.errors import UnknownEntryError, UnknownServerError
from reflex_mcphost.core.events import CatalogChanged, EventBus, HostEvent
from reflex_mcphost.core.hmac_key import canonical_json
from reflex_mcphost.core.types import Fqn

pytestmark = pytest.mark.anyio
TIMEOUT = 5.0
OBJ: dict[str, Any] = {"type": "object", "properties": {}}
ALL_CAPS = types.ServerCapabilities(
    tools=types.ToolsCapability(),
    resources=types.ResourcesCapability(),
    prompts=types.PromptsCapability(),
)


def tool(name: str, **kwargs: Any) -> types.Tool:
    kwargs.setdefault("input_schema", OBJ)
    return types.Tool(name=name, **kwargs)


class FakeClient:
    """The subset of ``mcp.Client`` the Catalog uses; lists are paginated by ``pages``."""

    def __init__(
        self,
        *,
        tools: Sequence[Sequence[types.Tool]] = ((),),
        resources: Sequence[types.Resource] = (),
        templates: Sequence[types.ResourceTemplate] = (),
        prompts: Sequence[types.Prompt] = (),
        caps: types.ServerCapabilities = ALL_CAPS,
        ttl_ms: int = 0,
        fail: frozenset[str] = frozenset(),
    ) -> None:
        self.tool_pages = [list(p) for p in tools]
        self.resources = list(resources)
        self.templates = list(templates)
        self.prompts = list(prompts)
        self.server_capabilities = caps
        self.ttl_ms = ttl_ms
        self.fail = fail
        self.calls: list[tuple[str, str | None]] = []

    def _check(self, method: str, cursor: str | None) -> None:
        self.calls.append((method, cursor))
        if method in self.fail:
            raise MCPError(types.INTERNAL_ERROR, f"{method} broke")

    async def list_tools(self, *, cursor: str | None = None, cache_mode: str = "use") -> Any:
        self._check("tools/list", cursor)
        index = int(cursor[1:]) if cursor else 0
        nxt = f"p{index + 1}" if index + 1 < len(self.tool_pages) else None
        return types.ListToolsResult(
            tools=self.tool_pages[index], next_cursor=nxt, ttl_ms=self.ttl_ms
        )

    async def list_resources(self, *, cursor: str | None = None, cache_mode: str = "use") -> Any:
        self._check("resources/list", cursor)
        return types.ListResourcesResult(resources=self.resources, ttl_ms=self.ttl_ms)

    async def list_resource_templates(
        self, *, cursor: str | None = None, cache_mode: str = "use"
    ) -> Any:
        self._check("resources/templates/list", cursor)
        return types.ListResourceTemplatesResult(
            resource_templates=self.templates, ttl_ms=self.ttl_ms
        )

    async def list_prompts(self, *, cursor: str | None = None, cache_mode: str = "use") -> Any:
        self._check("prompts/list", cursor)
        return types.ListPromptsResult(prompts=self.prompts, ttl_ms=self.ttl_ms)


def status(server_id: str, *, era: Era = "legacy", caps: Any = ALL_CAPS) -> ServerStatus:
    return ServerStatus(
        server_id=server_id,
        state=ServerState.READY,
        since=datetime.now(UTC),
        era=era,
        protocol_version="2025-11-25" if era == "legacy" else "2026-07-28",
        capabilities=caps,
    )


def _no_supervisor(server_id: str) -> Any:
    raise UnknownServerError(server_id)


class Changes:
    def __init__(self) -> None:
        self.events: list[CatalogChanged] = []
        self._changed = anyio.Event()

    def add(self, event: HostEvent) -> None:
        if isinstance(event, CatalogChanged):
            self.events.append(event)
            self._changed.set()
            self._changed = anyio.Event()

    async def wait(self, count: int) -> None:
        with anyio.fail_after(TIMEOUT):
            while len(self.events) < count:
                await self._changed.wait()


@asynccontextmanager
async def harness(**kwargs: Any) -> AsyncIterator[tuple[Catalog, Changes, anyio.abc.TaskGroup]]:
    bus = EventBus()
    catalog = Catalog(events=bus, supervisor_of=_no_supervisor, **kwargs)
    changes = Changes()
    async with anyio.create_task_group() as tg, bus.subscribe(buffer=1024) as events:

        async def collect() -> None:
            async for event in events:
                changes.add(event)

        tg.start_soon(collect)
        yield catalog, changes, tg
        tg.cancel_scope.cancel()


async def serve(
    catalog: Catalog, changes: Changes, tg: anyio.abc.TaskGroup, client: FakeClient, st: Any
) -> anyio.CancelScope:
    """Run ``on_ready`` until cancelled; returns once the first refresh was published."""
    scope = anyio.CancelScope()
    before = len(changes.events)

    async def run() -> None:
        with scope:
            await catalog.on_ready(_as_client(client), st)

    tg.start_soon(run)
    await changes.wait(before + 1)
    return scope


def _as_client(fake: FakeClient) -> Client:
    return fake  # type: ignore[return-value]


async def test_full_refresh_follows_cursors_to_the_end() -> None:
    """REQ-CAT-001: on ready, every declared list is fetched, following pagination cursors
    to the end (a 3-page tools listing yields all entries)."""
    pages = [[tool("c"), tool("a")], [tool("e")], [tool("b"), tool("d")]]
    client = FakeClient(
        tools=pages,
        resources=[types.Resource(uri="x://r", name="r")],
        templates=[types.ResourceTemplate(uri_template="x://t/{id}", name="t")],
        prompts=[types.Prompt(name="p")],
    )
    async with harness() as (catalog, changes, tg):
        await serve(catalog, changes, tg, client, status("srv"))
        assert [t.name for t in catalog.tools()] == ["a", "b", "c", "d", "e"]
        assert [r.uri for r in catalog.resources()] == ["x://r"]
        assert [t.uri_template for t in catalog.resource_templates()] == ["x://t/{id}"]
        assert [p.name for p in catalog.prompts()] == ["p"]
        assert ("tools/list", "p1") in client.calls
        assert ("tools/list", "p2") in client.calls
        (event,) = changes.events
        assert event.server_id == "srv"
        assert set(event.lists) == {"tools", "resources", "resource_templates", "prompts"}


async def test_only_declared_lists_are_fetched() -> None:
    """REQ-CAT-001: lists the Server did not declare are not requested."""
    caps = types.ServerCapabilities(tools=types.ToolsCapability())
    client = FakeClient(tools=[[tool("a")]], caps=caps)
    async with harness() as (catalog, changes, tg):
        await serve(catalog, changes, tg, client, status("srv", caps=caps))
        assert {m for m, _ in client.calls} == {"tools/list"}
        assert changes.events[0].lists == ("tools",)


async def test_one_failing_list_keeps_the_others(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-CAT-001: a list request that errors is logged; the other lists still load."""
    client = FakeClient(
        tools=[[tool("a")]], prompts=[types.Prompt(name="p")], fail=frozenset({"resources/list"})
    )
    with caplog.at_level(logging.WARNING):
        async with harness() as (catalog, changes, tg):
            await serve(catalog, changes, tg, client, status("srv"))
            assert [t.name for t in catalog.tools()] == ["a"]
            assert [p.name for p in catalog.prompts()] == ["p"]
            assert catalog.resources() == []
    assert any("resources" in r.getMessage() for r in caplog.records)


def test_definition_hash_is_canonical() -> None:
    """REQ-CAT-009: definition_hash = sha256 of canonical JSON of {name, title, description,
    inputSchema, outputSchema, annotations}; stable under key reordering, sensitive to the
    description."""
    schema_a = {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "number"}}}
    schema_b = {"properties": {"y": {"type": "number"}, "x": {"type": "string"}}, "type": "object"}
    annotations = types.ToolAnnotations(title="T", read_only_hint=True)
    a = tool("t", description="d", input_schema=schema_a, annotations=annotations)
    b = tool("t", description="d", input_schema=schema_b, annotations=annotations)
    assert definition_hash(a) == definition_hash(b)
    assert definition_hash(a) != definition_hash(
        tool("t", description="e", input_schema=schema_a, annotations=annotations)
    )
    material = {
        "name": "t",
        "title": None,
        "description": "d",
        "inputSchema": schema_a,
        "outputSchema": None,
        "annotations": {"title": "T", "readOnlyHint": True},
    }
    expected = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    assert definition_hash(a) == "sha256:" + expected


async def test_duplicate_tool_names_keep_the_first(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-CAT-008: two tools with the same name → the first is kept, the duplicate dropped,
    and a warning names the Server and the tool."""
    first = tool("dup", description="first")
    client = FakeClient(tools=[[first, tool("dup", description="second")], [tool("ok")]])
    with caplog.at_level(logging.WARNING):
        async with harness() as (catalog, changes, tg):
            await serve(catalog, changes, tg, client, status("srv"))
            dup = catalog.get_tool("srv::dup")
            assert dup.description == "first"
            assert [t.name for t in catalog.tools()] == ["dup", "ok"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'srv'" in m and "'dup'" in m for m in warnings), warnings


async def test_ui_resource_uri_and_visibility() -> None:
    """REQ-CAT-014: `_meta.ui.resourceUri` and `_meta.ui.visibility` are recorded; the
    deprecated flat `_meta["ui/resourceUri"]` is the fallback; visibility defaults to
    ("model", "app") and an invalid value falls back to the default."""
    tools = [
        tool("app", _meta={"ui": {"resourceUri": "ui://a", "visibility": ["app"]}}),
        tool("flat", _meta={"ui/resourceUri": "ui://flat"}),
        tool("both", _meta={"ui": {"resourceUri": "ui://new"}, "ui/resourceUri": "ui://old"}),
        tool("plain"),
        tool("bad", _meta={"ui": {"resourceUri": 7, "visibility": "model"}}),
        tool("hidden", _meta={"ui": {"visibility": []}}),
    ]
    async with harness() as (catalog, changes, tg):
        await serve(catalog, changes, tg, FakeClient(tools=[tools]), status("s"))
        by = {t.name: t for t in catalog.tools()}
    assert (by["app"].ui_resource_uri, by["app"].visibility) == ("ui://a", ("app",))
    assert (by["flat"].ui_resource_uri, by["flat"].visibility) == ("ui://flat", DEFAULT_VISIBILITY)
    assert by["both"].ui_resource_uri == "ui://new"
    assert (by["plain"].ui_resource_uri, by["plain"].visibility) == (None, ("model", "app"))
    assert (by["bad"].ui_resource_uri, by["bad"].visibility) == (None, DEFAULT_VISIBILITY)
    assert by["hidden"].visibility == ()


async def test_fqn_identity_and_ordering() -> None:
    """REQ-CAT-002: tools, templates and prompts are identified by `server_id::name`,
    resources by (server_id, uri); lists are ordered by server_id, then name."""
    async with harness() as (catalog, changes, tg):
        await serve(catalog, changes, tg, FakeClient(tools=[[tool("z"), tool("y")]]), status("b"))
        client_a = FakeClient(
            tools=[[tool("x")]],
            resources=[
                types.Resource(uri="x://2", name="m"),
                types.Resource(uri="x://1", name="m"),
            ],
            templates=[types.ResourceTemplate(uri_template="x://{v}", name="tpl")],
            prompts=[types.Prompt(name="p")],
        )
        await serve(catalog, changes, tg, client_a, status("a"))
        assert [str(t.fqn) for t in catalog.tools()] == ["a::x", "b::y", "b::z"]
        assert [t.name for t in catalog.tools("b")] == ["y", "z"]
        assert catalog.tools("nope") == []
        assert catalog.get_tool(Fqn(server_id="b", name="y")).server_id == "b"
        assert catalog.get_tool("a::x").fqn == Fqn.parse("a::x")
        assert [(r.server_id, r.uri) for r in catalog.resources()] == [
            ("a", "x://1"),
            ("a", "x://2"),
        ]
        assert str(catalog.resource_templates()[0].fqn) == "a::tpl"
        assert str(catalog.prompts("a")[0].fqn) == "a::p"
        with pytest.raises(UnknownEntryError) as info:
            catalog.get_tool("a::missing")
        assert info.value.fqn == "a::missing"
        with pytest.raises(UnknownEntryError):
            catalog.get_tool("zz::x")


async def test_entries_go_stale_when_ready_ends_and_refresh_replaces_them() -> None:
    """REQ-CAT-004: when a Server leaves ready its entries stay visible with stale=True
    (another Server's stay fresh); the next ready replaces them with fresh ones."""
    async with harness() as (catalog, changes, tg):
        scope = await serve(catalog, changes, tg, FakeClient(tools=[[tool("a")]]), status("one"))
        await serve(catalog, changes, tg, FakeClient(tools=[[tool("b")]]), status("two"))
        scope.cancel()
        await changes.wait(3)
        assert [(t.server_id, t.stale) for t in catalog.tools()] == [("one", True), ("two", False)]
        assert changes.events[-1].server_id == "one"
        await serve(catalog, changes, tg, FakeClient(tools=[[tool("a2")]]), status("one"))
        assert [(t.name, t.stale) for t in catalog.tools("one")] == [("a2", False)]
        catalog.forget("one")
        assert catalog.tools("one") == []


async def test_annotations_are_stored_as_given() -> None:
    """REQ-CAT-011: annotations are untrusted display data, stored verbatim (SDK type)."""
    annotations = types.ToolAnnotations(destructive_hint=False, read_only_hint=True)
    async with harness() as (catalog, changes, tg):
        await serve(
            catalog,
            changes,
            tg,
            FakeClient(tools=[[tool("t", annotations=annotations)]]),
            status("s"),
        )
        entry = catalog.get_tool("s::t")
    assert isinstance(entry, ToolEntry)
    assert entry.annotations == annotations
