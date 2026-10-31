"""Constitution Art. 3, Tech Design DD-018: only the Invoker, its MRTR loop and the sampling
handler may reach effectful session methods."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "reflex_mcphost"

#: Attribute names whose call causes an effect on a Server or an LLM provider.
EFFECTFUL_ATTRIBUTES = frozenset({"call_tool", "create_message"})

#: Modules allowed to call them (Art. 3; `core.mrtr` is part of the Invoker — DD-018).
ALLOWED_MODULES = frozenset(
    {
        "reflex_mcphost.core.invoker",
        "reflex_mcphost.core.mrtr",
        "reflex_mcphost.core.handlers.sampling",
    }
)


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def find_effectful_calls(root: Path) -> list[tuple[str, int, str]]:
    """Return (module, line, attribute) for every `<x>.call_tool(...)`-style call under root."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in EFFECTFUL_ATTRIBUTES
            ):
                found.append((_module_name(path, root), node.lineno, node.func.attr))
    return found


def test_effectful_calls_only_in_allowed_modules() -> None:
    """Art. 3: only the Invoker and the sampling handler reach call_tool/create_message."""
    offenders = [c for c in find_effectful_calls(SRC) if c[0] not in ALLOWED_MODULES]
    assert offenders == []


def test_detector_finds_calls(tmp_path: Path) -> None:
    """Art. 3: the detector itself recognises an effectful call (guards against a vacuous test)."""
    pkg = tmp_path / "reflex_mcphost" / "ui"
    pkg.mkdir(parents=True)
    (pkg / "rogue.py").write_text("async def f(s):\n    await s.session.call_tool('x', {})\n")
    calls = find_effectful_calls(tmp_path / "reflex_mcphost")
    assert calls == [("reflex_mcphost.ui.rogue", 2, "call_tool")]
