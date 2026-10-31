"""Constitution Art. 7: coverage gates (core >= 90 %, overall >= 80 %)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_coverage import Gate, evaluate


def _coverage_json(tmp_path: Path, files: dict[str, tuple[int, int]]) -> Path:
    """Write a minimal coverage.py JSON report: path -> (covered_lines, num_statements)."""
    data = {
        "files": {
            path: {"summary": {"covered_lines": cov, "num_statements": total}}
            for path, (cov, total) in files.items()
        }
    }
    out = tmp_path / "coverage.json"
    out.write_text(json.dumps(data))
    return out


def test_passes_when_both_gates_met(tmp_path: Path) -> None:
    """Art. 7: 95 % core and 85 % total pass."""
    report = _coverage_json(
        tmp_path,
        {
            "src/reflex_mcphost/core/a.py": (95, 100),
            "src/reflex_mcphost/ui/b.py": (75, 100),
        },
    )
    result = evaluate(report, Gate(core_min=90.0, total_min=80.0))
    assert result.ok
    assert result.core_pct == 95.0
    assert result.total_pct == 85.0


def test_fails_when_core_below_90(tmp_path: Path) -> None:
    """Art. 7: core at 89 % fails even if the total is fine."""
    report = _coverage_json(
        tmp_path,
        {
            "src/reflex_mcphost/core/a.py": (89, 100),
            "src/reflex_mcphost/ui/b.py": (100, 100),
        },
    )
    result = evaluate(report, Gate(core_min=90.0, total_min=80.0))
    assert not result.ok
    assert "core" in result.message


def test_fails_when_total_below_80(tmp_path: Path) -> None:
    """Art. 7: total at 70 % fails."""
    report = _coverage_json(
        tmp_path,
        {
            "src/reflex_mcphost/core/a.py": (100, 100),
            "src/reflex_mcphost/ui/b.py": (40, 100),
        },
    )
    result = evaluate(report, Gate(core_min=90.0, total_min=80.0))
    assert not result.ok
    assert "total" in result.message


def test_core_without_statements_counts_as_covered(tmp_path: Path) -> None:
    """Art. 7: an empty core (only docstrings) does not fail the gate."""
    report = _coverage_json(tmp_path, {"src/reflex_mcphost/core/__init__.py": (0, 0)})
    result = evaluate(report, Gate(core_min=90.0, total_min=80.0))
    assert result.ok
