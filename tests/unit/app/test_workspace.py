"""Workspace materialization of the packaged reference app (F6-08; DD-016, spike S-5).

The packaged Reflex sources are copied to ``<cache>/app/<version>-<content-hash>/``
through a temporary sibling renamed into place; the package directory is never written.
"""

from __future__ import annotations

import threading
from pathlib import Path

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
