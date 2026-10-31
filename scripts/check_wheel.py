"""Wheel content check (Constitution Art. 1, Art. 7).

Fails if the built wheel ships ``tests/`` or ``spikes/`` or any module that imports
``mcp.server``. Usage: ``check_wheel.py dist`` (checks every wheel in the directory).
"""

from __future__ import annotations

import ast
import sys
import zipfile
from pathlib import Path

FORBIDDEN_PREFIXES = ("tests/", "spikes/")


def _imports_mcp_server(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "mcp.server" or a.name.startswith("mcp.server.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mod = node.module
            if mod == "mcp.server" or mod.startswith("mcp.server."):
                return True
            if mod == "mcp" and any(a.name == "server" for a in node.names):
                return True
    return False


def check_wheel(wheel: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if name.startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{name}: must not ship")
            elif name.endswith(".py") and _imports_mcp_server(zf.read(name).decode("utf-8")):
                problems.append(f"{name}: imports mcp.server (Art. 1)")
    return problems


def main(argv: list[str] | None = None) -> int:
    target = Path((argv if argv is not None else sys.argv[1:] or ["dist"])[0])
    wheels = sorted(target.glob("*.whl")) if target.is_dir() else [target]
    if not wheels:
        print(f"no wheel found in {target}")
        return 1
    problems = [p for whl in wheels for p in check_wheel(whl)]
    for p in problems:
        print(p)
    print(f"checked {len(wheels)} wheel(s): {'FAIL' if problems else 'OK'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
