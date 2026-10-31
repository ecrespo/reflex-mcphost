"""Coverage gate (Constitution Art. 7): core >= 90 %, overall >= 80 %.

Reads a coverage.py JSON report (``coverage json``). Exit code 0 when the gates are met,
1 otherwise. ``--report-only`` always exits 0 (used until the first core modules land, F2-01).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

CORE_MARKER = "reflex_mcphost/core/"


@dataclass(frozen=True)
class Gate:
    core_min: float = 90.0
    total_min: float = 80.0


@dataclass(frozen=True)
class Result:
    ok: bool
    core_pct: float
    total_pct: float
    message: str


def _pct(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(100.0 * covered / total, 2)


def evaluate(report: Path, gate: Gate) -> Result:
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    core_cov = core_tot = all_cov = all_tot = 0
    for path, data in files.items():
        cov = int(data["summary"]["covered_lines"])
        tot = int(data["summary"]["num_statements"])
        all_cov += cov
        all_tot += tot
        if CORE_MARKER in path.replace("\\", "/"):
            core_cov += cov
            core_tot += tot
    core_pct, total_pct = _pct(core_cov, core_tot), _pct(all_cov, all_tot)
    failures = []
    if core_pct < gate.core_min:
        failures.append(f"core {core_pct:.2f}% < {gate.core_min:.0f}%")
    if total_pct < gate.total_min:
        failures.append(f"total {total_pct:.2f}% < {gate.total_min:.0f}%")
    message = "; ".join(failures) or f"core {core_pct:.2f}%, total {total_pct:.2f}%: OK"
    return Result(not failures, core_pct, total_pct, message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?", default=Path("coverage.json"))
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate(args.report, Gate())
    print(result.message)
    return 0 if result.ok or args.report_only else 1


if __name__ == "__main__":
    sys.exit(main())
