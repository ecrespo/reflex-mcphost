"""Crash-storm fixture Server (F8-03; REQ-NFR-020, REQ-CON-011; test-only code).

Run: ``python crash_server.py [--stderr-lines N] [--pidfile PATH]``. Every start writes ``N``
lines to stderr (default 3), appends its pid to ``PATH`` (one per line) when given, then
waits for the Client's first message on stdin and exits with status 3 before answering —
a Server that crashes on every connect. It imports nothing from ``mcp`` (Constitution
Art. 1 concerns shipped code; this file never reaches the wheel, ``scripts/check_wheel.py``).
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stderr-lines", type=int, default=3)
    parser.add_argument("--pidfile", default=None)
    opts = parser.parse_args()
    if opts.pidfile:
        with open(opts.pidfile, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n")
    for n in range(opts.stderr_lines):
        sys.stderr.write(f"crash fixture: starting ({n + 1}/{opts.stderr_lines})\n")
    sys.stderr.flush()
    sys.stdin.readline()  # the Client's first message (discover / initialize)
    sys.stderr.write("crash fixture: crashing on connect\n")
    sys.stderr.flush()
    os._exit(3)


if __name__ == "__main__":
    main()
