"""Pre-publish checks of the built distributions (Constitution Art. 1, Art. 7; DD-015, TD §9.1).

Wheel: no repository-only paths (``tests/``, ``spikes/``, ``specs/``, ``changes/``,
``.claude/``), no module importing ``mcp.server``, ``METADATA`` with the expected version,
the extras ``pydantic-ai``, ``langgraph`` and ``all`` and a Markdown long description, and
every ``console_scripts`` entry point resolving to a module (and top-level name) shipped in
the wheel. Sdist: the same forbidden paths, ``mcp.server`` and version checks.

Usage: ``check_wheel.py dist [--expect-version X]`` checks every wheel and sdist in the
directory (at least one of each); a single ``.whl`` or ``.tar.gz`` path is checked alone.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import sys
import tarfile
import zipfile
from email.message import Message
from email.parser import HeaderParser
from pathlib import Path

FORBIDDEN_PREFIXES = ("tests/", "spikes/", "specs/", "changes/", ".claude/")
REQUIRED_EXTRAS = ("pydantic-ai", "langgraph", "all")
SDIST_SUFFIX = ".tar.gz"


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


def _defines(source: str, name: str) -> bool:
    """True if ``name`` is bound at the module's top level (def, class, assignment, import)."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                return True
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return True
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return True
        elif isinstance(node, ast.Import | ast.ImportFrom) and any(
            (a.asname or a.name.split(".")[0]) == name for a in node.names
        ):
            return True
    return False


def _version_from_name(filename: str, suffix: str) -> str:
    """``reflex_mcphost-0.1.0a1-py3-none-any.whl`` / ``reflex_mcphost-0.1.0a1.tar.gz``."""
    stem = filename.removesuffix(suffix)
    parts = stem.split("-")
    return parts[1] if len(parts) > 1 else ""


def _version_problems(
    label: str, filename: str, meta: Message, suffix: str, expect: str
) -> list[str]:
    problems: list[str] = []
    in_name = _version_from_name(filename, suffix)
    if in_name != expect:
        problems.append(f"{label}: file name version {in_name} != expected {expect}")
    in_meta = meta.get("Version", "")
    if in_meta != expect:
        problems.append(f"{label}: {_meta_name(suffix)} version {in_meta} != expected {expect}")
    return problems


def _meta_name(suffix: str) -> str:
    return "PKG-INFO" if suffix == SDIST_SUFFIX else "METADATA"


def _console_script_problems(
    label: str, entry_points: str, names: set[str], read: dict[str, str]
) -> list[str]:
    parser = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keep script names as-is
    parser.read_string(entry_points)
    if not parser.has_section("console_scripts"):
        return []
    problems: list[str] = []
    for script, target in parser.items("console_scripts"):
        module, _, attr = target.partition(":")
        module, attr = module.strip(), attr.strip().split(".")[0].split("[")[0].strip()
        base = module.replace(".", "/")
        path = next((p for p in (f"{base}.py", f"{base}/__init__.py") if p in names), None)
        if path is None:
            problems.append(
                f"{label}: console script {script!r} -> {target.strip()}: "
                f"module {module} not in the wheel"
            )
        elif attr and not _defines(read[path], attr):
            problems.append(
                f"{label}: console script {script!r} -> {target.strip()}: "
                f"{module} does not define {attr}"
            )
    return problems


def check_wheel(wheel: Path, expect_version: str | None = None) -> list[str]:
    label = wheel.name
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        sources: dict[str, str] = {}
        for name in sorted(names):
            if name.startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{name}: must not ship")
                continue
            if name.endswith(".py"):
                sources[name] = zf.read(name).decode("utf-8")
                if _imports_mcp_server(sources[name]):
                    problems.append(f"{name}: imports mcp.server (Art. 1)")
        metadata = sorted(
            n for n in names if n.count("/") == 1 and n.endswith(".dist-info/METADATA")
        )
        if not metadata:
            problems.append(f"{label}: no .dist-info/METADATA")
            return problems
        dist_info = metadata[0].removesuffix("/METADATA")
        meta = HeaderParser().parsestr(zf.read(metadata[0]).decode("utf-8"))
        ep_name = f"{dist_info}/entry_points.txt"
        if ep_name in names:
            ep = zf.read(ep_name).decode("utf-8")
            problems += _console_script_problems(label, ep, names, sources)
    declared = set(meta.get_all("Provides-Extra") or [])
    problems += [
        f"{label}: METADATA does not declare Provides-Extra: {extra}"
        for extra in REQUIRED_EXTRAS
        if extra not in declared
    ]
    content_type = meta.get("Description-Content-Type", "")
    if not content_type.startswith("text/markdown"):
        problems.append(
            f"{label}: METADATA Description-Content-Type is {content_type or 'missing'!s}, "
            "expected text/markdown"
        )
    if expect_version is not None:
        problems += _version_problems(label, label, meta, ".whl", expect_version)
    return problems


def check_sdist(sdist: Path, expect_version: str | None = None) -> list[str]:
    label = sdist.name
    problems: list[str] = []
    pkg_info: Message | None = None
    with tarfile.open(sdist, "r:gz") as tf:
        for member in tf.getmembers():
            _, _, rel = member.name.partition("/")
            if rel.startswith(FORBIDDEN_PREFIXES) or (rel + "/").startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{label}: {member.name}: must not ship")
                continue
            if not member.isfile():
                continue
            handle = tf.extractfile(member)
            if handle is None:  # pragma: no cover - regular files always have a handle
                continue
            body = handle.read().decode("utf-8", errors="replace")
            if rel == "PKG-INFO":
                pkg_info = HeaderParser().parsestr(body)
            elif rel.endswith(".py") and _imports_mcp_server(body):
                problems.append(f"{label}: {member.name}: imports mcp.server (Art. 1)")
    if pkg_info is None:
        problems.append(f"{label}: no PKG-INFO")
    elif expect_version is not None:
        problems += _version_problems(label, label, pkg_info, SDIST_SUFFIX, expect_version)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", default="dist", type=Path)
    parser.add_argument("--expect-version", default=None)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    target: Path = args.target
    if target.is_dir():
        wheels = sorted(target.glob("*.whl"))
        sdists = sorted(target.glob(f"*{SDIST_SUFFIX}"))
        if not wheels:
            print(f"no wheel found in {target}")
            return 1
        if not sdists:
            print(f"no sdist found in {target}")
            return 1
    elif target.name.endswith(SDIST_SUFFIX):
        wheels, sdists = [], [target]
    else:
        wheels, sdists = [target], []
    problems = [p for whl in wheels for p in check_wheel(whl, args.expect_version)]
    problems += [p for sd in sdists for p in check_sdist(sd, args.expect_version)]
    for p in problems:
        print(p)
    version = f", version {args.expect_version}" if args.expect_version else ""
    print(
        f"checked {len(wheels)} wheel(s) and {len(sdists)} sdist(s){version}: "
        f"{'FAIL' if problems else 'OK'}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
