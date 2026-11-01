"""Test-only launcher: runs the argv stored (as a JSON list) in the file given as argument.

One Server definition (``python era_switch.py FILE``) — hence one era-cache key — can thus be
served by the modern fixture first and the legacy one later (cached-era invalidation,
REQ-CON-019). stdio is inherited; the exit code is passed through.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    argv = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sys.exit(subprocess.call(argv))  # noqa: S603 - test fixture runs its own argv
