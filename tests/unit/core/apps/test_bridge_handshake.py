"""``AppBridge`` handshake: proxy, ``ui/initialize``, tool input / result (F7-02; API §7)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.apps.bridge import AppBridge, AppSeed
from reflex_mcphost.core.apps.constants import APPS_PROTOCOL_VERSION
from reflex_mcphost.core.apps.csp import build_csp
from reflex_mcphost.core.invoker import InvocationError
from tests.unit.core.apps.helpers import (
    HOST_CONTEXT,
    HOST_INFO,
    HTML,
    INITIALIZE,
    INITIALIZED,
    PROXY_READY,
    FakePorts,
    Out,
    invocation,
    make_bridge,
    note,
    req,
    running,
)

pytestmark = pytest.mark.anyio
RESOURCE_READY = "ui/notifications/sandbox-resource-ready"


def _not_responses(ports: FakePorts) -> list[str]:
    return [m for m in ports.methods() if m != RESOURCE_READY]


async def test_resource_ready_once_after_proxy_ready() -> None:
    """REQ-APP-002, REQ-APP-003 (API §7): ``sandbox-proxy-ready`` → exactly one
    ``sandbox-resource-ready {html, csp, sandbox: "allow-scripts"}``; a second
    ``sandbox-proxy-ready`` is ignored and logged."""
    ports = FakePorts()
    csp = build_csp({"connectDomains": ["https://api.example.com"]})
    async with make_bridge(ports, csp=csp) as bridge:
        assert ports.sent == []
        await bridge.receive(PROXY_READY)
        await bridge.receive(PROXY_READY)
    (ready,) = ports.sent
    assert ready == {
        "jsonrpc": "2.0",
        "method": RESOURCE_READY,
        "params": {"html": HTML, "csp": csp.policy, "sandbox": "allow-scripts"},
    }
    assert ports.log_texts() == ["ui/notifications/sandbox-proxy-ready → ignored"]


async def test_initialize_reply() -> None:
    """REQ-APP-003, REQ-APP-011 (API §7 ``ui/initialize`` row): host info, the capabilities
    (``openLinks``, ``serverTools``, ``serverResources``, ``logging``, ``sandbox`` with no
    permissions — no device permission is ever granted — and the validated CSP lists) and
    the host context in ``inline`` mode."""
    ports = FakePorts()
    csp = build_csp({"resourceDomains": ["https://cdn.example.com", "*"]})
    async with make_bridge(ports, csp=csp) as bridge:
        await bridge.receive(PROXY_READY)
        await bridge.receive(INITIALIZE)
    result = ports.reply("init")["result"]
    assert result["protocolVersion"] == APPS_PROTOCOL_VERSION
    assert result["hostInfo"] == HOST_INFO
    assert result["hostCapabilities"] == {
        "openLinks": {},
        "serverTools": {},
        "serverResources": {},
        "logging": {},
        "sandbox": {
            "permissions": {},
            "csp": {
                "connectDomains": [],
                "resourceDomains": ["https://cdn.example.com"],
                "frameDomains": [],
                "baseUriDomains": [],
            },
        },
    }
    assert result["hostContext"] == {
        **HOST_CONTEXT,
        "displayMode": "inline",
        "availableDisplayModes": ["inline"],
    }


async def test_nothing_reaches_the_view_before_initialized() -> None:
    """REQ-APP-003 (API §7): before ``initialized`` the Host sends no request or
    notification to the View — only the proxy's resource and replies."""
    ports = FakePorts()
    async with make_bridge(ports) as bridge:
        await bridge.receive(PROXY_READY)
        await bridge.receive(INITIALIZE)
        await bridge.receive(req(2, "ping"))
        assert _not_responses(ports) == []
        await bridge.receive(INITIALIZED)
        assert _not_responses(ports) == [
            "ui/notifications/tool-input",
            "ui/notifications/tool-result",
        ]


async def test_requests_before_initialize_reply_are_refused() -> None:
    """REQ-APP-003, REQ-APP-005 (delta D10): before the ``ui/initialize`` reply only
    ``ui/initialize`` and ``ping`` are handled — other requests get ``-32600``
    "not initialized", notifications are dropped (also before the proxy is ready)."""
    ports = FakePorts()
    async with make_bridge(ports) as bridge:
        await bridge.receive(req(1, "tools/call", {"name": "app_only_echo"}))
        await bridge.receive(INITIALIZE.replace('"init"', '"early"'))
        await bridge.receive(req(2, "ping"))
        await bridge.receive(PROXY_READY)
        await bridge.receive(req(3, "resources/read", {"uri": "ui://x"}))
        await bridge.receive(note("ui/notifications/size-changed", {"height": 500}))
        await bridge.receive(INITIALIZED)  # before the reply: dropped
        await bridge.receive(INITIALIZE)
    for msg_id in (1, "early", 3):
        assert ports.reply(msg_id)["error"] == {"code": -32600, "message": "not initialized"}
    assert ports.reply(2)["result"] == {}
    assert "result" in ports.reply("init")
    assert ports.tool_calls == []
    assert ports.reads == []
    assert ports.heights == []
    assert _not_responses(ports) == []


