"""REQ-CAT-011: tool annotations are untrusted display hints; no core logic branches on them.

Only ``core/catalog.py`` may name an annotation hint (it stores the SDK ``ToolAnnotations``
as display data). Any other ``core`` module — consent, invoker, audit — reading
``readOnlyHint``/``destructiveHint``/``idempotentHint``/``openWorldHint`` (wire or SDK
snake_case spelling; as an attribute, a name or a string key) fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "reflex_mcphost" / "core"
ALLOWED = frozenset({CORE / "catalog.py"})
HINTS = frozenset(
    {
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
        "read_only_hint",
        "destructive_hint",
        "idempotent_hint",
        "open_world_hint",
    }
)


def find_hint_references(root: Path, allowed: frozenset[Path] = frozenset()) -> list[str]:
    """``path:line:name`` for every reference to an annotation hint under ``root``."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = node.value
            elif isinstance(node, ast.keyword):
                name = node.arg
            if name in HINTS:
                found.append((str(path.relative_to(root)), node.lineno, name))
    return [f"{p}:{line}:{name}" for p, line, name in sorted(found)]


def test_no_core_module_reads_annotation_hints() -> None:
    """REQ-CAT-011: outside catalog.py no core module references an annotation hint, so
    consent and catalog logic cannot change behaviour on them."""
    assert find_hint_references(CORE, ALLOWED) == []


def test_detector_finds_every_spelling(tmp_path: Path) -> None:
    """REQ-CAT-011: the scan recognises attribute, keyword and string-key references
    (guards against a vacuous test)."""
    (tmp_path / "consent.py").write_text(
        "def allow(tool, d):\n"
        "    if tool.annotations.read_only_hint:\n"
        "        return True\n"
        "    f(destructive_hint=1)\n"
        "    return d['openWorldHint']\n",
        encoding="utf-8",
    )
    found = find_hint_references(tmp_path)
    assert [f.rsplit(":", 1)[1] for f in found] == [
        "read_only_hint",
        "destructive_hint",
        "openWorldHint",
    ]
