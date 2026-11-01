"""REQ-NFR-030, Constitution Art. 7 / Art. 8 (F8-04): static checks of ``ci.yml``.

The CI matrix is what makes REQ-NFR-030 ("Linux, macOS, Windows; Python >= 3.11; CI runs
the core suite on all three") true, so its shape is pinned here. ``yaml`` comes with the
``test`` dependency group (``langgraph`` -> ``langchain-core``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def test_test_job_matrix_covers_three_os_and_two_pythons(workflow: dict[str, Any]) -> None:
    """REQ-NFR-030: the ``test`` job runs on Linux, macOS and Windows, on the lowest
    supported Python (3.11) and the newest pinned one (3.13), without fail-fast."""
    job = workflow["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert {"ubuntu-latest", "macos-latest", "windows-latest"} <= set(matrix["os"])
    assert {"3.11", "3.13"} <= {str(v) for v in matrix["python"]}
    assert job["strategy"]["fail-fast"] is False
    assert job["runs-on"] == "${{ matrix.os }}"


def test_test_job_runs_the_core_suite_without_e2e(workflow: dict[str, Any]) -> None:
    """REQ-NFR-030: every matrix leg runs the whole suite except ``e2e`` (which has its own
    job), then the coverage gate (Art. 7)."""
    runs = _runs(workflow["jobs"]["test"])
    assert any("pytest" in r and '-m "not e2e"' in r for r in runs), runs
    assert any("scripts/check_coverage.py" in r for r in runs), runs


def test_strict_req_gate_covers_every_family(workflow: dict[str, Any]) -> None:
    """Art. 7, REQ-NFR-030 (F8-04): the strict REQ-coverage step runs over **all**
    families — no ``--families`` restriction is left in CI."""
    runs = _runs(workflow["jobs"]["test"])
    strict = [r for r in runs if "check_req_coverage.py" in r and "--strict" in r]
    assert len(strict) == 1, runs
    assert "--families" not in strict[0]
    assert not any("check_req_coverage.py" in r and "--families" in r for r in runs)
