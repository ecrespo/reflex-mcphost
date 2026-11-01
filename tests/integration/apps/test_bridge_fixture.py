"""The core ``AppBridge`` on a real Host with the MCP Apps fixture Server (F7-06a).

Both eras over stdio: ``apps_server.py`` on mcp 2.3 (modern) and on mcp 1.x (legacy,
marker ``interop``). The bridge's ports are bound to the real Host exactly as the UI adapter
binds them (API §7): ``invoke_tool`` → ``Invoker.call("apps::<name>", …,
consumer=Consumer.app("apps"))``, ``read_resource`` → ``catalog.read_resource`` of the same
Server, ``log`` → ``connections.append_log``. View messages are scripted (no browser).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.apps.bridge import AppBridge, AppPorts, AppSeed
from reflex_mcphost.core.apps.csp import build_csp
from reflex_mcphost.core.apps.resource import NotRendered, ViewResource, select_view_html
from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome, InvocationResult
from reflex_mcphost.core.types import Consumer
from tests.integration.test_host import TIMEOUT, Setup, _toml_server, setup
from tests.integration.test_invoker import FakePrompter, wait_tools, write_config

pytestmark = pytest.mark.anyio
__all__ = ["setup"]
SERVER = "apps"
APP = Consumer.app(SERVER)
HUMAN = Consumer.human()
APPS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "apps"
VENDORED = APPS_DIR / "basic-vanillajs-1.0.1.mcp-app.html"
VENDORED_SHA256 = "076c3926f3fbf6e81f7cd8b9ebf428f1c6d9b0a37f9a4e4cf0c2f0e9aaff923c"
"""``dist/mcp-app.html`` of ``@modelcontextprotocol/server-basic-vanillajs@1.0.1`` (npm
shasum ``b693334d2febcbaf8377dd8f48eab5009312dcfa``; spike S-3)."""
Out = InvocationOutcome


@dataclass
class ScriptedView:
    """The proxy/View side: records what the bridge sends; answers link confirmations."""

    approve_links: bool = False
    sent: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    _changed: anyio.Event = field(default_factory=anyio.Event)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        self._changed.set()
        self._changed = anyio.Event()

    async def confirm_link(self, url: str) -> bool:
        self.links.append(url)
        return self.approve_links

    async def reply(self, msg_id: object) -> dict[str, Any]:
        with anyio.fail_after(TIMEOUT):
            while True:
                for message in self.sent:
                    if "method" not in message and message.get("id") == msg_id:
                        return message
                await self._changed.wait()


def host_ports(host: Host, view: ScriptedView, server_id: str = SERVER) -> AppPorts:
    """Ports bound to a real Host as API §7 / F7-05 bind them."""
    consumer = Consumer.app(server_id)

    async def invoke_tool(name: str, arguments: dict[str, Any]) -> InvocationResult:
        return await host.invoker.call(f"{server_id}::{name}", arguments, consumer=consumer)

    async def read_resource(uri: str) -> Any:
        return await host.catalog.read_resource(server_id, uri, consumer=consumer)

    return AppPorts(
        invoke_tool=invoke_tool,
        read_resource=read_resource,
        confirm_link=view.confirm_link,
        log=lambda entry: host.connections.append_log(server_id, entry),
        send=view.send,
    )


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
        {
            "protocolVersion": "2026-01-26",
            "appInfo": {"name": "v", "version": "1"},
            "appCapabilities": {},
        },
    ),
    msg(None, "ui/notifications/initialized"),
)


@asynccontextmanager
async def apps_host(
    setup: Setup, params: StdioServerParameters, *, mcp_apps: bool = True
) -> AsyncIterator[Host]:
    """A started Host with the fixture Server as ``apps`` (an allow-once human prompter)."""
    table = _toml_server([params.command, *params.args])
    host = setup.host(write_config(setup, {SERVER: table}), prompter=FakePrompter())
    host.set_mcp_apps(mcp_apps)
    with anyio.fail_after(TIMEOUT * 6):
        async with host:
            await wait_tools(host, SERVER)
            yield host


@asynccontextmanager
async def open_bridge(
    host: Host, view: ScriptedView, uri: str = "ui://probe", tool: str = "probe"
) -> AsyncIterator[AppBridge]:
    """Flow F steps 1-6: a UI invocation, the resource read, the CSP and the handshake."""
    arguments = {"q": "from-ui"}
    seed = await host.invoker.call(f"{SERVER}::{tool}", arguments, consumer=HUMAN)
    assert seed.ok, seed.error
    view_resource = select_view_html(await host.catalog.read_resource(SERVER, uri), uri)
    assert isinstance(view_resource, ViewResource)
    bridge = AppBridge(
        server_id=SERVER,
        view_id="view-1",
        seed=AppSeed(arguments=arguments, result=seed),
        html=view_resource.html,
        host_info={"name": "reflex-mcphost", "version": "test"},
        host_context={"theme": "light"},
        csp=build_csp(view_resource.csp_meta),
        ports=host_ports(host, view),
    )
    async with bridge:
        for raw in HANDSHAKE:
            await bridge.receive(raw)
        yield bridge


@pytest.fixture(params=["modern", "legacy"])
def apps_params(request: pytest.FixtureRequest) -> StdioServerParameters:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("apps_legacy_stdio")
        return params
    modern: StdioServerParameters = request.getfixturevalue("apps_modern_stdio")
    return modern


def test_vendored_view_hash() -> None:
    """REQ-APP-001 (Art. 7, spike S-3): the vendored reference View is byte-identical to the
    published ``1.0.1`` build."""
    assert hashlib.sha256(VENDORED.read_bytes()).hexdigest() == VENDORED_SHA256
    notice = (APPS_DIR / "NOTICE.md").read_text(encoding="utf-8")
    assert "MIT" in notice
    assert "server-basic-vanillajs@1.0.1" in notice


async def test_catalog_carries_ui_metadata(
    setup: Setup, apps_params: StdioServerParameters
) -> None:
    """REQ-APP-001, REQ-APP-008 (REQ-CAT-014): on both eras the Catalog entries carry
    ``ui_resource_uri`` and ``visibility``."""
    async with apps_host(setup, apps_params) as host:
        tools = {t.name: t for t in host.catalog.tools(SERVER)}
    assert tools["get-time"].ui_resource_uri == "ui://basic"
    assert tools["probe"].ui_resource_uri == "ui://probe"
    assert tools["app_only_echo"].visibility == ("app",)
    assert tools["model_only_echo"].visibility == ("model",)
    assert tools["slow"].visibility == ("model", "app")


async def test_resource_selection_and_reasons(
    setup: Setup, apps_params: StdioServerParameters
) -> None:
    """REQ-APP-001, REQ-APP-013 (API §7): ``ui://basic`` is the vendored View; each bad
    resource gives its reason."""
    async with apps_host(setup, apps_params) as host:

        async def select(uri: str) -> ViewResource | NotRendered:
            return select_view_html(await host.catalog.read_resource(SERVER, uri), uri)

        basic = await select("ui://basic")
        assert isinstance(basic, ViewResource)
        assert basic.html == VENDORED.read_text(encoding="utf-8")
        assert await select("ui://missing") == NotRendered("missing_content")
        assert await select("ui://wrong-mime") == NotRendered("mime_mismatch")
        assert await select("ui://bad-utf8") == NotRendered("invalid_encoding")
        assert await select("ui://too-big") == NotRendered("too_large")
        external = await select("ui://external")
        assert isinstance(external, ViewResource)
        csp = build_csp(external.csp_meta)
        assert csp.external is True
        assert csp.dropped == ()


async def test_bad_csp_dropped_and_logged(setup: Setup, apps_params: StdioServerParameters) -> None:
    """REQ-APP-014, REQ-APP-012: ``ui://bad-csp``'s invalid entries are left out of the
    policy and recorded in the Server's log stream when the bridge starts."""
    view = ScriptedView()
    async with apps_host(setup, apps_params) as host, open_bridge(host, view, "ui://bad-csp"):
        logs = [e.text for e in host.connections.logs(SERVER) if e.logger == "mcp-app"]
        (ready,) = [
            m for m in view.sent if m.get("method") == "ui/notifications/sandbox-resource-ready"
        ]
    policy = ready["params"]["csp"]
    assert "https://cdn.example.com" in policy
    assert "connect-src wss://rt.example.com;" in policy
    bad_entries = (
        "*",
        "https:",
        "'unsafe-eval'",
        "https://127.0.0.1",
        "https://x.example;script-src *",
        "http://api.example.com",
        "https://localhost",
    )
    for bad in bad_entries:
        assert any(t.startswith("csp → dropped") and t.endswith(f"entry {bad}") for t in logs), bad
    assert "'unsafe-eval'" not in policy
    assert "127.0.0.1" not in policy
    assert "localhost" not in policy


