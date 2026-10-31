"""S-2 #8: shipped code never builds ``Client(StdioServerParameters(...))``.

That path has no ``errlog``, so stderr capture and exit detection (Tech Design transport
factory) would be silently lost; stdio Clients must be built from our own Transport.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "reflex_mcphost"


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def find_direct_stdio_clients(root: Path) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and _name(node.func) == "Client"
                and node.args
                and isinstance(node.args[0], ast.Call)
                and _name(node.args[0].func) == "StdioServerParameters"
            ):
                hits.append((str(path.relative_to(root)), node.lineno))
    return hits


def test_no_direct_stdio_client_in_src() -> None:
    """S-2 #8: no Client(StdioServerParameters(...)) in reflex_mcphost."""
    assert find_direct_stdio_clients(SRC) == []


def test_detector_catches_direct_stdio_client(tmp_path: Path) -> None:
    """S-2 #8: the detector flags the forbidden construction."""
    (tmp_path / "bad.py").write_text(
        "from mcp import Client, StdioServerParameters\n"
        "c = Client(StdioServerParameters(command='x'))\n",
        encoding="utf-8",
    )
    assert find_direct_stdio_clients(tmp_path) == [("bad.py", 2)]
