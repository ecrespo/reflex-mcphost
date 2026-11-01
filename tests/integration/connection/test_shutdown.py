"""Disconnect and shutdown (F2-09, F8-08; REQ-CON-008, REQ-CON-009, S-2 §4, TD DD-002).

The stdio escalation (close stdin → 2 s → SIGTERM to the process group → 2 s → SIGKILL) is
the SDK's (Art. 2); these tests prove the Host wires it, never shortens it and runs it even
under cancellation. After the transport has closed for any reason — also when the Server
exited promptly on stdin EOF, where the SDK signals nothing — the Host signals what is left
of the Server's process group (POSIX ``killpg`` SIGTERM, SIGKILL after 2 s; Windows: the
SDK's Job Object, closed with ``KILL_ON_JOB_CLOSE``). Timing bounds are generous upward
(loaded CI) but tight downward.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.host import SHUTDOWN_DEADLINE_S
from tests.conftest import MODERN
from tests.integration.connection.test_supervisor import running, stdio_def, wait_state
from tests.integration.test_host import Setup, _toml_server, setup  # noqa: F401 - fixture
from tests.process import pid_alive

pytestmark = pytest.mark.anyio
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups/signals")
S = ServerState


def _modern(*flags: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(MODERN), *flags])


alive = pid_alive


async def gone(pid: int, within: float = 2.0) -> bool:
    with anyio.move_on_after(within):
        while alive(pid):  # noqa: ASYNC110 - no event signals another process's death
            await anyio.sleep(0.02)
        return True
    return False


async def _pid(sup: ServerSupervisor) -> int:
    async with sup.lease() as client:
        result = await client.call_tool("pid", {})
    return int(result.content[0].text)  # type: ignore[union-attr]


async def _timed_stop(params: StdioServerParameters) -> tuple[float, int]:
    async with running(stdio_def(params)) as (sup, _):
        await wait_state(sup, S.READY)
        pid = await _pid(sup)
        started = time.monotonic()
        await sup.stop()
        elapsed = time.monotonic() - started
        assert sup.status.state is S.DISCONNECTED
    return elapsed, pid


async def test_eof_honouring_server_stops_fast() -> None:
    """REQ-CON-008: a Server that exits on stdin EOF is gone well before the 2 s grace."""
    elapsed, pid = await _timed_stop(_modern())
    assert elapsed < 1.5
    assert await gone(pid)


@posix_only
async def test_ignore_eof_is_terminated_after_two_seconds() -> None:
    """REQ-CON-008: stdin closed, 2 s grace, then SIGTERM ends a Server ignoring EOF."""
    elapsed, pid = await _timed_stop(_modern("--ignore-eof"))
    assert 1.9 <= elapsed < 3.9
    assert await gone(pid)


@posix_only
async def test_ignore_term_is_killed_after_four_seconds() -> None:
    """REQ-CON-008: a Server ignoring EOF and SIGTERM is SIGKILLed 2 s after SIGTERM."""
    elapsed, pid = await _timed_stop(_modern("--ignore-eof", "--ignore-term"))
    assert 3.9 <= elapsed < 6.5
    assert await gone(pid)


@posix_only
async def test_grandchild_is_gone_after_disconnect(tmp_path: Path) -> None:
    """REQ-CON-008: the whole process group is signalled — a grandchild spawned by the
    Server does not survive the disconnect."""
    pidfile = tmp_path / "grandchild.pid"
    params = _modern("--ignore-eof", "--spawn-grandchild", str(pidfile))
    async with running(stdio_def(params)) as (sup, _):
        await wait_state(sup, S.READY)
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        assert alive(grandchild)
        await sup.stop()
    assert await gone(grandchild)


@posix_only
async def test_stop_is_shielded_from_outer_cancellation() -> None:
    """REQ-CON-008, REQ-CON-009: cancelling the scope that runs the supervisor does not cut
    the escalation short — the stubborn child is still terminated."""
    bus = EventBus()
    pid = 0
    started = 0.0
    with anyio.fail_after(10):
        async with anyio.create_task_group() as tg:
            sup = ServerSupervisor(stdio_def(_modern("--ignore-eof")), events=bus)
            sup.start(tg)
            await sup.wait_for(S.READY)
            pid = await _pid(sup)
            started = time.monotonic()
            tg.cancel_scope.cancel()
    assert time.monotonic() - started >= 1.9
    assert await gone(pid)


@posix_only
async def test_host_aclose_stops_five_stubborn_servers_concurrently(
    setup: Setup,  # noqa: F811 - the imported fixture
) -> None:
    """REQ-CON-009: Host.aclose() disconnects every Client concurrently within 15 s — five
    Servers ignoring EOF and SIGTERM (≈4 s each) take ≈4 s in total, not ≈20 s."""
    table = _toml_server([sys.executable, str(MODERN), "--ignore-eof", "--ignore-term"])
    config = setup.config({f"s{i}": table for i in range(5)})
    host = setup.host(config)
    pids: list[int] = []
    async with host:
        for i in range(5):
            await host.connections.wait_ready(f"s{i}", timeout=10)
            async with host.connections.supervisor(f"s{i}").lease() as client:
                result = await client.call_tool("pid", {})
            pids.append(int(result.content[0].text))  # type: ignore[union-attr]
        started = time.monotonic()
    elapsed = time.monotonic() - started
    assert elapsed < SHUTDOWN_DEADLINE_S
    assert elapsed < 8.0  # concurrent: one escalation, not five in a row
    for pid in pids:
        assert await gone(pid)


# ------------------------------------------------------------------ F8-08: clean exit


async def test_grandchild_gone_after_clean_eof_exit(tmp_path: Path) -> None:
    """REQ-CON-008 (TD DD-002): a Server that spawns a grandchild and exits at once on stdin
    EOF leaves no orphan — after ``stop()`` the grandchild is gone within 4 s (POSIX: the
    remaining process group is signalled; Windows: the SDK's Job Object kills it)."""
    pidfile = tmp_path / "grandchild.pid"
    async with running(stdio_def(_modern("--spawn-grandchild", str(pidfile)))) as (sup, _):
        await wait_state(sup, S.READY)
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        assert alive(grandchild)
        started = time.monotonic()
        await sup.stop()
        assert time.monotonic() - started < 1.5  # the Server itself honoured EOF
    assert await gone(grandchild, within=4.0)


@posix_only
async def test_term_ignoring_grandchild_is_killed(tmp_path: Path) -> None:
    """REQ-CON-008 (TD DD-002): a grandchild that ignores SIGTERM is SIGKILLed 2 s after
    the group SIGTERM, inside the 2 s / 2 s budget."""
    pidfile = tmp_path / "grandchild.pid"
    params = _modern("--spawn-grandchild", str(pidfile), "--grandchild-ignore-term")
    async with running(stdio_def(params)) as (sup, _):
        await wait_state(sup, S.READY)
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        started = time.monotonic()
        await sup.stop()
        elapsed = time.monotonic() - started
    assert await gone(grandchild, within=1.0)
    assert 1.9 <= elapsed < 4.5


@posix_only
async def test_empty_group_cleanup_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-CON-008: when the Server's group is already empty the cleanup raises nothing and
    logs nothing above DEBUG."""
    caplog.set_level(logging.DEBUG, logger="reflex_mcphost")
    elapsed, pid = await _timed_stop(_modern())
    assert elapsed < 1.5
    assert await gone(pid)
    noisy = [
        r
        for r in caplog.records
        if r.name == "reflex_mcphost.core.connection.transports" and r.levelno > logging.DEBUG
    ]
    assert noisy == []


@posix_only
async def test_host_aclose_cleans_three_groups_within_budget(
    setup: Setup,  # noqa: F811 - the imported fixture
    tmp_path: Path,
) -> None:
    """REQ-CON-009, REQ-CON-008: Host.aclose() with three Servers that exit on EOF but leave
    a SIGTERM-ignoring grandchild stays < 15 s (concurrent cleanup) and leaves no orphan."""
    tables = {
        f"s{i}": _toml_server(
            [
                sys.executable,
                str(MODERN),
                "--spawn-grandchild",
                str(tmp_path / f"g{i}.pid"),
                "--grandchild-ignore-term",
            ]
        )
        for i in range(3)
    }
    host = setup.host(setup.config(tables))
    async with host:
        for i in range(3):
            await host.connections.wait_ready(f"s{i}", timeout=10)
        grandchildren = [
            int((tmp_path / f"g{i}.pid").read_text(encoding="utf-8")) for i in range(3)
        ]
        assert all(alive(pid) for pid in grandchildren)
        started = time.monotonic()
    elapsed = time.monotonic() - started
    assert elapsed < SHUTDOWN_DEADLINE_S
    assert elapsed < 8.0  # concurrent: one cleanup window, not three in a row
    for pid in grandchildren:
        assert await gone(pid, within=1.0)
