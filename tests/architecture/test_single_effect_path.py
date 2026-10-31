"""Constitution Art. 3, Tech Design DD-018: the single effect path.

Only the Invoker and its MRTR loop (``core.invoker``, ``core.mrtr``) may send ``tools/call``;
only the sampling handler (Phase 4) may reach ``create_message``. The detector looks for
attribute calls (``x.call_tool(...)``, ``x.session.call_tool(...)``), ``getattr(x,
"call_tool")`` and hand-built ``CallToolRequest`` objects (a ``send_request`` bypass).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "reflex_mcphost"

#: Effectful name → the only modules allowed to use it (Art. 3).
ALLOWED: dict[str, frozenset[str]] = {
    "call_tool": frozenset({"reflex_mcphost.core.invoker", "reflex_mcphost.core.mrtr"}),
    "CallToolRequest": frozenset({"reflex_mcphost.core.mrtr"}),
    "create_message": frozenset({"reflex_mcphost.core.handlers.sampling"}),
}


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _effect(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in ALLOWED:
        return func.attr
    if isinstance(func, ast.Name) and func.id in ALLOWED:
        return func.id
    if (
        isinstance(func, ast.Name)
        and func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in ALLOWED
    ):
        return str(node.args[1].value)
    return None


def find_effectful_calls(root: Path) -> list[tuple[str, int, str]]:
    """Return (module, line, name) for every effectful call under ``root``."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (name := _effect(node)) is not None:
                found.append((_module_name(path, root), getattr(node, "lineno", 0), name))
    return found


def test_effectful_calls_only_in_allowed_modules() -> None:
    """Art. 3: no module outside core.invoker / core.mrtr calls call_tool (and only the
    sampling handler calls create_message)."""
    offenders = [c for c in find_effectful_calls(SRC) if c[0] not in ALLOWED[c[2]]]
    assert offenders == []


def test_the_effect_path_exists() -> None:
    """Art. 3: the allowed path really sends tools/call (guards against a vacuous pass)."""
    modules = {(module, name) for module, _, name in find_effectful_calls(SRC)}
    assert ("reflex_mcphost.core.mrtr", "call_tool") in modules


def test_detector_finds_calls(tmp_path: Path) -> None:
    """Art. 3: the detector recognises attribute calls, getattr and request construction."""
    pkg = tmp_path / "reflex_mcphost" / "ui"
    pkg.mkdir(parents=True)
    (pkg / "rogue.py").write_text(
        "async def f(s):\n"
        "    await s.session.call_tool('x', {})\n"
        "    await getattr(s, 'call_tool')('x')\n"
        "    await s.send_request(CallToolRequest(params=None), None)\n",
        encoding="utf-8",
    )
    calls = find_effectful_calls(tmp_path / "reflex_mcphost")
    assert sorted(calls) == [
        ("reflex_mcphost.ui.rogue", 2, "call_tool"),
        ("reflex_mcphost.ui.rogue", 3, "call_tool"),
        ("reflex_mcphost.ui.rogue", 4, "CallToolRequest"),
    ]
