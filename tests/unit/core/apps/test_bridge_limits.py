"""``AppBridge`` limits and robustness (F7-02; API §7 "Limits", REQ-APP-005/013)."""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reflex_mcphost.core.apps.constants import MAX_INFLIGHT, MAX_MESSAGE_BYTES
from tests.unit.core.apps.helpers import (
    INITIALIZE,
    INITIALIZED,
    PROXY_READY,
    FakePorts,
    make_bridge,
    note,
    req,
    running,
)

pytestmark = pytest.mark.anyio
RESOURCE_READY = "ui/notifications/sandbox-resource-ready"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not json", -32700),
        ('[{"jsonrpc":"2.0","id":1,"method":"ping"}]', -32600),
        ('{"id":"m","method":"ping"}', -32600),
        (json.dumps({"jsonrpc": "2.0", "id": "m", "method": "x" * 129}), -32600),
        (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "m",
                    "method": "ping",
                    "params": {"f": "x" * MAX_MESSAGE_BYTES},
                }
            ),
            -32600,
        ),
        ('{"jsonrpc":"2.0","id":1.5,"method":"ping"}', -32600),
    ],
    ids=["not-json", "batch", "no-jsonrpc", "long-method", "too-large", "bad-id"],
)
async def test_malformed_gets_code_and_bridge_keeps_running(raw: str, code: int) -> None:
    """REQ-APP-005 (API §7): each malformed input gets its JSON-RPC error (the id echoed
    when readable, else ``null``) and the bridge still answers ``ping``."""
    ports = FakePorts()
    async with running(ports) as bridge:
        await bridge.receive(raw)
        await bridge.receive(req("after", "ping"))
    errors = [m for m in ports.sent if "error" in m]
    assert [e["error"]["code"] for e in errors] == [code]
    assert errors[0]["id"] in ("m", None)
    assert ports.reply("after")["result"] == {}
    assert ports.log_texts()[-2] == f"invalid message → {code}"


async def test_oversized_without_id_dropped() -> None:
    """REQ-APP-005: an oversized message without a readable id is dropped (no reply)."""
    ports = FakePorts()
    async with running(ports) as bridge:
        before = len(ports.sent)
        await bridge.receive(note("m", {"f": "x" * MAX_MESSAGE_BYTES}))
        assert len(ports.sent) == before
    assert ports.log_texts()[-1] == "invalid message → dropped"


async def test_fifth_concurrent_request_refused() -> None:
    """REQ-APP-005, REQ-APP-013 (API §7): at most 4 View requests in flight; the 5th →
    ``-32000`` "too many concurrent requests"; once one finishes a new one is accepted."""
    gate = anyio.Event()
    ports = FakePorts(tool_gate=gate)
    async with running(ports) as bridge:
        for i in range(MAX_INFLIGHT - 1):
            await bridge.receive(req(i, "tools/call", {"name": "t"}))
        await bridge.receive(req(3, "resources/read", {"uri": "x://a"}))
        await bridge.receive(req(4, "tools/call", {"name": "t"}))
        await bridge.receive(req(5, "ping"))  # not counted
        assert ports.reply(4)["error"] == {
            "code": -32000,
            "message": "too many concurrent requests",
        }
        assert ports.reply(5)["result"] == {}
        gate.set()
        await anyio.wait_all_tasks_blocked()
        await bridge.receive(req(6, "tools/call", {"name": "t"}))
        await anyio.wait_all_tasks_blocked()
    assert "result" in ports.reply(6)
    assert len(ports.tool_calls) == MAX_INFLIGHT  # 3 + the 6th; the 5th never invoked


async def test_send_failure_never_raises() -> None:
    """REQ-APP-005: a failing ``send`` port is contained; ``receive`` never raises."""
    ports = FakePorts(send_error=RuntimeError("socket gone"))
    async with make_bridge(ports) as bridge:
        await bridge.receive(PROXY_READY)
        await bridge.receive(INITIALIZE)
        await bridge.receive(INITIALIZED)
        await bridge.receive(req(1, "ping"))


async def test_receive_outside_the_context_is_dropped() -> None:
    """REQ-APP-005: messages for a bridge that is not running are dropped, not raised."""
    ports = FakePorts()
    bridge = make_bridge(ports)
    await bridge.receive(PROXY_READY)
    async with bridge:
        pass
    await bridge.receive(req(1, "ping"))
    assert ports.sent == []


_ids = st.one_of(st.integers(-5, 5), st.text(max_size=3), st.none(), st.floats(), st.booleans())
_methods = st.sampled_from(
    [
        "ui/notifications/sandbox-proxy-ready",
        "ui/initialize",
        "ui/notifications/initialized",
        "ping",
        "tools/call",
        "resources/read",
        "ui/open-link",
        "ui/request-display-mode",
        "ui/notifications/size-changed",
        "notifications/message",
        "ui/message",
        "unknown/x",
    ]
)
_params = st.one_of(
    st.none(),
    st.dictionaries(
        st.sampled_from(["name", "arguments", "uri", "url", "height", "level", "data"]),
        st.one_of(st.text(max_size=8), st.integers(), st.none(), st.just("https://example.com")),
        max_size=3,
    ),
    st.lists(st.integers(), max_size=2),
)


@st.composite
def _message(draw: Any) -> str:
    kind = draw(st.sampled_from(["request", "notification", "response", "junk", "batch"]))
    if kind == "junk":
        return draw(st.one_of(st.text(max_size=20), st.just("{}"), st.just("null")))
    msg: dict[str, Any] = {"jsonrpc": draw(st.sampled_from(["2.0", "2.0", "1.0"]))}
    if kind in ("request", "notification"):
        msg["method"] = draw(_methods)
        params = draw(_params)
        if params is not None:
            msg["params"] = params
    if kind in ("request", "response"):
        msg["id"] = draw(_ids)
    if kind == "response":
        msg["result"] = {}
    if kind == "batch":
        return json.dumps([msg])
    return json.dumps(msg)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(_message(), max_size=25))
def test_property_never_raises_never_sends_early(messages: list[str]) -> None:
    """REQ-APP-003, REQ-APP-005 (Hypothesis): random message sequences never raise out of
    ``receive``, and before ``ui/notifications/initialized`` the bridge sends only replies
    and the proxy's ``sandbox-resource-ready``."""

    async def scenario() -> None:
        ports = FakePorts(link_answer=False)
        async with make_bridge(ports, teardown_timeout_s=0.01) as bridge:
            for raw in messages:
                await bridge.receive(raw)
                if not bridge.initialized:
                    early = [m for m in ports.methods() if m != RESOURCE_READY]
                    assert early == [], early
            await anyio.wait_all_tasks_blocked()
            await bridge.teardown("closed")
        assert ports.methods().count(RESOURCE_READY) <= 1
        assert ports.methods().count("ui/notifications/tool-input") <= 1

    anyio.run(scenario)
