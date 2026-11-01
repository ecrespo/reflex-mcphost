"""``AppBridge`` method table and App ``tools/call`` replies (F7-02; API §7, delta D9)."""

from __future__ import annotations

import base64

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.apps.constants import MAX_RESOURCE_BYTES
from reflex_mcphost.core.catalog import ResourceContents
from reflex_mcphost.core.invoker import InvocationError
from reflex_mcphost.core.types import Consumer
from tests.unit.core.apps.helpers import (
    ARG_SECRET,
    HTML_SECRET,
    INITIALIZE,
    INITIALIZED,
    PROXY_READY,
    RESULT_SECRET,
    SERVER,
    FakePorts,
    Out,
    invocation,
    note,
    req,
    running,
)

pytestmark = pytest.mark.anyio
APP = Consumer.app(SERVER)


async def _settle() -> None:
    await anyio.wait_all_tasks_blocked()


async def test_ping() -> None:
    """REQ-APP-005 (API §7 ``ping`` row): an empty result, logged ``ping → ok``."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(10, "ping"))
    assert ports.reply(10)["result"] == {}
    assert ports.log_texts()[-1] == "ping → ok"


async def test_tools_call_success_builds_own_server_name() -> None:
    """REQ-APP-004 (API §7 ``tools/call`` row): the bridge passes the View's bare name and
    arguments to the ``invoke_tool`` port (bound to ``Invoker.call`` with
    ``Consumer.app(server_id)`` and ``<server_id>::<name>``) and replies with the
    ``CallToolResult``; logged ``tools/call → success``."""
    ports = FakePorts()
    ports.tool_results["app_only_echo"] = invocation(
        text="echo", structured={"v": 1}, name="app_only_echo", consumer=APP
    )
    async with running(ports) as bridge:
        await bridge.receive(
            req(11, "tools/call", {"name": "app_only_echo", "arguments": {"text": "hi"}})
        )
        await _settle()
    assert ports.tool_calls == [("app_only_echo", {"text": "hi"})]
    assert ports.reply(11)["result"] == {
        "content": [{"type": "text", "text": "echo"}],
        "structuredContent": {"v": 1},
        "isError": False,
    }
    assert ports.log_texts()[-1] == "tools/call → success"
    assert ports.logged[-1].level == "info"


async def test_tools_call_tool_error() -> None:
    """REQ-APP-004 (API §7 reply table): ``tool_error`` → ``CallToolResult`` with
    ``isError: true``; logged at ``warning``."""
    ports = FakePorts()
    ports.tool_results["t"] = invocation(Out.tool_error, text="bad", name="t", consumer=APP)
    async with running(ports) as bridge:
        await bridge.receive(req(12, "tools/call", {"name": "t"}))
        await _settle()
    assert ports.tool_calls == [("t", {})]
    assert ports.reply(12)["result"] == {
        "content": [{"type": "text", "text": "bad"}],
        "isError": True,
    }
    assert (ports.log_texts()[-1], ports.logged[-1].level) == ("tools/call → tool_error", "warning")


async def test_tools_call_app_scope_is_invalid_params() -> None:
    """REQ-APP-008 (API §7 reply table): ``denied`` / ``app_scope`` → JSON-RPC ``-32602``."""
    ports = FakePorts()
    ports.tool_results["model_only_echo"] = invocation(
        Out.denied,
        text="",
        name="model_only_echo",
        consumer=APP,
        error=InvocationError(code="app_scope", message="m", details={"reason": "not_app_visible"}),
    )
    async with running(ports) as bridge:
        await bridge.receive(req(13, "tools/call", {"name": "model_only_echo"}))
        await _settle()
    assert ports.reply(13)["error"]["code"] == -32602
    assert ports.log_texts()[-1] == "tools/call → -32602"
    assert ports.logged[-1].level == "warning"


@pytest.mark.parametrize(
    ("outcome", "code", "message", "text"),
    [
        (
            Out.denied,
            "consent_denied",
            "denied by consent",
            "Permission denied by the host: denied by consent",
        ),
        (Out.server_unavailable, "server_unavailable", "Server apps is reconnecting", None),
        (Out.invalid_arguments, "invalid_arguments", "the arguments do not match", None),
        (Out.timeout, "timeout", "no result within 60 s", None),
    ],
)
async def test_tools_call_other_outcomes_are_error_results(
    outcome: Out, code: str, message: str, text: str | None
) -> None:
    """REQ-APP-004 (API §7 reply table, delta D9): any other outcome →
    ``CallToolResult{isError: true, content: [text]}`` with the Toolset's
    ``ToolCallOutcome.text`` (never argument values)."""
    ports = FakePorts()
    ports.tool_results["t"] = invocation(
        outcome, text="", name="t", consumer=APP, error=InvocationError(code=code, message=message)
    )
    async with running(ports) as bridge:
        await bridge.receive(req(14, "tools/call", {"name": "t", "arguments": {"k": ARG_SECRET}}))
        await _settle()
    result = ports.reply(14)["result"]
    assert result["isError"] is True
    (block,) = result["content"]
    assert block["type"] == "text"
    assert block["text"]
    if text is not None:
        assert block["text"] == text
    assert ARG_SECRET not in block["text"]
    assert ports.log_texts()[-1] == f"tools/call → {outcome.value}"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"name": 3},
        {"name": ""},
        {"name": "t", "arguments": [1]},
        {"name": "t", "arguments": "x"},
    ],
)
async def test_tools_call_bad_params(params: dict[str, object]) -> None:
    """REQ-APP-005: a ``tools/call`` without a string ``name`` or with non-object
    ``arguments`` → ``-32602``; nothing is invoked."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(15, "tools/call", params))
        await _settle()
    assert ports.reply(15)["error"]["code"] == -32602
    assert ports.tool_calls == []


