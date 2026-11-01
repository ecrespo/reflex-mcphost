"""Workspace materialization of the packaged reference app (DD-016, spike S-5).

The Reflex sources of the reference app ship inside the wheel (``reflex_mcphost/app/web/``:
``rxconfig.py`` and the ``mcphost_app`` package). Reflex writes ``.web/``, ``.states/``,
``reflex.lock`` and more into the directory it runs in, and site-packages may be
read-only (``uvx``), so ``reflex-mcphost run`` runs Reflex in a per-user workspace:
``platformdirs.user_cache_dir("reflex-mcphost")/app/<version>-<content-hash>/``. The key
changes with the version and with the packaged sources (a ``.devN`` rebuild with the same
version gets a new workspace). A workspace is written to a temporary sibling directory and
renamed into place, so concurrent runs never see a half-written one; the package directory
is only ever read.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from importlib import resources
from pathlib import Path

import platformdirs

__all__ = ["APP_FILES", "MARKER", "WorkspaceError", "content_hash", "materialize", "source_root"]

APP_NAME = "reflex-mcphost"
APP_FILES = ("rxconfig.py", "mcphost_app")
"""What a workspace holds (relative to ``source_root()``); bytecode caches are skipped."""
MARKER = ".reflex-mcphost-workspace"
"""Written last into the temporary directory: its presence means the workspace is complete."""
_HASH_CHARS = 16
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")


class WorkspaceError(OSError):
    """The workspace cannot be materialized (CLI exit 3)."""


def source_root() -> Path:
    """The packaged reference-app sources (``reflex_mcphost/app/web``)."""
    return Path(str(resources.files("reflex_mcphost.app") / "web"))


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for name in APP_FILES:
        path = root / name
        if path.is_file():
            out.append(path)
        else:
            out += sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            )
    return out


def content_hash(root: Path) -> str:
    """SHA-256 over the relative path and bytes of every workspace file under ``root``."""
    digest = hashlib.sha256()
    for path in _files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _version() -> str:
    from reflex_mcphost import __version__

    return __version__


def materialize(
    *,
    cache_dir: Path | None = None,
    source: Path | None = None,
    version: str | None = None,
) -> Path:
    """Return the complete workspace for ``source`` at ``version``, creating it if needed.

    ``cache_dir`` defaults to ``platformdirs.user_cache_dir("reflex-mcphost")``, ``source`` to
    ``source_root()`` and ``version`` to the installed package version. Raises
    ``WorkspaceError`` when the directory exists without the completion marker (not ours)
    or cannot be written.
    """
    source = source if source is not None else source_root()
    version = version if version is not None else _version()
    root = Path(cache_dir) if cache_dir is not None else Path(platformdirs.user_cache_dir(APP_NAME))
    parent = root / "app"
    dest = parent / f"{version}-{content_hash(source)[:_HASH_CHARS]}"
    if dest.exists():
        if (dest / MARKER).is_file():
            return dest
        raise WorkspaceError(f"{dest} exists but is not a complete reflex-mcphost workspace")
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", suffix=".tmp", dir=parent))
    try:
        for name in APP_FILES:
            src, out = source / name, tmp / name
            if src.is_dir():
                shutil.copytree(src, out, ignore=_SKIP)
            else:
                shutil.copy2(src, out)
        (tmp / MARKER).write_text(f"{version}\n", encoding="utf-8")
        try:
            os.replace(tmp, dest)
        except OSError:
            # another run renamed its copy first (non-empty target); use the winner's
            if not (dest / MARKER).is_file():
                raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return dest
