"""``core.apps`` constants, MIME parsing, visibility and purity (F7-01; API §7, DD-012)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from reflex_mcphost.core.apps import constants
from reflex_mcphost.core.apps.constants import (
    EXTENSION_ID,
    MAX_HEIGHT_PX,
    MAX_INFLIGHT,
    MAX_MESSAGE_BYTES,
    MAX_RESOURCE_BYTES,
    MIME_TYPE,
    MIN_HEIGHT_PX,
    TEARDOWN_TIMEOUT_S,
    app_visible,
    is_app_mime,
)
from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.types import Fqn

APPS_DIR = Path(constants.__file__).parent


def _entry(visibility: tuple[str, ...]) -> ToolEntry:
    return ToolEntry(
        fqn=Fqn(server_id="a", name="t"),
        server_id="a",
        name="t",
        input_schema={"type": "object"},
        definition_hash="sha256:x",
        visibility=visibility,  # type: ignore[arg-type]
    )


def test_wire_constants() -> None:
    """REQ-APP-007, REQ-APP-013 (API §7): the extension id, MIME type and limits."""
    assert EXTENSION_ID == "io.modelcontextprotocol/ui"
    assert MIME_TYPE == "text/html;profile=mcp-app"
    assert MAX_RESOURCE_BYTES == 5 * 2**20 == 5_242_880
    assert MAX_MESSAGE_BYTES == 2**20
    assert MAX_INFLIGHT == 4
    assert TEARDOWN_TIMEOUT_S == 2.0
    assert (MIN_HEIGHT_PX, MAX_HEIGHT_PX) == (80, 4000)


@pytest.mark.parametrize(
    "mime",
    [
        "text/html;profile=mcp-app",
        "TEXT/HTML; profile=mcp-app; charset=utf-8",
        "text/html; charset=utf-8; profile=mcp-app",
        'text/html;profile="mcp-app"',
        "Text/Html ; PROFILE=mcp-app",
    ],
)
def test_app_mime_accepted(mime: str) -> None:
    """REQ-APP-013 (API §7): parsed as a media type — ``text/html`` case-insensitive with
    parameter ``profile=mcp-app``; other parameters ignored."""
    assert is_app_mime(mime)


@pytest.mark.parametrize(
    "mime",
    [
        None,
        "",
        "text/html",
        "text/html; charset=utf-8",
        "text/plain;profile=mcp-app",
        "text/html;profile=mcp-app-x",
        "text/html;profile=other;profile=mcp-app",
        "text/htmlx;profile=mcp-app",
        "application/xhtml+xml;profile=mcp-app",
        "text/html;profile",
        42,
    ],
)
def test_app_mime_rejected(mime: object) -> None:
    """REQ-APP-013: ``text/html`` alone and any other type or profile are rejected."""
    assert not is_app_mime(mime)


@pytest.mark.parametrize(
    ("visibility", "visible"),
    [(("model", "app"), True), (("app",), True), (("model",), False), ((), False)],
)
def test_app_visible(visibility: tuple[str, ...], visible: bool) -> None:
    """REQ-APP-008: a tool is App-visible iff its visibility includes ``"app"``."""
    assert app_visible(_entry(visibility)) is visible


FORBIDDEN_ROOTS = {"reflex", "reflex_base", "starlette", "socket", "pathlib", "subprocess"}


def test_core_apps_is_pure() -> None:
    """REQ-APP-002 (Art. 4, DD-018): ``core.apps`` imports no Reflex, no Starlette, no
    socket / file / process I/O module, nothing from ``mcp.server`` (Art. 1) and no higher
    layer."""
    for path in sorted(APPS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert root not in FORBIDDEN_ROOTS, (path.name, name)
                assert not name.startswith("mcp.server"), (path.name, name)
                assert not name.startswith(("reflex_mcphost.ui", "reflex_mcphost.app")), name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", path.name