async def test_app_tool_call_through_the_invoker(
    setup: Setup, apps_params: StdioServerParameters
) -> None:
    """REQ-APP-004, REQ-APP-012 (Flow F step 7): a View ``tools/call app_only_echo`` reaches
    the Server through the Invoker with a consent prompt for ``app:apps`` (allowed) and one
    audit record; the View gets the ``CallToolResult``; the log stream records
    ``tools/call → success``."""
    view = ScriptedView()
    async with apps_host(setup, apps_params) as host, open_bridge(host, view) as bridge:
        prompter = host.prompter
        assert isinstance(prompter, FakePrompter)
        before = len(prompter.asked)
        await bridge.receive(
            msg(7, "tools/call", {"name": "app_only_echo", "arguments": {"text": "hi"}})
        )
        reply = await view.reply(7)
        records = (await host.audit.query(AuditQuery(consumer=APP))).records
        logs = [e.text for e in host.connections.logs(SERVER) if e.logger == "mcp-app"]
        asked = prompter.asked[before:]
    assert reply["result"] == {"content": [{"type": "text", "text": "hi"}], "isError": False}
    assert [str(r.consumer) for r in asked] == ["app:apps"]
    assert [(r.outcome, str(r.fqn)) for r in records] == [(Out.success, "apps::app_only_echo")]
    assert "tools/call → success" in logs


