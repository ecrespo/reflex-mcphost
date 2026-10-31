"""Constitution Art. 1 / Art. 4, Tech Design DD-018: import-linter contracts hold."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _lint_imports(cwd: Path) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("lint-imports")
    assert exe is not None, "import-linter is not installed (dev group)"
    return subprocess.run(  # noqa: S603 - fixed executable, no shell
        [exe, "--no-cache"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_contracts_are_kept() -> None:
    """DD-018: layers, no mcp.server, Reflex-free core and adapter independence are kept."""
    result = _lint_imports(REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 kept, 0 broken" in result.stdout


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the package + config, so a negative test can add a bad module."""
    dst = tmp_path / "repo"
    shutil.copytree(REPO / "src", dst / "src")
    shutil.copy(REPO / "pyproject.toml", dst / "pyproject.toml")
    return dst


def test_importing_mcp_server_breaks_the_build(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Art. 1: any `mcp.server` import under src/ fails import-linter."""
    bad = repo_copy / "src" / "reflex_mcphost" / "core" / "_bad.py"
    bad.write_text("import mcp.server\n")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout
    assert "mcp.server" in result.stdout


def test_core_importing_reflex_breaks_the_build(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Art. 4: `reflex_mcphost.core` must not import `reflex`."""
    bad = repo_copy / "src" / "reflex_mcphost" / "core" / "_bad.py"
    bad.write_text("import reflex\n")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout


def test_python_version() -> None:
    """Art. 8 stack constraint: Python >= 3.11."""
    assert sys.version_info >= (3, 11)
