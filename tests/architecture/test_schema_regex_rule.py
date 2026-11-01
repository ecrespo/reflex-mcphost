"""Server regexes never run on stdlib ``re`` in ``core.schema`` (F8-10; REQ-NFR-021, DD-010).

AST rule: ``core/schema.py`` neither imports ``re`` (``import re`` / ``from re import …``)
nor calls ``re.compile`` / ``re.search`` / ``re.match`` / ``re.fullmatch`` (also via an
alias). Server-provided patterns are compiled and matched only through ``regex`` with a
time bound. ``regex`` itself is imported only by ``core.schema`` (Phase 8 layering rule).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "reflex_mcphost"
SCHEMA = SRC / "core" / "schema.py"
_FORBIDDEN_CALLS = frozenset({"compile", "search", "match", "fullmatch"})


def stdlib_re_uses(source: str) -> list[tuple[int, str]]:
    """``(line, what)`` for every stdlib ``re`` import or matching call in ``source``."""
    tree = ast.parse(source)
    aliases: set[str] = set()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re":
                    aliases.add(alias.asname or "re")
                    hits.append((node.lineno, "import re"))
        elif isinstance(node, ast.ImportFrom) and node.module == "re":
            hits.append((node.lineno, "from re import"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in (aliases | {"re"})
            and node.func.attr in _FORBIDDEN_CALLS
        ):
            hits.append((node.lineno, f"re.{node.func.attr}"))
    return sorted(hits)


def regex_importers(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name == "regex" or name.startswith("regex.") for name in names):
                found.append(path.relative_to(root).as_posix())
                break
    return found


def test_core_schema_never_uses_stdlib_re() -> None:
    """REQ-NFR-021: no ``re.compile`` / ``re.search`` / ``re.match`` / ``re.fullmatch`` (and
    no ``re`` import) in ``core.schema``."""
    assert stdlib_re_uses(SCHEMA.read_text(encoding="utf-8")) == []


def test_regex_is_imported_only_by_core_schema() -> None:
    """REQ-NFR-021, Art. 4: the ``regex`` dependency is confined to ``core.schema``."""
    assert regex_importers(SRC) == ["core/schema.py"]


def test_detector_catches_stdlib_re() -> None:
    """REQ-NFR-021: the detector flags every forbidden form, including aliases."""
    bad = (
        "import re\n"
        "import re as _r\n"
        "from re import search\n"
        "re.compile(p)\n"
        "_r.fullmatch(p, s)\n"
        "re.match(p, s)\n"
        "x.search(s)\n"
    )
    assert stdlib_re_uses(bad) == [
        (1, "import re"),
        (2, "import re"),
        (3, "from re import"),
        (4, "re.compile"),
        (5, "re.fullmatch"),
        (6, "re.match"),
    ]
