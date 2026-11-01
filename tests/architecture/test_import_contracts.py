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
    """DD-018: layers, no mcp.server, Reflex-free core, adapter independence,
    adapters-not-core (delta D1), Reflex-free UI view models and actions (F6-02, F8-04)
    and the Reflex-free sandbox proxy (F7-03) are kept."""
    result = _lint_imports(REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7 kept, 0 broken" in result.stdout


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
    bad.write_text("import mcp.server\n", encoding="utf-8")
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
    bad.write_text("import reflex\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout


def test_adapter_importing_core_breaks_the_build(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Art. 4, DD-018 contract 5 (delta D1): an adapter importing `reflex_mcphost.core`
    directly fails import-linter; adapters reach the core only via the public toolset."""
    bad = repo_copy / "src" / "reflex_mcphost" / "adapters" / "_bad.py"
    bad.write_text("from reflex_mcphost.core.invoker import Invoker\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    contract = "Adapters reach the core only through reflex_mcphost.toolset (DD-018, delta D1)"
    assert f"{contract} BROKEN" in result.stdout
    assert "independent of each other (DD-018) KEPT" in result.stdout


def test_python_version() -> None:
    """Art. 8 stack constraint: Python >= 3.11."""
    assert sys.version_info >= (3, 11)


def test_ui_models_importing_reflex_breaks_the_build(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-UI-007, TD §5.1 (delta D7): `reflex_mcphost.ui.models` must not import `reflex`,
    so the view-model mapping and filtering stay testable without Reflex."""
    models = repo_copy / "src" / "reflex_mcphost" / "ui" / "models.py"
    models.write_text(models.read_text(encoding="utf-8") + "\nimport reflex\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    assert (
        "UI view models and actions are Reflex-free (F6-02, F8-04, DD-018 contract 6) BROKEN"
        in result.stdout
    )


def test_ui_actions_importing_reflex_breaks_the_build(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-UI-007, TD DD-018 contract 6 (F8-04, delta D13): `reflex_mcphost.ui.actions`
    must not import `reflex`, so the UI actions stay testable without Reflex."""
    actions = repo_copy / "src" / "reflex_mcphost" / "ui" / "actions.py"
    actions.write_text(actions.read_text(encoding="utf-8") + "\nimport reflex\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(repo_copy / "src"))
    result = _lint_imports(repo_copy)
    assert result.returncode != 0
    assert (
        "UI view models and actions are Reflex-free (F6-02, F8-04, DD-018 contract 6) BROKEN"
        in result.stdout
    )
