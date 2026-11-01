"""``ui.apps_bridge``: per-tab Views, App calls, link confirmation and teardown with a fake
Host (F7-05; API §7, TD Flow F; REQ-APP-001/004/010/015, Art. 3)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.catalog import ResourceContents
from reflex_mcphost.core.config.models import RegistryDiff
from reflex_mcphost.core.consent.outcome import ConsentOutcome
from reflex_mcphost.core.events import EventBus, RegistryChanged
from reflex_mcphost.core.invoker import InvocationResult
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import apps_bridge
from reflex_mcphost.ui.apps_bridge import ViewRegistry, render_view
from reflex_mcphost.ui.install import AppsStatus
from reflex_mcphost.ui.state import HostState
from tests.unit.ui.rig import published, redirect_target
from tests.unit.ui.test_app_models import ui_tool

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 1, tzinfo=UTC)
TOKEN = "tab-1"  # noqa: S105 - a client id, not a secret
ORIGIN = "http://localhost:4321"
APPS = AppsStatus(enabled=True, proxy_origin=ORIGIN)
HTML = "<!doctype html><p>view</p>"
WAIT = 5.0


def result(
    outcome: InvocationOutcome = InvocationOutcome.success,
    consumer: Consumer | None = None,
    allowed: bool = True,
    fqn: str = "srv::view",
) -> InvocationResult:
    return InvocationResult(
        id="inv-1",
        fqn=Fqn.parse(fqn),
        consumer=consumer or Consumer.human(),
        outcome=outcome,
        content=(types.TextContent(type="text", text="done"),),
        consent=ConsentOutcome(allowed=allowed, source="prompt"),
        started_at=NOW,
        duration_ms=1,
    )


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], Consumer]] = []

    async def call(self, fqn: str, arguments: dict[str, Any], *, consumer: Consumer) -> Any:
        self.calls.append((fqn, arguments, consumer))
        return result(fqn=fqn, consumer=consumer)


class FakeCatalog:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str, Consumer]] = []

    async def read_resource(self, server_id: str, uri: str, *, consumer: Consumer) -> Any:
        self.reads.append((server_id, uri, consumer))
        item = types.TextResourceContents(uri=uri, mime_type="text/html;profile=mcp-app", text=HTML)
        return ResourceContents(server_id=server_id, uri=uri, contents=(item,))

    def get_tool(self, fqn: str) -> Any:
        return ui_tool()


class FakeConnections:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def append_log(self, server_id: str, entry: Any) -> None:
        self.logs.append((server_id, entry.text))


class FakeHost:
    def __init__(self) -> None:
        self.invoker = FakeInvoker()
        self.catalog = FakeCatalog()
        self.connections = FakeConnections()
        self.events = EventBus()


class TabState:
    """The tab's ``app_view`` / ``app_outbox`` fields, with change notification."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}
        self.commits = 0
        self._changed = anyio.Event()

    async def commit(self, fields: dict[str, Any]) -> None:
        self.fields.update(fields)
        self.commits += 1
        self._changed.set()
        self._changed = anyio.Event()

    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(e["message"]) for e in self.fields.get("app_outbox", [])]

    async def until(self, predicate: Callable[[TabState], bool]) -> None:
        with anyio.fail_after(WAIT):
            while not predicate(self):
                await self._changed.wait()