async def test_second_initialize_and_initialized() -> None:
    """REQ-APP-006: a repeated ``ui/initialize`` is refused and a repeated ``initialized``
    sends nothing more — tool input and result go out exactly once."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(INITIALIZE.replace('"init"', '"again"'))
        await bridge.receive(INITIALIZED)
    assert ports.reply("again")["error"]["code"] == -32600
    assert _not_responses(ports) == ["ui/notifications/tool-input", "ui/notifications/tool-result"]


@pytest.mark.parametrize(("outcome", "is_error"), [(Out.success, False), (Out.tool_error, True)])
async def test_tool_input_then_result(outcome: Out, is_error: bool) -> None:
    """REQ-APP-006 (API §7): after ``initialized``: ``tool-input {arguments}`` then
    ``tool-result`` (the ``CallToolResult`` shape: content blocks, ``structuredContent``,
    ``isError`` true only for ``tool_error``), each exactly once."""
    ports = FakePorts()
    result = invocation(outcome, structured={"time": "t"}, text="12:00")
    async with running(ports, result=result, arguments={"q": 1}):
        pass
    tool_input, tool_result = (
        m for m in ports.sent if m.get("method", "").endswith(("input", "result"))
    )
    assert tool_input == {
        "jsonrpc": "2.0",
        "method": "ui/notifications/tool-input",
        "params": {"arguments": {"q": 1}},
    }
    assert tool_result["method"] == "ui/notifications/tool-result"
    assert tool_result["params"] == {
        "content": [{"type": "text", "text": "12:00"}],
        "structuredContent": {"time": "t"},
        "isError": is_error,
    }


async def test_result_without_structured_content() -> None:
    """REQ-APP-006: ``structuredContent`` is present only when set."""
    ports = FakePorts()
    async with running(ports, result=invocation(text="x")):
        pass
    (tool_result,) = [m for m in ports.sent if m.get("method") == "ui/notifications/tool-result"]
    assert tool_result["params"] == {"content": [{"type": "text", "text": "x"}], "isError": False}


@pytest.mark.parametrize("outcome", [Out.cancelled, Out.timeout, Out.connection_lost])
async def test_cancelled_outcomes(outcome: Out) -> None:
    """REQ-APP-009 (API §7): ``cancelled`` / ``timeout`` / ``connection_lost`` →
    ``tool-input`` then ``tool-cancelled {reason: <outcome>}`` and no ``tool-result``."""
    ports = FakePorts()
    error = InvocationError(code=outcome.value, message="x")
    async with running(ports, result=invocation(outcome, text="", error=error)):
        pass
    assert _not_responses(ports) == [
        "ui/notifications/tool-input",
        "ui/notifications/tool-cancelled",
    ]
    assert ports.sent[-1]["params"] == {"reason": outcome.value}


@pytest.mark.parametrize(
    "outcome",
    [
        Out.denied,
        Out.invalid_arguments,
        Out.not_found,
        Out.server_unavailable,
        Out.protocol_error,
        Out.audit_unavailable,
    ],
)
def test_non_rendering_outcomes_rejected(outcome: Out) -> None:
    """REQ-APP-001 (delta D2): a seed whose outcome renders no View is refused."""
    with pytest.raises(ValueError, match="does not render"):
        AppBridge(
            server_id="apps",
            view_id="v",
            seed=AppSeed(arguments={}, result=invocation(outcome)),
            html="x",
            host_info=HOST_INFO,
            host_context={},
            csp=build_csp(None),
            ports=FakePorts().ports(),
        )


async def test_dropped_csp_entries_are_logged() -> None:
    """REQ-APP-014 (API §7): each dropped CSP entry is recorded in the Server's log stream
    when the bridge starts."""
    ports = FakePorts()
    csp = build_csp(
        {"resourceDomains": ["*", "https://ok.example.com"], "frameDomains": ["https:"]}
    )
    async with make_bridge(ports, csp=csp):
        pass
    assert ports.log_texts() == [
        "csp → dropped resourceDomains entry *",
        "csp → dropped frameDomains entry https:",
    ]
    assert {e.level for e in ports.logged} == {"warning"}
