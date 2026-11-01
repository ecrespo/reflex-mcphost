"""Deadlines, cancellation and progress per transport (F3-09; REQ-INV-003…005, DD-004).

Transports: modern stdio, modern Streamable HTTP, legacy stdio and legacy Streamable HTTP.
The fixture ``slow`` tool writes ``cancelled`` to its ``marker`` file when the Server
observes the cancellation (stdio / legacy HTTP: ``notifications/cancelled``; modern HTTP: the
closed response stream).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.events import InvocationProgress
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome, Progress
from tests.integration.test_host import MODERN_TABLE, Setup, setup
from tests.integration.test_invoker import (
    BOT,
    FakePrompter,
    call_counts,
    http_table,
    records,
    running_host,
    stdio_table,
)

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
OBSERVE_TIMEOUT = 5.0
__all__ = ["setup"]  # re-exported fixture
TRANSPORTS = ["modern_stdio", "modern_http", "legacy_stdio", "legacy_http"]


def _table(request: pytest.FixtureRequest, transport: str, **extra: object) -> str:
    if transport.startswith("legacy"):
        request.applymarker(pytest.mark.interop)
    target = request.getfixturevalue(transport)
    if isinstance(target, StdioServerParameters):
        return stdio_table(target, **extra)
    return http_table(str(target), **extra)


@pytest.fixture(params=TRANSPORTS)
async def host(request: pytest.FixtureRequest, setup: Setup) -> AsyncIterator[Host]:
    async with running_host(setup, {"srv": _table(request, request.param)}) as h:
        yield h


async def _observed(marker: Path) -> None:
    """The Server wrote the marker: it saw the cancellation."""
    path = anyio.Path(marker)
    with anyio.fail_after(OBSERVE_TIMEOUT):
        while not (await path.exists() and await path.read_text(encoding="utf-8") == "cancelled"):  # noqa: ASYNC110 - bounded poll (fail_after) of a file the Server writes
            await anyio.sleep(0.02)


async def test_timeout_cancels_on_the_server(host: Host, tmp_path: Path) -> None:
    """REQ-INV-003: a call exceeding its timeout is cancelled by the transport-specific
    mechanism (the Server observes it) and returns outcome timeout, audited once."""
    marker = tmp_path / "timeout.marker"
    result = await host.invoker.call(
        "srv::slow", {"seconds": 30, "marker": str(marker)}, consumer=BOT, timeout=0.5
    )
    assert result.outcome is Out.timeout
    assert result.error is not None
    assert result.error.details["timeout_s"] == 0.5
    # duration uses perf_counter, the deadline the event-loop clock (~16 ms ticks on Windows)
    assert 480 <= result.duration_ms < 5000
    await _observed(marker)
    (record,) = await records(host, outcome=Out.timeout)
    assert (record.invocation_id, record.error_code) == (result.id, "timeout")


async def test_handle_cancel(host: Host, tmp_path: Path) -> None:
    """REQ-INV-004: handle.cancel() cancels the in-flight call by the same mechanism and the
    result is cancelled."""
    marker = tmp_path / "cancel.marker"
    handle = host.invoker.start("srv::slow", {"seconds": 30, "marker": str(marker)}, consumer=BOT)
    with anyio.fail_after(OBSERVE_TIMEOUT):  # the request reached the Server
        while "slow" not in await call_counts(host, "srv"):  # noqa: ASYNC110 - bounded poll (fail_after) of the Server's call counter
            await anyio.sleep(0.05)
    await handle.cancel()
    result = await handle.result()
    assert result.outcome is Out.cancelled
    await _observed(marker)
    record = await host.audit.get(result.id)
    assert record.outcome is Out.cancelled


async def test_awaiting_task_cancellation_reraises(host: Host, tmp_path: Path) -> None:
    """REQ-INV-004, API §4.8: cancelling the task awaiting call() cancels the request, records
    cancelled and re-raises the cancellation."""
    marker = tmp_path / "outer.marker"
    reached = False
    with anyio.move_on_after(0.8) as scope:
        await host.invoker.call("srv::slow", {"seconds": 30, "marker": str(marker)}, consumer=BOT)
        reached = True
    assert scope.cancelled_caught
    assert not reached
    await _observed(marker)
    (record,) = (await host.audit.query(AuditQuery(outcome=Out.cancelled))).records
    assert record.error_code == "cancelled"


async def test_progress_forwarded_in_order(host: Host) -> None:
    """REQ-INV-005: progress and total are forwarded in order to on_progress, to
    handle.progress() and as invocation.progress events."""
    got: list[Progress] = []
    result = await host.invoker.call(
        "srv::progress", {}, consumer=BOT, on_progress=got.append, timeout=10
    )
    assert result.outcome is Out.success
    assert [(p.progress, p.total) for p in got] == [(1.0, 3.0), (2.0, 3.0), (3.0, 3.0)]
    assert [p.message for p in got] == ["step 1", "step 2", "step 3"]

    async with host.events.subscribe() as events:
        handle = host.invoker.start("srv::progress", {}, consumer=BOT)
        streamed = [p async for p in handle.progress()]
        assert (await handle.result()).ok
        published: list[Progress] = []
        with anyio.fail_after(OBSERVE_TIMEOUT):
            async for event in events:
                if isinstance(event, InvocationProgress) and event.invocation_id == handle.id:
                    published.append(event.progress)
                    if len(published) == 3:
                        break
    assert streamed == published == got


# ------------------------------------------------------------------ deadline arithmetic


@pytest.fixture(params=["modern_stdio", "legacy_stdio"])
def stdio_transport(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def test_prompter_wait_excluded_from_timeout(
    request: pytest.FixtureRequest, setup: Setup, stdio_transport: str
) -> None:
    """DD-004, API §4.8: time spent waiting on the consent Prompter does not consume the
    call timeout."""
    prompter = FakePrompter(delay=2.5)
    servers = {"srv": _table(request, stdio_transport)}
    async with running_host(setup, servers, prompter=prompter, allow=False) as host:
        result = await host.invoker.call("srv::slow", {"seconds": 0.2}, consumer=BOT, timeout=2.0)
    assert result.outcome is Out.success, result.error
    assert result.duration_ms >= 2500  # longer than the 2 s timeout
    assert len(prompter.asked) == 1


async def test_timeout_resolution_per_server_then_default(
    request: pytest.FixtureRequest, setup: Setup, stdio_transport: str
) -> None:
    """REQ-INV-003: the timeout is per call, else the Server's timeout_s, else the Host
    default_tool_timeout_s."""
    servers = {
        "fast": _table(request, stdio_transport, timeout_s=0.4),
        "dflt": _table(request, stdio_transport),
    }
    lines = ["default_tool_timeout_s = 0.6"]
    async with running_host(setup, servers, host_lines=lines) as host:
        per_server = await host.invoker.call("fast::slow", {"seconds": 30}, consumer=BOT)
        default = await host.invoker.call("dflt::slow", {"seconds": 30}, consumer=BOT)
        per_call = await host.invoker.call("fast::slow", {"seconds": 0.6}, consumer=BOT, timeout=5)
    assert per_server.outcome is default.outcome is Out.timeout
    assert per_server.error is not None
    assert per_server.error.details["timeout_s"] == 0.4
    assert default.error is not None
    assert default.error.details["timeout_s"] == 0.6
    assert per_call.outcome is Out.success


async def test_timeout_reset_on_progress(
    request: pytest.FixtureRequest, setup: Setup, stdio_transport: str
) -> None:
    """REQ-INV-005: the timeout is reset by progress only when configured to do so."""
    args = {"steps": 8, "interval": 0.3}  # ~2.4 s in total, 0.3 s between progress
    async with running_host(setup, {"srv": _table(request, stdio_transport)}) as host:
        plain = await host.invoker.call("srv::progress", args, consumer=BOT, timeout=1.2)
        reset = await host.invoker.call(
            "srv::progress", args, consumer=BOT, timeout=1.2, reset_timeout_on_progress=True
        )
    assert plain.outcome is Out.timeout
    assert reset.outcome is Out.success, reset.error


async def test_host_default_reset_on_progress(setup: Setup) -> None:
    """REQ-INV-005: HostConfig.reset_timeout_on_progress is the default for every call."""
    args = {"steps": 8, "interval": 0.3}
    lines = ["reset_timeout_on_progress = true"]
    async with running_host(setup, {"srv": MODERN_TABLE}, host_lines=lines) as host:
        result = await host.invoker.call("srv::progress", args, consumer=BOT, timeout=1.2)
        opted_out = await host.invoker.call(
            "srv::progress", args, consumer=BOT, timeout=1.2, reset_timeout_on_progress=False
        )
    assert result.outcome is Out.success, result.error
    assert opted_out.outcome is Out.timeout
