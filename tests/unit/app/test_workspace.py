"""Workspace materialization of the packaged reference app (F6-08; DD-016, spike S-5).

The packaged Reflex sources are copied to ``<cache>/app/<version>-<content-hash>/``
through a temporary sibling renamed into place; the package directory is never written.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from reflex_mcphost.app import workspace
from reflex_mcphost.app.workspace import WorkspaceError, materialize


def _source(tmp_path: Path, body: str = "APP = 1\n") -> Path:
    root = tmp_path / "src"
    (root / "mcphost_app").mkdir(parents=True)
    (root / "rxconfig.py").write_text("config = None\n", encoding="utf-8")
    (root / "mcphost_app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "mcphost_app" / "mcphost_app.py").write_text(body, encoding="utf-8")
    (root / "mcphost_app" / "__pycache__").mkdir()
    (root / "mcphost_app" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (root / "__init__.py").write_text("", encoding="utf-8")  # not part of the workspace
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(root.rglob("*"))
    }


def test_copies_rxconfig_and_app_module_only(tmp_path: Path) -> None:
    """DD-016: the workspace holds rxconfig.py and the app package, without bytecode."""
    dest = materialize(cache_dir=tmp_path / "cache", source=_source(tmp_path), version="1.0")
    assert dest.parent == tmp_path / "cache" / "app"
    assert dest.name.startswith("1.0-")
    assert (dest / "rxconfig.py").read_text(encoding="utf-8") == "config = None\n"
    assert (dest / "mcphost_app" / "mcphost_app.py").read_text(encoding="utf-8") == "APP = 1\n"
    assert not (dest / "__init__.py").exists()
    assert not (dest / "mcphost_app" / "__pycache__").exists()


def test_same_content_reuses_the_directory(tmp_path: Path) -> None:
    """DD-016: an unchanged version and content reuse the workspace (and its .web/)."""
    source = _source(tmp_path)
    first = materialize(cache_dir=tmp_path / "cache", source=source, version="1.0")
    (first / ".web").mkdir()  # what Reflex writes there on the first run
    second = materialize(cache_dir=tmp_path / "cache", source=source, version="1.0")
    assert second == first
    assert (second / ".web").is_dir()


def test_content_change_gives_a_new_directory(tmp_path: Path) -> None:
    """S-5: the key is version + content hash, so a .devN rebuild never reuses a stale one."""
    cache = tmp_path / "cache"
    first = materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
    changed = _source(tmp_path / "v2", body="APP = 2\n")
    second = materialize(cache_dir=cache, source=changed, version="1.0")
    assert second != first
    assert (second / "mcphost_app" / "mcphost_app.py").read_text(encoding="utf-8") == "APP = 2\n"
    assert (first / "mcphost_app" / "mcphost_app.py").read_text(encoding="utf-8") == "APP = 1\n"


def test_version_change_gives_a_new_directory(tmp_path: Path) -> None:
    """DD-016: a new version never reuses the workspace of another one."""
    source = _source(tmp_path)
    first = materialize(cache_dir=tmp_path / "cache", source=source, version="1.0")
    second = materialize(cache_dir=tmp_path / "cache", source=source, version="1.1")
    assert first != second
    assert second.name.startswith("1.1-")


def test_concurrent_materializations_leave_one_complete_directory(tmp_path: Path) -> None:
    """DD-016: two concurrent runs see one complete workspace and no partial sibling."""
    source = _source(tmp_path)
    cache = tmp_path / "cache"
    barrier = threading.Barrier(4)
    results: list[Path] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(materialize(cache_dir=cache, source=source, version="1.0"))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(results)) == 1
    entries = sorted(p.name for p in (cache / "app").iterdir())
    assert entries == [results[0].name]
    dest = results[0]
    assert (dest / workspace.MARKER).is_file()
    assert (dest / "mcphost_app" / "mcphost_app.py").read_text(encoding="utf-8") == "APP = 1\n"


def test_a_directory_without_marker_is_an_error(tmp_path: Path) -> None:
    """DD-016: a workspace that is not ours (no marker) is never used or overwritten."""
    source = _source(tmp_path)
    dest = materialize(cache_dir=tmp_path / "cache", source=source, version="1.0")
    (dest / workspace.MARKER).unlink()
    with pytest.raises(WorkspaceError, match="not a complete"):
        materialize(cache_dir=tmp_path / "cache", source=source, version="1.0")


def test_packaged_sources_are_never_written(tmp_path: Path) -> None:
    """DD-016: materializing from the installed package writes nothing under it."""
    source = workspace.source_root()
    assert (source / "rxconfig.py").is_file()
    assert (source / "mcphost_app" / "mcphost_app.py").is_file()
    before = _snapshot(source)
    dest = materialize(cache_dir=tmp_path / "cache")
    assert _snapshot(source) == before
    assert not dest.is_relative_to(source)
    assert (dest / "rxconfig.py").read_bytes() == (source / "rxconfig.py").read_bytes()


def test_default_cache_dir_is_the_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DD-016: the default root is platformdirs.user_cache_dir("reflex-mcphost")."""
    seen: list[str] = []

    def fake_cache_dir(name: str, **_: object) -> str:
        seen.append(name)
        return str(tmp_path / "user-cache")

    monkeypatch.setattr(workspace.platformdirs, "user_cache_dir", fake_cache_dir)
    dest = materialize(source=_source(tmp_path), version="1.0")
    assert seen == ["reflex-mcphost"]
    assert dest.parent == tmp_path / "user-cache" / "app"