async def test_app_hidden_tool_refused(setup: Setup, apps_params: StdioServerParameters) -> None:
    """REQ-APP-008, REQ-APP-004 (Art. 3): ``model_only_echo`` from the View → ``-32602``,
    one ``denied`` / ``app_scope`` record, no prompt and the Server never called."""
    view = ScriptedView()
    async with apps_host(setup, apps_params) as host, open_bridge(host, view) as bridge:
        prompter = host.prompter
        assert isinstance(prompter, FakePrompter)
        before = len(prompter.asked)
        await bridge.receive(
            msg(8, "tools/call", {"name": "model_only_echo", "arguments": {"text": "x"}})
        )
        reply = await view.reply(8)
        records = (await host.audit.query(AuditQuery(consumer=APP))).records
        counts = await host.invoker.call(f"{SERVER}::call_counts", {}, consumer=HUMAN)
        logs = [e.text for e in host.connections.logs(SERVER) if e.logger == "mcp-app"]
        app_prompts = [r for r in prompter.asked[before:] if r.consumer == APP]
    assert app_prompts == []
    assert reply["error"]["code"] == -32602
    assert [(r.outcome, r.error_code, r.decision_source) for r in records] == [
        (Out.denied, "app_scope", "not_evaluated")
    ]
    assert "model_only_echo" not in json.loads(counts.text)
    assert "tools/call → -32602" in logs


async def test_view_resource_read_and_handshake(
    setup: Setup, apps_params: StdioServerParameters
) -> None:
    """REQ-APP-003, REQ-APP-006, REQ-APP-012: the handshake order on a real Host (resource,
    initialize reply, tool-input, tool-result) and a View ``resources/read`` of its own
    Server."""
    view = ScriptedView()
    async with apps_host(setup, apps_params) as host, open_bridge(host, view) as bridge:
        await bridge.receive(msg(9, "resources/read", {"uri": "ui://external"}))
        reply = await view.reply(9)
    methods = [m.get("method", f"reply:{m.get('id')}") for m in view.sent]
    assert methods[:4] == [
        "ui/notifications/sandbox-resource-ready",
        "reply:init",
        "ui/notifications/tool-input",
        "ui/notifications/tool-result",
    ]
    assert view.sent[2]["params"] == {"arguments": {"q": "from-ui"}}
    (item,) = reply["result"]["contents"]
    assert item["uri"] == "ui://external"


async def test_client_caps_only_after_enabling(
    setup: Setup, apps_params: StdioServerParameters
) -> None:
    """REQ-APP-007: the fixture reports the extension only when ``set_mcp_apps(True)``."""
    for enabled in (False, True):
        async with apps_host(setup, apps_params, mcp_apps=enabled) as host:
            result = await host.invoker.call(f"{SERVER}::client_caps", {}, consumer=HUMAN)
        assert result.ok, result.error
        assert json.loads(result.text)["mcp_apps"] is enabled
