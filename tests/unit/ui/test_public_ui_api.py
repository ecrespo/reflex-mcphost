"""The public surface of ``reflex_mcphost.ui`` (F6-09a; REQ-UI-007, API §8, Art. 4).

The expected names are read from the API Spec §8 table, so the test fails when the spec and
the package drift apart.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import reflex_mcphost.ui as ui

API_SPEC = Path(__file__).resolve().parents[3] / "specs" / "api" / "mcphost-api-v0.1.md"
STATE_WIRING = {"HostRuntime", "HostState"}
"""Exported for embedding apps (``on_load=HostState.start_watch``, ``HostRuntime.host``):
the state wiring API §8 leaves to the Technical Design (TD §5 ``ui`` row, DD-011)."""


def _section_8_names() -> set[str]:
    text = API_SPEC.read_text(encoding="utf-8")
    section = text.split("## 8. UI layer surface", 1)[1].split("\n## 9.", 1)[0]
    names = set()
    for line in section.splitlines():
        match = re.match(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            names.add(match.group(1))
    return names


def test_all_is_the_api_section_8_surface() -> None:
    """REQ-UI-007: ``reflex_mcphost.ui.__all__`` is the API §8 table (incl. the Phase 7
    ``mcp_app_frame``, F7-05) plus the documented state wiring, and every name resolves."""
    spec_names = _section_8_names()
    assert {"install", "servers_panel", "consent_center", "ReflexPrompter"} <= spec_names
    assert "mcp_app_frame" in spec_names
    assert set(ui.__all__) == spec_names | STATE_WIRING
    for name in ui.__all__:
        assert getattr(ui, name) is not None, name


def test_importing_the_ui_layer_imports_no_reference_app() -> None:
    """REQ-UI-007, Art. 4: importing ``reflex_mcphost.ui`` and resolving every public name
    imports no ``reflex_mcphost.app`` module (checked in a fresh interpreter)."""
    code = (
        "import sys\n"
        "import reflex_mcphost.ui as ui\n"
        "for name in ui.__all__:\n"
        "    getattr(ui, name)\n"
        "bad = sorted(m for m in sys.modules if m.startswith('reflex_mcphost.app'))\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
