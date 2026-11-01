"""A hostile Server's catastrophic regexes cannot block the Host (F8-10; REQ-NFR-021).

``tests/fixtures/servers/hostile_server.py`` declares ``^(a|aa)+$`` in an input and an output
schema. On both eras: the input case concludes ``protocol_error`` / ``invalid_input_schema``
without sending the call; the output case concludes ``protocol_error``; meanwhile a second,
healthy Server answers a ``tools/call`` in its normal time and an ``anyio`` heartbeat task is
never starved for more than 300 ms.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from tests.integration.test_host import MODERN_TABLE, Setup, _toml_server, setup
from tests.integration.test_invoker import BOT, call_counts, records, running_host

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["setup"]  # re-exported fixture

HOSTILE = Path(__file__).resolve().parents[1] / "fixtures" / "servers" / "hostile_server.py"
HOSTILE_TEXT = "a" * 64 + "!"
MAX_LOOP_GAP = 0.300  # budget + slack (F8-10 Done)
HEALTHY_LIMIT = 2.0


@pytest.fixture(params=["modern", "legacy"])
def hostile_table(request: pytest.FixtureRequest) -> str:
    """The hostile fixture Server on the modern SDK or (``interop``) the isolated mcp 1.x."""
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        legacy: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return _toml_server([legacy.command, *legacy.args[:-1], str(HOSTILE)])
    return _toml_server([sys.executable, str(HOSTILE)])


class Heartbeat:
    """Ticks every 10 ms and records the largest gap between ticks (event-loop starvation)."""

    def __init__(self) -> None:
        self.max_gap = 0.0

    async def run(self) -> None:
        last = time.perf_counter()
        while True:
            await anyio.sleep(0.01)
            now = time.perf_counter()
            self.max_gap = max(self.max_gap, now - last)
            last = now


async def test_catastrophic_input_pattern_is_unusable_schema(
    setup: Setup, hostile_table: str
) -> None:
    """REQ-NFR-021, REQ-INV-002, DD-010: a catastrophic input pattern → protocol_error /
    invalid_input_schema; the call is not sent; one audit record."""
    async with running_host(setup, {"evil": hostile_table}) as host:
        result = await host.invoker.call("evil::redos_input", {"text": HOSTILE_TEXT}, consumer=BOT)
        assert result.outcome is Out.protocol_error
        assert result.error is not None
        assert result.error.code == "invalid_input_schema"
        assert "pattern evaluation timed out" in result.error.message
        assert HOSTILE_TEXT not in result.error.message
        (record,) = await records(host, fqn=result.fqn)
        assert record.error_code == "invalid_input_schema"
        assert (await call_counts(host, "evil")).get("redos_input", 0) == 0
        ok = await host.invoker.call("evil::redos_input", {"text": "aaaa"}, consumer=BOT)
        assert ok.outcome is Out.success, ok.error


async def test_catastrophic_output_pattern_is_protocol_error(
    setup: Setup, hostile_table: str
) -> None:
    """REQ-NFR-021, REQ-INV-007: structuredContent that triggers the output pattern →
    protocol_error (never accepted as valid)."""
    async with running_host(setup, {"evil": hostile_table}) as host:
        result = await host.invoker.call("evil::redos_output", {}, consumer=BOT)
        assert result.outcome is Out.protocol_error
        assert result.error is not None
        assert "pattern evaluation timed out" in result.error.message
        assert result.structured_content is None


async def test_hostile_server_does_not_block_the_event_loop(
    setup: Setup, hostile_table: str
) -> None:
    """REQ-NFR-021, Art. 8: while both hostile validations run, a healthy Server answers a
    tools/call in its normal time and the event loop is never blocked for > 300 ms."""
    servers = {"evil": hostile_table, "good": MODERN_TABLE}
    async with running_host(setup, servers) as host:
        baseline_start = time.perf_counter()
        warm = await host.invoker.call("good::echo", {"text": "warm"}, consumer=BOT)
        baseline = time.perf_counter() - baseline_start
        assert warm.outcome is Out.success, warm.error
        heartbeat = Heartbeat()
        healthy: list[float] = []
        outcomes: list[Out] = []

        async def hostile(host: Host, fqn: str, args: dict[str, str]) -> None:
            for _ in range(3):
                outcomes.append((await host.invoker.call(fqn, args, consumer=BOT)).outcome)

        async def good(host: Host) -> None:
            for _ in range(3):
                start = time.perf_counter()
                result = await host.invoker.call("good::echo", {"text": "hi"}, consumer=BOT)
                healthy.append(time.perf_counter() - start)
                assert result.outcome is Out.success, result.error

        async with anyio.create_task_group() as tg:
            tg.start_soon(heartbeat.run)
            async with anyio.create_task_group() as work:
                work.start_soon(hostile, host, "evil::redos_input", {"text": HOSTILE_TEXT})
                work.start_soon(hostile, host, "evil::redos_output", {})
                work.start_soon(good, host)
            tg.cancel_scope.cancel()
        assert outcomes == [Out.protocol_error] * 6
        assert max(healthy) <= max(HEALTHY_LIMIT, baseline + MAX_LOOP_GAP), json.dumps(healthy)
        assert heartbeat.max_gap <= MAX_LOOP_GAP, heartbeat.max_gap