# ------------------------------------------------------------------ pruning (F8-14, delta D12)

DAY = 24 * 3600.0


def _old_workspace(parent: Path, name: str, age_s: float, *, marker: bool = True) -> Path:
    """A sibling workspace whose marker (or, unmarked, the directory) was last used
    ``age_s`` seconds ago."""
    path = parent / name
    (path / ".web").mkdir(parents=True)
    (path / "rxconfig.py").write_text("config = None\n", encoding="utf-8")
    stamp = time.time() - age_s
    if marker:
        (path / workspace.MARKER).write_text("0.0\n", encoding="utf-8")
        os.utime(path / workspace.MARKER, (stamp, stamp))
    os.utime(path, (stamp, stamp))
    return path


def _leftover(parent: Path, name: str, age_s: float) -> Path:
    path = parent / name
    (path / "mcphost_app").mkdir(parents=True)
    stamp = time.time() - age_s
    os.utime(path / "mcphost_app", (stamp, stamp))
    os.utime(path, (stamp, stamp))
    return path


def test_run_prunes_old_marked_workspaces_and_keeps_the_rest(tmp_path: Path) -> None:
    """REQ-UI-001, DD-016 (delta D12): materializing touches the current marker, removes a
    marked sibling last used ≥ 7 days ago, keeps a recent marked one, never touches an
    unmarked directory (however old) or a plain file."""
    cache = tmp_path / "cache"
    parent = cache / "app"
    parent.mkdir(parents=True)
    old = _old_workspace(parent, "0.0.1-0123456789abcdef", 8 * DAY)
    recent = _old_workspace(parent, "0.0.2-0123456789abcdef", 6 * DAY)
    foreign = _old_workspace(parent, "not-ours", 400 * DAY, marker=False)
    stray = parent / "notes.txt"
    stray.write_text("keep\n", encoding="utf-8")
    dest = materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
    assert not old.exists()
    assert recent.is_dir()
    assert (recent / workspace.MARKER).is_file()
    assert foreign.is_dir()
    assert (foreign / ".web").is_dir()
    assert stray.is_file()
    assert (dest / workspace.MARKER).is_file()


def test_the_current_workspace_is_touched_and_never_pruned(tmp_path: Path) -> None:
    """REQ-UI-001, DD-016 (delta D12): reusing a workspace whose marker is months old
    refreshes the marker's mtime (last use) and keeps the workspace."""
    cache = tmp_path / "cache"
    source = _source(tmp_path)
    dest = materialize(cache_dir=cache, source=source, version="1.0")
    stamp = time.time() - 90 * DAY
    os.utime(dest / workspace.MARKER, (stamp, stamp))
    again = materialize(cache_dir=cache, source=source, version="1.0")
    assert again == dest
    assert (dest / "rxconfig.py").is_file()
    assert time.time() - (dest / workspace.MARKER).stat().st_mtime < DAY