async def test_tools_call_port_failure_fails_closed() -> None:
    """REQ-APP-005 (Art. 3 fail-closed): a port that raises → ``-32000``, the View keeps
    running."""
    ports = FakePorts(tool_error=RuntimeError(f"boom {ARG_SECRET}"))
    async with running(ports) as bridge:
        await bridge.receive(req(16, "tools/call", {"name": "t"}))
        await _settle()
        await bridge.receive(req(17, "ping"))
    assert ports.reply(16)["error"] == {"code": -32000, "message": "tool call failed"}
    assert ports.reply(17)["result"] == {}


async def test_resources_read() -> None:
    """API §7 ``resources/read`` row: own Server only (the port is bound to it); the
    ``ReadResourceResult`` shape; logged ``resources/read → ok``."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(18, "resources/read", {"uri": "fixture://hello"}))
        await _settle()
    assert ports.reads == ["fixture://hello"]
    assert ports.reply(18)["result"] == {
        "contents": [{"uri": "fixture://hello", "mimeType": "text/plain", "text": "hello"}]
    }
    assert ports.log_texts()[-1] == "resources/read → ok"


async def test_resources_read_limits_and_errors() -> None:
    """REQ-APP-013 (API §7): a result larger than 5 MiB → ``-32000`` "resource too
    large"; a failed read → ``-32000``; a missing ``uri`` → ``-32602``."""
    big = types.BlobResourceContents(
        uri="x://b", blob=base64.b64encode(b"a" * (MAX_RESOURCE_BYTES + 1)).decode()
    )
    ports = FakePorts(resource=ResourceContents(server_id=SERVER, uri="x://b", contents=(big,)))
    async with running(ports) as bridge:
        await bridge.receive(req(19, "resources/read", {"uri": "x://b"}))
        await bridge.receive(req(20, "resources/read", {}))
        await _settle()
        ports.resource = None
        ports.resource_error = RuntimeError("down")
        await bridge.receive(req(21, "resources/read", {"uri": "x://c"}))
        await _settle()
    assert ports.reply(19)["error"] == {"code": -32000, "message": "resource too large"}
    assert ports.reply(20)["error"]["code"] == -32602
    assert ports.reply(21)["error"] == {"code": -32000, "message": "resource read failed"}
    assert sorted(ports.log_texts()[-3:]) == [
        "resources/read → -32000",
        "resources/read → -32000",
        "resources/read → -32602",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "https://",
        "mailto:a@example.com",
        "https://exa mple.com",
        "https://example.com/" + "a" * 2048,
        7,
    ],
)
async def test_open_link_not_openable(url: object) -> None:
    """REQ-APP-015 (API §7 ``ui/open-link`` row): a URL that is not ``http``/``https``
    with a host, or longer than 2,048 characters → ``-32000`` without asking the user."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(22, "ui/open-link", {"url": url}))
        await _settle()
    assert ports.reply(22)["error"]["code"] == -32000
    assert ports.links == []


async def test_open_link_confirmed_and_declined() -> None:
    """REQ-APP-015: an openable URL is shown for confirmation; approve → ``{}``, decline →
    ``-32000``; a second link while one waits → ``-32000``."""
    gate = anyio.Event()
    ports = FakePorts(link_gate=gate)
    async with running(ports) as bridge:
        await bridge.receive(req(23, "ui/open-link", {"url": "https://example.com/a"}))
        await _settle()
        await bridge.receive(req(24, "ui/open-link", {"url": "https://example.com/b"}))
        gate.set()
        await _settle()
        ports.link_answer = False
        await bridge.receive(req(25, "ui/open-link", {"url": "http://example.org"}))
        await _settle()
    assert ports.links == ["https://example.com/a", "http://example.org"]
    assert ports.reply(23)["result"] == {}
    assert ports.reply(24)["error"] == {"code": -32000, "message": "a link confirmation is pending"}
    assert ports.reply(25)["error"] == {"code": -32000, "message": "link declined"}


async def test_request_display_mode() -> None:
    """API §7: only ``inline`` is supported; the reply is ``{mode: "inline"}``."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(26, "ui/request-display-mode", {"mode": "fullscreen"}))
    assert ports.reply(26)["result"] == {"mode": "inline"}


