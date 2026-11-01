"""``AppBridge.teardown``: ``ui/resource-teardown`` and in-flight cancellation (F7-02;
API §7, REQ-APP-010, delta D10)."""

from __future__ import annotations

import time
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.apps.bridge import TEARDOWN_REASONS
from tests.unit.core.apps.helpers import FakePorts, make_bridge, req, running

pytestmark = pytest.mark.anyio


def _teardowns(ports: FakePorts) -> list[dict[str, Any]]:
    return [m for m in ports.sent if m.get("method") == "ui/resource-teardown"]


def test_reasons() -> None:
    """REQ-APP-010 (API §7): the four teardown reasons."""
    assert frozenset({"replaced", "closed", "server_removed", "host_closing"}) == TEARDOWN_REASONS


@pytest.mark.parametrize("reason", sorted(TEARDOWN_REASONS))
async def test_teardown_answered_in_time(reason: str) -> None:
    """REQ-APP-010: the request carries the reason; a response ends the wait at once."""
    ports = FakePorts()

    async def answer(message: dict[str, Any]) -> None:
        if message.get("method") == "ui/resource-teardown":
            tg.start_soon(
                bridge.receive, f'{{"jsonrpc":"2.0","id":"{message["id"]}","result":{{}}}}'
            )

    ports.on_send = answer
    async with anyio.create_task_group() as tg, running(ports) as bridge:
        started = time.monotonic()
        await bridge.teardown(reason)
        assert time.monotonic() - started < 1.0
    (teardown,) = _teardowns(ports)
    assert teardown["params"] == {"reason": reason}
    assert isinstance(teardown["id"], str)
    assert bridge.closed


async def test_teardown_proceeds_after_two_seconds() -> None:
    """REQ-APP-010: with no response the bridge proceeds after 2 s (the default)."""
    ports = FakePorts()
    async with running(ports) as bridge:
        started = time.monotonic()
        await bridge.teardown("replaced")
        elapsed = time.monotonic() - started
    assert 1.9 <= elapsed < 3.0
    assert len(_teardowns(ports)) == 1


async def test_teardown_cancels_in_flight_calls() -> None:
    """REQ-APP-010 (delta D10): after the wait the View's in-flight requests are cancelled
    (their invocations end ``cancelled``) and no reply is sent for them."""
    ports = FakePorts(tool_gate=anyio.Event())
    async with running(ports, teardown_timeout_s=0.1) as bridge:
        await bridge.receive(req(1, "tools/call", {"name": "slow"}))
        await anyio.wait_all_tasks_blocked()
        await bridge.teardown("closed")
        assert ports.cancelled_calls == 1
        await bridge.receive(req(2, "ping"))
    assert [m for m in ports.sent if m.get("id") in (1, 2)] == []


async def test_teardown_before_initialized_sends_nothing() -> None:
    """REQ-APP-003, REQ-APP-010: a View that never initialized gets no teardown request
    (nothing is sent before ``initialized``); the bridge closes at once."""
    ports = FakePorts()
    async with running(ports, initialize=False) as bridge:
        await bridge.teardown("replaced")
    assert ports.sent == []
    assert bridge.closed


async def test_teardown_idempotent_and_validated() -> None:
    """REQ-APP-010: a second teardown is a no-op; an unknown reason is a ``ValueError``."""
    ports = FakePorts()
    async with running(ports, teardown_timeout_s=0.05) as bridge:
        with pytest.raises(ValueError, match="unknown teardown reason"):
            await bridge.teardown("bored")
        await bridge.teardown("closed")
        await bridge.teardown("closed")
    assert len(_teardowns(ports)) == 1


async def test_requests_while_tearing_down_refused() -> None:
    """REQ-APP-010: requests arriving during the teardown wait get ``-32000``."""
    ports = FakePorts()

    async def during(message: dict[str, Any]) -> None:
        if message.get("method") == "ui/resource-teardown":
            tg.start_soon(bridge.receive, req("late", "tools/call", {"name": "t"}))

    ports.on_send = during
    async with anyio.create_task_group() as tg, running(ports, teardown_timeout_s=0.2) as bridge:
        await bridge.teardown("closed")
    assert ports.reply("late")["error"] == {"code": -32000, "message": "the View is closing"}
    assert ports.tool_calls == []


async def test_exit_discards_without_teardown() -> None:
    """REQ-APP-010 (API §7): leaving the context without ``teardown`` (a tab disconnect)
    cancels in-flight requests and sends nothing."""
    ports = FakePorts(tool_gate=anyio.Event())
    async with make_bridge(ports) as bridge:
        from tests.unit.core.apps.helpers import INITIALIZE, INITIALIZED, PROXY_READY

        for raw in (PROXY_READY, INITIALIZE, INITIALIZED):
            await bridge.receive(raw)
        await bridge.receive(req(1, "tools/call", {"name": "slow"}))
        await anyio.wait_all_tasks_blocked()
        sent = len(ports.sent)
    assert ports.cancelled_calls == 1
    assert len(ports.sent) == sent
    assert _teardowns(ports) == []


# ------------------------------------------------------------------ discard (F8-13, delta D7)


async def test_discard_navigated_sends_nothing_and_cancels_in_flight_calls() -> None:
    """REQ-APP-010, REQ-APP-012 (API §7 Lifecycle, delta D7): ``discard("navigated")`` —
    the View navigated its own frame — sends nothing to the View (no
    ``ui/resource-teardown``), cancels an in-flight ``tools/call`` (its invocation ends
    ``cancelled``, no reply), logs ``view → navigated`` and closes the bridge, which then
    drops every message."""
    ports = FakePorts(tool_gate=anyio.Event())
    async with running(ports) as bridge:
        await bridge.receive(req(1, "tools/call", {"name": "slow"}))
        await anyio.wait_all_tasks_blocked()
        sent_before = len(ports.sent)
        started = time.monotonic()
        await bridge.discard("navigated")
        assert time.monotonic() - started < 1.0  # no 2 s teardown wait
        assert ports.cancelled_calls == 1
        assert bridge.closed
        await bridge.receive(req(2, "ping"))
    assert ports.sent[sent_before:] == []
    assert _teardowns(ports) == []
    assert "view → navigated" in ports.log_texts()


async def test_discard_is_idempotent_validated_and_final() -> None:
    """REQ-APP-010 (delta D7): ``navigated`` is a discard reason, never a teardown reason;
    an unknown discard reason is refused; a second discard (or a later teardown) does
    nothing and logs nothing more."""
    assert "navigated" not in TEARDOWN_REASONS
    ports = FakePorts()
    async with running(ports) as bridge:
        with pytest.raises(ValueError, match="unknown discard reason"):
            await bridge.discard("closed")
        with pytest.raises(ValueError, match="unknown teardown reason"):
            await bridge.teardown("navigated")
        await bridge.discard("navigated")
        logged = list(ports.log_texts())
        await bridge.discard("navigated")
        await bridge.teardown("closed")
        assert ports.log_texts() == logged
    assert _teardowns(ports) == []
