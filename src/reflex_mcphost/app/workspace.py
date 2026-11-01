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

Pruning (TD DD-016, API §6, delta D12): every ``materialize`` touches the current
workspace's marker (its mtime is the last use) and then, best effort, removes sibling
directories of ``…/app/`` that carry the marker and were last used ≥ 7 days ago, and
leftover ``.<name>.<random>.tmp`` directories older than 1 day. A directory without the
marker (and anything that is not a real directory, such as a symlink) is never touched. A
workspace is first renamed to a leftover name and then deleted, so a failure half-way never
leaves a marker-less directory under a workspace name (it is retried as a leftover on a
later run). Any error is logged at DEBUG and ignored (Windows: files in use).
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from importlib import resources
from pathlib import Path

import platformdirs

from reflex_mcphost.core.logging import get_logger

__all__ = [
    "APP_FILES",
    "LEFTOVER_MAX_AGE_S",
    "MARKER",
    "WORKSPACE_MAX_AGE_S",
    "WorkspaceError",
    "content_hash",
    "materialize",
    "prune",
    "source_root",
]

APP_NAME = "reflex-mcphost"
APP_FILES = ("rxconfig.py", "mcphost_app")
"""What a workspace holds (relative to ``source_root()``); bytecode caches are skipped."""
MARKER = ".reflex-mcphost-workspace"
"""Written last into the temporary directory: its presence means the workspace is complete."""
WORKSPACE_MAX_AGE_S = 7 * 24 * 3600.0
"""A marked sibling workspace last used this long ago (marker mtime) is removed."""
LEFTOVER_MAX_AGE_S = 24 * 3600.0
"""A leftover temporary directory older than this (directory mtime) is removed."""
_HASH_CHARS = 16
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")
_LEFTOVER = re.compile(rf"\..+-[0-9a-f]{{{_HASH_CHARS}}}\.[A-Za-z0-9_]+\.tmp")
"""``.<version>-<hash>.<random>.tmp``: ``mkdtemp`` copies and renamed-away workspaces."""
_log = get_logger(__name__)


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
            prune(dest)
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
    prune(dest)
    return dest


# ------------------------------------------------------------------ pruning (delta D12)


def _real_dir(path: Path) -> bool:
    """A directory that is not a symlink (never followed)."""
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _marker_mtime(path: Path) -> float | None:
    """The mtime of ``path``'s marker when it is a regular file, else ``None``."""
    try:
        info = (path / MARKER).lstat()
    except OSError:
        return None
    return info.st_mtime if stat.S_ISREG(info.st_mode) else None


def _remove(path: Path, what: str) -> None:
    """Rename ``path`` to a leftover name, then delete it; errors are DEBUG and ignored."""
    try:
        if _LEFTOVER.fullmatch(path.name) is None:
            doomed = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
            os.replace(path, doomed)
            path = doomed
        shutil.rmtree(path)
    except OSError as exc:
        _log.debug("workspace pruning skipped a %s: %s", what, type(exc).__name__)


def prune(current: Path, *, now: float | None = None) -> None:
    """Touch ``current``'s marker, then remove stale siblings (best effort, never raises).

    Removed: sibling directories that carry the marker and whose marker is at least
    ``WORKSPACE_MAX_AGE_S`` old, and ``.<name>.<random>.tmp`` leftovers whose mtime is at
    least ``LEFTOVER_MAX_AGE_S`` old. Kept: ``current``, every directory without the
    marker, symlinks and files.
    """
    try:
        os.utime(current / MARKER)
        clock = time.time() if now is None else now
        siblings = list(current.parent.iterdir())
    except OSError as exc:
        _log.debug("workspace pruning skipped: %s", type(exc).__name__)
        return
    for path in siblings:
        if path.name == current.name or not _real_dir(path):
            continue
        if _LEFTOVER.fullmatch(path.name) is not None:
            try:
                age = clock - path.lstat().st_mtime
            except OSError:
                continue
            if age >= LEFTOVER_MAX_AGE_S:
                _remove(path, "leftover")
            continue
        used = _marker_mtime(path)
        if used is not None and clock - used >= WORKSPACE_MAX_AGE_S:
            _remove(path, "workspace")
