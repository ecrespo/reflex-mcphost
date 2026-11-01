"""Terminal summary of the REQ-NFR-001/002 measurements (F8-02): the p95 of every budget
test is printed under "perf measurements" so the CI log carries the numbers for the
execution log, whether the budget is asserted (Linux) or informational (macOS, Windows)."""

from __future__ import annotations

import pytest

MEASUREMENTS: list[str] = []
"""One line per measured budget: name, p95, budget, platform."""


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if MEASUREMENTS:
        terminalreporter.section("perf measurements (REQ-NFR-001/002)")
        for line in MEASUREMENTS:
            terminalreporter.write_line(line)