def msg(msg_id: object | None, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    if params is not None:
        message["params"] = params
    return json.dumps(message)


HANDSHAKE = (
    msg(None, "ui/notifications/sandbox-proxy-ready"),
    msg(
        "init",
        "ui/initialize",
        {"protocolVersion": "2026-01-26", "appInfo": {}, "appCapabilities": {}},
    ),
    msg(None, "ui/notifications/initialized"),
)


def sent(tab: TabState, method: str) -> list[dict[str, Any]]:
    return [m for m in tab.messages() if m.get("method") == method]


def reply(tab: TabState, msg_id: object) -> dict[str, Any] | None:
    return next((m for m in tab.messages() if "method" not in m and m.get("id") == msg_id), None)


@asynccontextmanager
async def served(
    host: FakeHost, registry: ViewRegistry, tab: TabState, token: str = TOKEN
) -> AsyncIterator[None]:
    async with anyio.create_task_group() as tg:
        tg.start_soon(registry.serve_tab, token, tab.commit, host)
        yield
        tg.cancel_scope.cancel()


async def opened(host: FakeHost, registry: ViewRegistry, tab: TabState) -> apps_bridge.ViewSession:
    session = await render_view(
        host, TOKEN, ui_tool(), {"q": 1}, result(), tab.commit, apps=APPS, registry=registry
    )
    assert session is not None
    await tab.until(lambda t: t.fields.get("app_view", {}).get("view_id") == session.view_id)
    return session


async def handshake(registry: ViewRegistry, tab: TabState, view_id: str) -> None:
    for raw in HANDSHAKE:
        assert await registry.receive(TOKEN, view_id, raw)
    await tab.until(lambda t: bool(sent(t, "ui/notifications/tool-result")))


async def test_render_only_for_allowed_ui_invocations() -> None:
    """REQ-APP-001 (delta D2): a denied, agent or non-UI-tool invocation creates no View
    and commits nothing."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    cases = [
        (ui_tool(), result(allowed=False)),
        (ui_tool(), result(consumer=Consumer.agent("helper"))),
        (ui_tool(None), result()),
        (ui_tool(), result(InvocationOutcome.denied)),
    ]
    for entry, res in cases:
        assert (
            await render_view(host, TOKEN, entry, {}, res, tab.commit, apps=APPS, registry=registry)
            is None
        )
    assert tab.commits == 0
    assert registry.get(TOKEN) is None
    assert host.catalog.reads == []


async def test_disabled_apps_show_the_reason() -> None:
    """REQ-APP-001, DD-012: with MCP Apps disabled the frame names the reason; no read."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    disabled = AppsStatus(enabled=False, reason="localhost_not_loopback")
    assert (
        await render_view(
            host, TOKEN, ui_tool(), {}, result(), tab.commit, apps=disabled, registry=registry
        )
        is None
    )
    assert tab.fields["app_view"]["notices"] == ["MCP Apps are disabled: localhost_not_loopback"]
    assert host.catalog.reads == []


async def test_view_handshake_through_the_outbox() -> None:
    """REQ-APP-001, REQ-APP-003, delta D4: the View's outbox gets
    ``sandbox-resource-ready`` only after ``sandbox-proxy-ready``; acknowledged entries
    are dropped; the frame carries the proxy URL; the resource was read as ``human:ui``."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        view = tab.fields["app_view"]
        assert view["src"] == f"{ORIGIN}/proxy?view={session.view_id}"
        assert view["origin"] == ORIGIN
        assert tab.fields["app_outbox"] == []
        assert host.catalog.reads == [("srv", "ui://view", Consumer.human())]
        assert not await registry.receive(TOKEN, "other-view", HANDSHAKE[0])
        assert not await registry.receive("other-tab", session.view_id, HANDSHAKE[0])
        assert tab.fields["app_outbox"] == []
        await handshake(registry, tab, session.view_id)
        [ready] = sent(tab, "ui/notifications/sandbox-resource-ready")
        assert ready["params"]["html"] == HTML
        assert sent(tab, "ui/notifications/tool-input")[0]["params"] == {"arguments": {"q": 1}}
        last = tab.fields["app_outbox"][-1]["seq"]
        registry.ack(TOKEN, session.view_id, last)
        await tab.until(lambda t: t.fields["app_outbox"] == [])


async def test_app_tool_call_goes_through_the_invoker_as_the_app() -> None:
    """REQ-APP-004, Art. 3: a View ``tools/call`` is ``Invoker.call("srv::<name>", …,
    consumer=app:srv)``; the log port records ``method → outcome`` only."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        await handshake(registry, tab, session.view_id)
        await registry.receive(
            TOKEN,
            session.view_id,
            msg(5, "tools/call", {"name": "echo", "arguments": {"t": "secret-arg"}}),
        )
        await tab.until(lambda t: reply(t, 5) is not None)
    assert host.invoker.calls == [("srv::echo", {"t": "secret-arg"}, Consumer.app("srv"))]
    assert ("srv", "tools/call → success") in host.connections.logs
    assert all("secret-arg" not in text for _, text in host.connections.logs)


async def test_open_link_approve_and_decline() -> None:
    """REQ-APP-015: ``ui/open-link`` shows the full URL; approve → the URL to open and
    ``{}``; decline → ``-32000``; nothing pending → ``None``."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    url = "https://example.com/path?q=1"
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        await handshake(registry, tab, session.view_id)
        assert registry.answer_link(TOKEN, True) is None
        await registry.receive(TOKEN, session.view_id, msg(6, "ui/open-link", {"url": url}))
        await tab.until(lambda t: t.fields["app_view"]["link"] == url)
        assert registry.answer_link(TOKEN, True) == url
        await tab.until(lambda t: reply(t, 6) is not None)
        assert reply(tab, 6) == {"jsonrpc": "2.0", "id": 6, "result": {}}
        assert tab.fields["app_view"]["link"] == ""
        await registry.receive(TOKEN, session.view_id, msg(7, "ui/open-link", {"url": url}))
        await tab.until(lambda t: t.fields["app_view"]["link"] == url)
        assert registry.answer_link(TOKEN, False) is None
        await tab.until(lambda t: reply(t, 7) is not None)
        assert reply(tab, 7)["error"]["code"] == -32000  # type: ignore[index]
        await registry.receive(
            TOKEN, session.view_id, msg(8, "ui/open-link", {"url": "javascript:x"})
        )
        await tab.until(lambda t: reply(t, 8) is not None)
        assert reply(tab, 8)["error"]["code"] == -32000  # type: ignore[index]


async def test_second_view_replaces_the_first() -> None:
    """REQ-APP-010: a new View in the tab tears the previous one down with ``replaced``
    (the teardown request reaches the outbox; the View's answer ends the wait)."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        first = await opened(host, registry, tab)
        await handshake(registry, tab, first.view_id)

        async def answer_teardown() -> None:
            await tab.until(lambda t: bool(sent(t, "ui/resource-teardown")))
            [teardown] = sent(tab, "ui/resource-teardown")
            assert teardown["params"] == {"reason": "replaced"}
            reply_ = json.dumps({"jsonrpc": "2.0", "id": teardown["id"], "result": {}})
            assert await registry.receive(TOKEN, first.view_id, reply_)

        with anyio.fail_after(1.5):  # answered: well under the 2 s teardown wait
            async with anyio.create_task_group() as tg:
                tg.start_soon(answer_teardown)
                second = await opened(host, registry, tab)
        assert first.closed
        assert registry.get(TOKEN) is second
        assert tab.fields["app_outbox"] == []


@pytest.mark.parametrize("reason", ["closed", "host_closing"])
async def test_close_and_close_all(reason: str) -> None:
    """REQ-APP-010: closing the panel (``closed``) or the Host (``host_closing``, ≤ 2 s
    overall) sends ``ui/resource-teardown {reason}`` and clears the frame."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        await handshake(registry, tab, session.view_id)
        seen: list[dict[str, Any]] = []

        async def watch() -> None:
            await tab.until(lambda t: bool(sent(t, "ui/resource-teardown")))
            seen.extend(sent(tab, "ui/resource-teardown"))

        with anyio.fail_after(3.5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(watch)
                if reason == "closed":
                    assert await registry.close(TOKEN, "closed")
                else:
                    await registry.close_all("host_closing", timeout_s=0.5)
        await tab.until(lambda t: t.fields["app_view"] == {})
    assert [m["params"] for m in seen] == [{"reason": reason}]
    assert registry.get(TOKEN) is None
    assert not await registry.close(TOKEN, "closed")


async def test_server_removal_tears_the_view_down() -> None:
    """REQ-APP-010: ``registry.changed`` removing the View's Server → ``server_removed``."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        await handshake(registry, tab, session.view_id)
        host.events.publish(RegistryChanged(diff=RegistryDiff(removed=("other",))))
        host.events.publish(RegistryChanged(diff=RegistryDiff(removed=("srv",))))
        await tab.until(lambda t: bool(sent(t, "ui/resource-teardown")))
        assert sent(tab, "ui/resource-teardown")[0]["params"] == {"reason": "server_removed"}
        await tab.until(lambda t: t.fields["app_view"] == {})
    assert registry.get(TOKEN) is None


async def test_tab_disconnect_drops_without_teardown() -> None:
    """REQ-APP-010 (API §7): the watcher's cancellation (tab disconnect) discards the View
    without ``ui/resource-teardown``."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    async with served(host, registry, tab):
        session = await opened(host, registry, tab)
        await handshake(registry, tab, session.view_id)
    assert registry.get(TOKEN) is None
    assert sent(tab, "ui/resource-teardown") == []
    assert session.closed
    assert not await registry.receive(TOKEN, session.view_id, HANDSHAKE[0])
    assert registry._arrived == {}  # nothing bound to this event loop stays behind


async def test_unserved_view_closes_without_hanging() -> None:
    """REQ-APP-010: a View that no watcher runs (no ``on_load``) closes at once."""
    host, registry, tab = FakeHost(), ViewRegistry(), TabState()
    await render_view(
        host, TOKEN, ui_tool(), {}, result(), tab.commit, apps=APPS, registry=registry
    )
    with anyio.fail_after(1):
        assert await registry.close(TOKEN, "closed")


async def test_state_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-APP-015, REQ-APP-010 (``HostState``): ``answer_app_link`` turns an approved
    link into ``rx.redirect(url, is_external=True)`` (opened with ``noopener``) and a
    decline into nothing; ``on_app_message`` / ``on_app_ack`` route by the tab's token."""
    registry = ViewRegistry()
    monkeypatch.setattr(apps_bridge, "VIEWS", registry)
    state = HostState(_reflex_internal_init=True)
    token = state.router.session.client_token
    host, tab = FakeHost(), TabState()
    url = "https://example.com/"
    with published(host):
        async with served(host, registry, tab, token):
            session = await render_view(
                host, token, ui_tool(), {}, result(), tab.commit, apps=APPS, registry=registry
            )
            assert session is not None
            await tab.until(
                lambda t: t.fields.get("app_view", {}).get("view_id") == session.view_id
            )
            for raw in HANDSHAKE:
                await HostState.on_app_message.fn(state, session.view_id, raw)
            await tab.until(lambda t: bool(sent(t, "ui/notifications/tool-result")))
            HostState.on_app_ack.fn(state, session.view_id, tab.fields["app_outbox"][-1]["seq"])
            await tab.until(lambda t: t.fields["app_outbox"] == [])
            assert HostState.answer_app_link.fn(state, True) is None
            await HostState.on_app_message.fn(
                state, session.view_id, msg(1, "ui/open-link", {"url": url})
            )
            await tab.until(lambda t: t.fields["app_view"]["link"] == url)
            assert redirect_target(HostState.answer_app_link.fn(state, True)) == (url, True)
            await HostState.on_app_message.fn(
                state, session.view_id, msg(2, "ui/open-link", {"url": url})
            )
            await tab.until(lambda t: t.fields["app_view"]["link"] == url)
            assert HostState.answer_app_link.fn(state, False) is None
            assert HostState.select_entry.fn(state, "srv", "srv::view") is HostState.close_app