@pytest.mark.parametrize(("height", "clamped"), [(10, 80), (500, 500), (99999, 4000), (250.6, 251)])
async def test_size_changed_clamped(height: float, clamped: int) -> None:
    """API §7 ``size-changed`` row: height clamped to 80…4,000 px."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(
            note("ui/notifications/size-changed", {"width": 300, "height": height})
        )
        assert bridge.height == clamped
        await bridge.receive(note("ui/notifications/size-changed", {"height": "tall"}))
        assert bridge.height == clamped
    assert ports.heights == [clamped]


async def test_notifications_message_appended() -> None:
    """REQ-HSH-030, REQ-APP-012 (API §7): a View log message is appended with logger
    ``mcp-app``, its level and its ``data`` as text cut to 4 KiB."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(note("notifications/message", {"level": "error", "data": "x" * 5000}))
        await bridge.receive(note("notifications/message", {"level": "loud", "data": {"a": 1}}))
    first, second = ports.logged[-2:]
    assert (first.logger, first.level) == ("mcp-app", "error")
    assert len(first.text.encode()) <= 4096 + len(" [truncated]")
    assert first.text.endswith(" [truncated]")
    assert (second.level, second.text) == ("info", '{"a": 1}')


@pytest.mark.parametrize("method", ["ui/message", "ui/update-model-context"])
async def test_conversation_methods_not_supported(method: str) -> None:
    """REQ-APP-005 (API §7): no conversation in v0.1 → ``-32601`` "Method not supported by
    this host"."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(req(27, method, {"role": "user"}))
    assert ports.reply(27)["error"] == {
        "code": -32601,
        "message": "Method not supported by this host",
    }


async def test_unknown_method_and_notification() -> None:
    """REQ-APP-005: an unknown request → ``-32601``; an unknown notification is dropped;
    the View keeps running."""
    ports = FakePorts()
    async with running(ports) as bridge:
        sent = len(ports.sent)
        await bridge.receive(note("ui/notifications/whatever"))
        assert len(ports.sent) == sent
        await bridge.receive(req(28, "tools/list"))
        await bridge.receive(req(29, "ping"))
    assert ports.reply(28)["error"] == {"code": -32601, "message": "Method not found"}
    assert ports.reply(29)["result"] == {}


async def test_host_context_changed() -> None:
    """API §7: a theme / container change is sent as ``host-context-changed`` once the View
    is initialized (never before)."""
    ports = FakePorts()
    async with running(ports, initialize=False) as bridge:
        await bridge.set_host_context(theme="dark")
        assert ports.sent == []
        for raw in (PROXY_READY, INITIALIZE, INITIALIZED):
            await bridge.receive(raw)
        await bridge.set_host_context(theme="dark")
    assert ports.sent[-1] == {
        "jsonrpc": "2.0",
        "method": "ui/notifications/host-context-changed",
        "params": {"theme": "dark"},
    }


async def test_log_entries_carry_method_and_outcome_only() -> None:
    """REQ-APP-012 (Art. 5): every View RPC is logged as ``<method> → <outcome>`` — the
    seeded argument, result and HTML markers never appear in any log entry."""
    ports = FakePorts()
    ports.tool_results["t"] = invocation(text=f"out {RESULT_SECRET}", name="t", consumer=APP)
    async with running(ports) as bridge:
        await bridge.receive(req(30, "tools/call", {"name": "t", "arguments": {"a": ARG_SECRET}}))
        await bridge.receive(req(31, "resources/read", {"uri": f"x://{ARG_SECRET}"}))
        await bridge.receive(req(32, "ui/open-link", {"url": f"https://example.com/{ARG_SECRET}"}))
        await bridge.receive(req(33, "nope", {"k": ARG_SECRET}))
        await bridge.receive(
            '{"jsonrpc":"2.0","id":34,"method":"m","params":["' + ARG_SECRET + '"]}'
        )
        await _settle()
    assert ports.logged
    for entry in ports.logged:
        for marker in (ARG_SECRET, RESULT_SECRET, HTML_SECRET):
            assert marker not in entry.text, entry
        assert " → " in entry.text
