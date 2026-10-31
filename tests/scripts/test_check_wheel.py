"""Constitution Art. 1 and Art. 7: the wheel ships only the library, never server code."""

from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.check_wheel import check_wheel


def _wheel(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "reflex_mcphost-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def test_clean_wheel_passes(tmp_path: Path) -> None:
    """Art. 1: a wheel with only library modules passes."""
    whl = _wheel(tmp_path, {"reflex_mcphost/__init__.py": "import mcp\n"})
    assert check_wheel(whl) == []


def test_tests_and_spikes_are_rejected(tmp_path: Path) -> None:
    """Art. 7: tests/ and spikes/ never ship."""
    whl = _wheel(tmp_path, {"tests/test_a.py": "", "spikes/S-1/a.py": ""})
    problems = check_wheel(whl)
    assert len(problems) == 2


def test_mcp_server_import_is_rejected(tmp_path: Path) -> None:
    """Art. 1: no shipped module imports mcp.server, in either import form."""
    whl = _wheel(
        tmp_path,
        {
            "reflex_mcphost/a.py": "from mcp.server import Server\n",
            "reflex_mcphost/b.py": "import mcp.server.fastmcp\n",
            "reflex_mcphost/c.py": "from mcp import server\n",
        },
    )
    assert len(check_wheel(whl)) == 3