def test_stale_temporary_directories_are_removed_fresh_ones_kept(tmp_path: Path) -> None:
    """REQ-UI-001, DD-016 (delta D12): ``.<name>.*.tmp`` leftovers older than 1 day are
    removed, a fresh one (a concurrent run's copy) is kept, and a dot-directory that does
    not follow the leftover naming is never touched."""
    cache = tmp_path / "cache"
    parent = cache / "app"
    parent.mkdir(parents=True)
    stale = _leftover(parent, ".0.0.1-0123456789abcdef.k3j_x9ab.tmp", 2 * DAY)
    fresh = _leftover(parent, ".0.0.1-0123456789abcdef.q1w2e3r4.tmp", 3600)
    other = _leftover(parent, ".cache.tmp", 30 * DAY)
    materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
    assert not stale.exists()
    assert fresh.is_dir()
    assert other.is_dir()


def test_a_removal_error_leaves_the_run_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-UI-001, DD-016 (delta D12): a workspace that cannot be removed (files in use on
    Windows, a read-only tree) is logged at DEBUG and skipped; the run gets its workspace
    and the old one keeps its marker (it is never half-deleted in place)."""
    cache = tmp_path / "cache"
    parent = cache / "app"
    parent.mkdir(parents=True)
    locked = _old_workspace(parent, "0.0.1-0123456789abcdef", 30 * DAY)
    real_replace = os.replace

    def refuse(src: Any, dst: Any) -> None:
        if Path(src) == locked:
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    monkeypatch.setattr(workspace.os, "replace", refuse)
    with caplog.at_level(logging.DEBUG, logger="reflex_mcphost.app.workspace"):
        dest = materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
    assert (dest / workspace.MARKER).is_file()
    assert (locked / workspace.MARKER).is_file()
    records = [r for r in caplog.records if r.name == "reflex_mcphost.app.workspace"]
    assert records
    assert {r.levelno for r in records} == {logging.DEBUG}


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX permission bits")
def test_a_read_only_tree_is_skipped_without_failing(tmp_path: Path) -> None:
    """REQ-UI-001, DD-016 (delta D12): an old workspace holding a read-only subtree cannot be
    fully deleted; pruning ignores the error, the run proceeds and no marker-less directory
    is left under the old workspace name (the rest is a leftover, retried later)."""
    cache = tmp_path / "cache"
    parent = cache / "app"
    parent.mkdir(parents=True)
    old = _old_workspace(parent, "0.0.1-0123456789abcdef", 30 * DAY)
    inner = old / ".web" / "locked"
    inner.mkdir()
    (inner / "file").write_text("x", encoding="utf-8")
    inner.chmod(0o500)
    try:
        dest = materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
        assert (dest / workspace.MARKER).is_file()
        assert not old.exists()
        leftovers = [p for p in parent.iterdir() if p.name.endswith(".tmp")]
        assert len(leftovers) == 1
        assert leftovers[0].name.startswith(f".{old.name}.")
    finally:
        for path in parent.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)


def test_pruning_never_follows_a_symlink(tmp_path: Path) -> None:
    """REQ-UI-001, DD-016 (delta D12): a symlink in the workspace parent — even to an old
    marked workspace elsewhere — is never followed or removed."""
    cache = tmp_path / "cache"
    parent = cache / "app"
    parent.mkdir(parents=True)
    elsewhere = _old_workspace(tmp_path / "elsewhere", "0.0.1-0123456789abcdef", 30 * DAY)
    link = parent / "0.0.1-0123456789abcdef"
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    materialize(cache_dir=cache, source=_source(tmp_path), version="1.0")
    assert link.is_symlink()
    assert (elsewhere / workspace.MARKER).is_file()
