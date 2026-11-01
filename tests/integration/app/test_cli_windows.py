"""The CLI's Ctrl-C forwarding on Windows (F8-14; REQ-UI-001, TD DD-016, delta D12).

On Windows ``spawn_child`` starts Reflex with ``CREATE_NEW_PROCESS_GROUP`` and a Ctrl-C is
forwarded as ``CTRL_BREAK_EVENT`` (``cli.INTERRUPT_SIGNAL``), the only console event a new
process group receives. Here the real helper spawns a tiny Python child that counts the
``SIGBREAK`` it receives; the test forwards the event exactly as ``_stop`` does and checks
the child got it once and exited within the grace period. Runs on the ``windows-latest``
CI legs only; skipped elsewhere, and where the runner has no console (the event cannot be
generated without one).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from reflex_mcphost.app import cli

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only: CTRL_BREAK_EVENT")
READY_TIMEOUT_S = 30.0

CHILD = """\
import signal, sys, time
from pathlib import Path

out = Path(sys.argv[1])
breaks = []

def on_break(signum, frame):
    breaks.append(time.monotonic())

signal.signal(signal.SIGBREAK, on_break)
out.write_text("ready\\n", encoding="utf-8")
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    if breaks and time.monotonic() - breaks[0] > 0.5:  # a duplicate would arrive by now
        out.write_text(f"break {len(breaks)}\\n", encoding="utf-8")
        sys.exit(0)
    time.sleep(0.05)
out.write_text("timeout\\n", encoding="utf-8")
sys.exit(3)
"""


def _has_console() -> bool:  # pragma: no cover - Windows CI
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    return bool(kernel32.GetConsoleCP())


def _read(path: Path) -> str:  # pragma: no cover - Windows CI
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def test_ctrl_break_is_forwarded_once_and_the_child_exits(
    tmp_path: Path,
) -> None:  # pragma: no cover - Windows CI
    """REQ-UI-001, DD-016 (delta D12): the child started by ``spawn_child`` (its own process
    group) receives the forwarded ``CTRL_BREAK_EVENT`` exactly once and exits 0 within
    ``STOP_GRACE_S``; the test process (another group) is not interrupted."""
    if not _has_console():
        pytest.skip("no console: CTRL_BREAK_EVENT cannot be generated on this runner")
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    report = tmp_path / "report.txt"
    child = cli.spawn_child([sys.executable, str(script), str(report)], tmp_path, dict(os.environ))
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while _read(report) != "ready":
            assert child.poll() is None, f"the child exited early: {_read(report)!r}"
            assert time.monotonic() < deadline, "the child never became ready"
            time.sleep(0.05)
        assert cli.INTERRUPT_SIGNAL == cli.signal.CTRL_BREAK_EVENT  # type: ignore[attr-defined,unused-ignore]
        started = time.monotonic()
        try:
            child.send_signal(cli.INTERRUPT_SIGNAL)
        except OSError as exc:
            pytest.skip(f"no console: CTRL_BREAK_EVENT failed ({type(exc).__name__})")
        code = child.wait(timeout=cli.STOP_GRACE_S)
        elapsed = time.monotonic() - started
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert code == 0
    assert _read(report) == "break 1"
    assert elapsed < cli.STOP_GRACE_S
