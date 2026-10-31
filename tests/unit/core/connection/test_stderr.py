"""Per-Server log ring and stderr pipe reader (F2-06, spike S-2 Q2)."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import anyio
import anyio.to_thread
import pytest

from reflex_mcphost.core.connection.stderr import MAX_LINE_BYTES, LogEntry, LogRing, StderrPipe
from reflex_mcphost.core.secrets import MASK, register_secret

pytestmark = pytest.mark.anyio


def test_ring_is_bounded_and_keeps_newest() -> None:
    """REQ-CON-007: the ring holds the last ``log_buffer_size`` entries."""
    ring = LogRing(3)
    for i in range(5):
        ring.add(f"line {i}")
    assert [e.text for e in ring.snapshot()] == ["line 2", "line 3", "line 4"]
    assert len(ring) == 3
    assert ring.maxlen == 3


def test_ring_redacts_and_publishes() -> None:
    """REQ-CON-007: entries are redacted before they are stored or published (Art. 5)."""
    secret = "stderr-secret-0123456789"  # noqa: S105 - test sentinel
    register_secret(secret)
    published: list[LogEntry] = []
    now = datetime(2026, 10, 31, tzinfo=UTC)
    ring = LogRing(10, on_entry=published.append, clock=lambda: now)
    entry = ring.add(f"token={secret}")
    assert entry.text == f"token={MASK}"
    assert entry.level == "info"
    assert entry.logger == "stderr"
    assert entry.timestamp == now
    assert published == [entry]
    assert secret not in repr(ring.snapshot())


def test_ring_publisher_failure_does_not_lose_entry() -> None:
    """REQ-CON-011: a failing subscriber hook never breaks log capture."""

    def boom(_: LogEntry) -> None:
        raise RuntimeError("subscriber bug")

    ring = LogRing(2, on_entry=boom)
    ring.add("kept")
    assert [e.text for e in ring.snapshot()] == ["kept"]


def test_ring_rejects_empty_size() -> None:
    """REQ-CON-007: log_buffer_size must be >= 1."""
    with pytest.raises(ValueError, match=">= 1"):
        LogRing(0)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe reader")
async def test_pipe_splits_lines_caps_long_ones_and_ends_on_eof() -> None:
    """REQ-CON-007: bytes written to the child end come out as lines; overlong lines are cut,
    invalid UTF-8 is replaced, a trailing partial line is flushed at EOF."""
    pipe = StderrPipe()
    lines: list[str] = []
    fd = pipe.child_end().fileno()
    async with anyio.create_task_group() as tg:
        tg.start_soon(pipe.pump, lines.append)
        data = b"one\ntwo\n\xff bad\n" + b"x" * (MAX_LINE_BYTES + 10) + b"\ntail"
        await anyio.to_thread.run_sync(os.write, fd, data)  # may exceed the pipe capacity
        pipe.close_parent_write_end()
        with anyio.fail_after(5):
            await pipe.eof.wait()
    assert lines[:3] == ["one", "two", "� bad"]
    assert lines[3].endswith(" [truncated]")
    assert lines[-1] == "tail"
    pipe.close()
    pipe.close()  # idempotent


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe reader")
async def test_pipe_sink_errors_do_not_stop_draining() -> None:
    """REQ-CON-011: a failing sink is logged; the reader keeps draining until EOF."""
    pipe = StderrPipe()
    calls: list[str] = []

    def sink(line: str) -> None:
        calls.append(line)
        raise RuntimeError("sink bug")

    async with anyio.create_task_group() as tg:
        tg.start_soon(pipe.pump, sink)
        os.write(pipe.child_end().fileno(), b"a\nb\n")
        pipe.close_parent_write_end()
        with anyio.fail_after(5):
            await pipe.eof.wait()
    assert calls == ["a", "b"]
    pipe.close()
