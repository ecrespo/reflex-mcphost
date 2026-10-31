"""Cross-platform process liveness for tests (POSIX ``kill(pid, 0)`` would *terminate* the
process on Windows, where ``os.kill`` maps any other signal to ``TerminateProcess``)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _windows_alive(pid: int) -> bool:  # pragma: no cover - exercised on Windows CI only
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process (a zombie awaiting its reaper counts as dead)."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI only
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - pid reused by another user's process
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):  # pragma: no cover - raced with the exit
            return False
    return True
