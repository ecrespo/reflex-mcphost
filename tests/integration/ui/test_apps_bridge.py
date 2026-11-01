"""MCP App Views through the UI layer on a real Host (F7-05; API §7, TD Flow F; both eras).

A real Host with the Apps fixture Server (``apps_server.py``, modern and legacy stdio) and
the real ``ReflexPrompter`` over a fresh ``PromptRegistry``; one ``HostState`` stands in for
a tab, fed by its own watcher (``watch_host``, which also serves the tab's View). The
relay's side is scripted: the test sends the proxy / View messages through
``HostState.on_app_message`` and reads what the bridge sends from ``app_outbox``. MCP Apps
are enabled in ``HostRuntime`` with a placeholder proxy origin (no browser, no proxy
listener: F7-06b renders the real thing).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import apps_bridge, models
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.apps_bridge import ViewRegistry
from reflex_mcphost.ui.install import AppsStatus, HostRuntime
from reflex_mcphost.ui.prompter import PromptRegistry, ReflexPrompter
from reflex_mcphost.ui.state import HostState, WatcherRegistry, watch_host
from tests.integration.apps.test_bridge_fixture import HANDSHAKE, SERVER, apps_params, msg
from tests.integration.test_host import TIMEOUT, Setup, _toml_server, setup
from tests.integration.test_invoker import wait_tools, write_config
from tests.integration.ui.rig import Runtime, Tab
from tests.unit.ui.rig import published, redirect_target

pytestmark = pytest.mark.anyio
__all__ = ["apps_params", "setup"]  # re-exported fixtures
HUMAN = Consumer.human()
APP = Consumer.app(SERVER)
ORIGIN = "http://localhost:49999"


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[PromptRegistry]:
    registry = PromptRegistry()
    monkeypatch.setattr(prompter_mod, "PROMPTS", registry)
    yield registry
    assert registry.pending() == []


@pytest.fixture
def views(monkeypatch: pytest.MonkeyPatch) -> ViewRegistry:
    registry = ViewRegistry()
    monkeypatch.setattr(apps_bridge, "VIEWS", registry)
    return registry


@asynccontextmanager
async def ui_apps(
    setup: Setup, params: StdioServerParameters, prompts: PromptRegistry
) -> AsyncIterator[tuple[Host, Tab]]:
    """A started Host (``apps`` ready, MCP Apps declared), published with MCP Apps enabled,
    and one watched tab; ``human:ui`` may call ``get-time`` and ``probe`` (rules)."""
    table = _toml_server([params.command, *params.args])
    host = setup.host(write_config(setup, {SERVER: table}), prompter=ReflexPrompter(prompts))
    host.set_mcp_apps(True)
    with anyio.fail_after(TIMEOUT * 6):
        async with host:
            await wait_tools(host, SERVER)
            for tool in ("get-time", "probe"):
                await host.consent.grant(
                    effect="allow",
                    operation=OperationKind.TOOL_CALL,
                    target=ConsentTarget.tool(f"{SERVER}::{tool}"),
                    consumer=HUMAN,
                )
            await host.consent.grant(
                effect="deny",
                operation=OperationKind.TOOL_CALL,
                target=ConsentTarget.tool(f"{SERVER}::slow"),
                consumer=HUMAN,
            )
            tab = Tab()
            tab.state.tools = models.catalog_rows("tools", host.catalog.tools(SERVER))
            token = tab.state.router.session.client_token
            with published(host):
                HostRuntime._set_apps(AppsStatus(enabled=True, proxy_origin=ORIGIN))
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: watch_host(
                            token,
                            tab.commit,
                            runtime=Runtime(host),
                            watchers=WatcherRegistry(),
                            prompts=prompts,
                        )
                    )
                    await tab.until(lambda s: bool(s.servers))
                    yield host, tab
                    tg.cancel_scope.cancel()


def outbox(tab: Tab) -> list[dict[str, Any]]:
    return [json.loads(e["message"]) for e in tab.state.app_outbox]


def sent(tab: Tab, method: str) -> list[dict[str, Any]]:
    return [m for m in outbox(tab) if m.get("method") == method]


def reply(tab: Tab, msg_id: object) -> dict[str, Any] | None:
    return next((m for m in outbox(tab) if "method" not in m and m.get("id") == msg_id), None)


async def invoke(tab: Tab, tool: str, form: dict[str, Any] | None = None) -> None:
    tab.sync(HostState.select_entry, SERVER, f"{SERVER}::{tool}")
    await tab.run(HostState.invoke_tool, form or {})


async def rendered(tab: Tab, tool: str = "probe") -> str:
    """Invoke ``tool``; the View id once the frame shows it."""
    await invoke(tab, tool)
    await tab.until(lambda s: s.app_view.get("status") == "rendered")
    view_id: str = tab.state.app_view["view_id"]
    return view_id


async def handshake(tab: Tab, view_id: str) -> None:
    for raw in HANDSHAKE:
        await tab.call(HostState.on_app_message, view_id, raw)
    await tab.until(
        lambda s: bool(
            sent(tab, "ui/notifications/tool-result")
            or sent(tab, "ui/notifications/tool-cancelled")
        )
    )


async def answer_teardown(tab: Tab, view_id: str, reasons: list[str]) -> None:
    """Act as the View: answer the next ``ui/resource-teardown`` and record its reason."""
    await tab.until(lambda s: bool(sent(tab, "ui/resource-teardown")))
    [teardown] = sent(tab, "ui/resource-teardown")
    reasons.append(teardown["params"]["reason"])
    answer = json.dumps({"jsonrpc": "2.0", "id": teardown["id"], "result": {}})
    await tab.call(HostState.on_app_message, view_id, answer)


async def test_allowed_invoke_renders_denied_does_not(
    setup: Setup, apps_params: StdioServerParameters, prompts: PromptRegistry, views: ViewRegistry
) -> None:
    """REQ-APP-001, REQ-APP-003, REQ-APP-006 (both eras): an allowed UI invoke of
    ``get-time`` creates the tab's View, whose outbox gets ``sandbox-resource-ready`` only
    after a scripted ``sandbox-proxy-ready``, then the handshake; a denied invoke (``slow``)
    creates none and clears the frame."""
    async with ui_apps(setup, apps_params, prompts) as (_, tab):
        view_id = await rendered(tab, "get-time")
        token = tab.state.router.session.client_token
        assert views.get(token) is not None
        assert tab.state.app_view["src"] == f"{ORIGIN}/proxy?view={view_id}"
        assert tab.state.app_outbox == []
        await handshake(tab, view_id)
        methods = [m.get("method", "reply") for m in outbox(tab)]
        assert methods == [
            "ui/notifications/sandbox-resource-ready",
            "reply",
            "ui/notifications/tool-input",
            "ui/notifications/tool-result",
        ]
        assert "structuredContent" in sent(tab, "ui/notifications/tool-result")[0]["params"]
        reasons: list[str] = []
        async with anyio.create_task_group() as tg:
            tg.start_soon(answer_teardown, tab, view_id, reasons)
            await invoke(tab, "slow", {"seconds": "1"})
        assert tab.state.invocation["result"]["outcome"] == "denied"
        assert reasons == ["replaced"]
        assert views.get(token) is None
        assert tab.state.app_view == {}


async def test_app_tool_call_needs_human_consent(
    setup: Setup, apps_params: StdioServerParameters, prompts: PromptRegistry, views: ViewRegistry
) -> None:
    """REQ-APP-004, REQ-APP-008, Art. 3 (both eras): a View ``tools/call app_only_echo``
    raises a consent card naming ``app:apps``; approving it replies to the View with the
    ``CallToolResult``; the audit record names ``app:apps``. ``model_only_echo`` →
    ``-32602`` without a card."""
    async with ui_apps(setup, apps_params, prompts) as (host, tab):
        view_id = await rendered(tab)
        await handshake(tab, view_id)
        call = msg(11, "tools/call", {"name": "app_only_echo", "arguments": {"text": "hi"}})
        await tab.call(HostState.on_app_message, view_id, call)
        await tab.until(lambda s: len(s.cards) == 1)
        [card] = tab.state.cards
        assert (card["asking"], card["fqn"]) == ("app:apps", "apps::app_only_echo")
        HostState.answer_consent.fn(tab.state, card["id"], "allow_once", "tool", "once")
        await tab.until(lambda s: reply(tab, 11) is not None)
        assert reply(tab, 11)["result"] == {  # type: ignore[index]
            "content": [{"type": "text", "text": "hi"}],
            "isError": False,
        }
        hidden = msg(12, "tools/call", {"name": "model_only_echo", "arguments": {"text": "x"}})
        await tab.call(HostState.on_app_message, view_id, hidden)
        await tab.until(lambda s: reply(tab, 12) is not None)
        assert reply(tab, 12)["error"]["code"] == -32602  # type: ignore[index]
        assert prompts.pending() == []
        records = (await host.audit.query(AuditQuery(consumer=APP))).records
    assert sorted((str(r.fqn), r.outcome) for r in records) == [
        ("apps::app_only_echo", InvocationOutcome.success),
        ("apps::model_only_echo", InvocationOutcome.denied),
    ]


async def test_teardown_reasons_and_disconnect(
    setup: Setup, apps_params: StdioServerParameters, prompts: PromptRegistry, views: ViewRegistry
) -> None:
    """REQ-APP-010 (both eras): a second invoke replaces the View (``replaced``), Close
    sends ``closed``, Host shutdown ``host_closing``, removing the Server from the registry
    ``server_removed``; the watcher's cancellation (tab disconnect) drops the View without
    teardown."""
    async with ui_apps(setup, apps_params, prompts) as (host, tab):
        token = tab.state.router.session.client_token
        reasons: list[str] = []

        async def triggered(view_id: str, trigger: Any) -> None:
            await handshake(tab, view_id)
            async with anyio.create_task_group() as tg:
                tg.start_soon(answer_teardown, tab, view_id, reasons)
                await trigger()

        first = await rendered(tab)
        await triggered(first, lambda: rendered(tab))  # second invoke → replaced
        second = tab.state.app_view["view_id"]
        assert second != first
        await triggered(second, lambda: tab.run(HostState.close_app))
        await tab.until(lambda s: s.app_view == {})
        third = await rendered(tab)
        await triggered(third, lambda: views.close_all("host_closing"))
        await tab.until(lambda s: s.app_view == {})
        fourth = await rendered(tab)
        await triggered(fourth, lambda: host.registry.remove(SERVER))
        await tab.until(lambda s: s.app_view == {})
        assert views.get(token) is None
    assert reasons == ["replaced", "closed", "host_closing", "server_removed"]


async def test_tab_disconnect_and_open_link(
    setup: Setup, apps_params: StdioServerParameters, prompts: PromptRegistry, views: ViewRegistry
) -> None:
    """REQ-APP-015, REQ-APP-010 (both eras): ``ui/open-link`` approved → an external
    redirect to the full URL and ``{}``; declined → ``-32000``; the tab's watcher ending
    (disconnect) drops the View without ``ui/resource-teardown``."""
    url = "https://example.com/docs?x=1"
    async with ui_apps(setup, apps_params, prompts) as (_, tab):
        token = tab.state.router.session.client_token
        view_id = await rendered(tab)
        await handshake(tab, view_id)
        await tab.call(HostState.on_app_message, view_id, msg(21, "ui/open-link", {"url": url}))
        await tab.until(lambda s: s.app_view.get("link") == url)
        assert redirect_target(HostState.answer_app_link.fn(tab.state, True)) == (url, True)
        await tab.until(lambda s: reply(tab, 21) is not None)
        assert reply(tab, 21)["result"] == {}  # type: ignore[index]
        await tab.call(HostState.on_app_message, view_id, msg(22, "ui/open-link", {"url": url}))
        await tab.until(lambda s: s.app_view.get("link") == url)
        assert HostState.answer_app_link.fn(tab.state, False) is None
        await tab.until(lambda s: reply(tab, 22) is not None)
        assert reply(tab, 22)["error"]["code"] == -32000  # type: ignore[index]
        session = views.get(token)
        assert session is not None
    # leaving ui_apps cancelled the tab's watcher: the View was discarded, not torn down
    assert views.get(token) is None
    assert session.closed
    assert sent(tab, "ui/resource-teardown") == []
