"""Crash storm next to a healthy Server (F8-03; REQ-NFR-020, REQ-CON-011, REQ-CON-005/006).

``crash_server.py`` crashes on every connect (it reads the Client's first message and
exits). Its supervisor goes through 20 connect cycles (10 attempts → ``failed``,
``reconnect()``, 10 more) while a healthy modern Server next to it, on the same event bus
and task group, serves a call every 20 ms. The storm's sleep is injected (fast and
recorded; the RNG is fixed), so the backoff schedule is checked exactly: no attempt more
than REQ-CON-005/006 allow. Log volume stays bounded, and after the storm's supervisor
closes no task, file descriptor (Linux ``/proc/self/fd``) or crash-fixture process is left.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import anyio
import anyio.lowlevel
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.connection.state import MAX_FAILURES, ServerState, backoff_delay
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.events import EventBus, ServerStateChanged
from tests.conftest import SERVERS
from tests.integration.connection.test_supervisor import (
    CountingFactory,
    FakeSleep,
    stdio_def,
    wait_state,
)

pytestmark = pytest.mark.anyio
S = ServerState
CRASH_SERVER = SERVERS / "crash_server.py"
CYCLES = 2 * MAX_FAILURES  # 20 connect attempts: two runs to `failed`, one reconnect between
STDERR_LINES = 3
JITTER = 0.25
CALL_EVERY_S = 0.02
RECORDS_PER_ATTEMPT = 3
"""Host log records allowed per failed attempt (observed: one WARNING, plus the final ERROR)."""


def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _alive(pid: int) -> bool:
    """``pid`` exists and is not a zombie (Linux ``/proc``; elsewhere ``kill(pid, 0)``)."""
    stat = Path(f"/proc/{pid}/stat")
    if sys.platform == "linux":
        try:
            return stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


async def test_crash_storm_leaves_the_healthy_server_serving(
    modern_stdio: StdioServerParameters, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-NFR-020, REQ-CON-011, REQ-CON-005, REQ-CON-006: a Server crashing on every
    connect for 20 cycles never affects a healthy Server (it stays ``ready`` and every one
    of its calls succeeds); exactly 20 attempts with the jittered exponential delays of the
    schedule (none skipped, none extra); a bounded number of log records and log-ring
    entries; no task, file descriptor (Linux) or Server process left after the storm."""
    caplog.set_level(logging.DEBUG, logger="reflex_mcphost")
    pidfile = tmp_path / "pids.txt"
    crash = StdioServerParameters(
        command=sys.executable,
        args=[str(CRASH_SERVER), "--stderr-lines", str(STDERR_LINES), "--pidfile", str(pidfile)],
    )
    bus = EventBus()
    healthy_changes: list[ServerStateChanged] = []
    sleep, factory = FakeSleep(), CountingFactory()
    calls = {"ok": 0, "failed": 0}
    async with anyio.create_task_group() as tg, bus.subscribe(buffer=4096) as events:

        async def collect() -> None:
            async for event in events:
                if isinstance(event, ServerStateChanged) and event.server_id == "healthy":
                    healthy_changes.append(event)

        tg.start_soon(collect)
        healthy = ServerSupervisor(stdio_def(modern_stdio, server_id="healthy"), events=bus)
        healthy.start(tg)
        await wait_state(healthy, S.READY)
        await anyio.sleep(0.2)  # let the healthy Server's background tasks settle
        tasks_before = len(asyncio.all_tasks())
        fds_before = _fds() if sys.platform == "linux" else 0
        ready_changes = len(healthy_changes)
        storm_done = anyio.Event()

        async def caller() -> None:
            while not storm_done.is_set():
                try:
                    async with healthy.lease() as client:
                        result = await client.call_tool("echo", {"text": "alive"})
                    ok = result.content[0].text == "alive"  # type: ignore[union-attr]
                    calls["ok" if ok else "failed"] += 1
                except Exception:
                    calls["failed"] += 1
                await anyio.sleep(CALL_EVERY_S)

        tg.start_soon(caller)
        storm = ServerSupervisor(
            stdio_def(crash, server_id="storm"),
            events=bus,
            sleep=sleep,
            rng=lambda: JITTER,
            transport_factory=factory,
        )
        storm.start(tg)
        with anyio.fail_after(120):
            first = await storm.wait_for(S.FAILED)
            assert first.consecutive_failures == MAX_FAILURES
            await storm.reconnect()
            second = await storm.wait_for(S.FAILED)
            assert second.consecutive_failures == MAX_FAILURES
        await anyio.sleep(0.3)  # parked: no further attempt
        assert factory.calls == CYCLES
        ring = len(storm.logs)
        with anyio.fail_after(10):
            await storm.aclose()
        storm_done.set()
        for _ in range(50):  # the caller's last call and the storm's tasks wind down
            await anyio.sleep(0.02)
            if len(asyncio.all_tasks()) <= tasks_before:
                break
        tasks_after = len(asyncio.all_tasks())
        fds_after = _fds() if sys.platform == "linux" else 0
        assert healthy.status.state is S.READY
        assert healthy_changes[ready_changes:] == []  # never left `ready`
        with anyio.fail_after(10):
            await healthy.aclose()
        tg.cancel_scope.cancel()

    # the healthy Server served throughout
    assert calls["failed"] == 0
    assert calls["ok"] >= 20
    # backoff honoured: delays 1, 2, 4, … x jitter for attempts 1-9 of each run, nothing more
    expected = [backoff_delay(n, rng=lambda: JITTER) for n in range(MAX_FAILURES - 1)]
    assert sleep.delays == expected * 2
    # bounded log volume
    storm_records = [r for r in caplog.records if getattr(r, "server_id", None) == "storm"]
    assert 0 < len(storm_records) <= RECORDS_PER_ATTEMPT * CYCLES, len(storm_records)
    assert ring <= CYCLES * (STDERR_LINES + 1)
    # no leak
    assert tasks_after <= tasks_before, (tasks_before, tasks_after)
    if sys.platform == "linux":
        assert fds_after <= fds_before, (fds_before, fds_after)
    pids = [int(p) for p in pidfile.read_text(encoding="utf-8").split()]
    assert len(pids) == CYCLES
    if sys.platform != "win32":
        assert [p for p in pids if _alive(p)] == []
