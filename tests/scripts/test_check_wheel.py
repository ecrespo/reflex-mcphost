"""Pre-publish checks of the built distributions (Constitution Art. 1, Art. 7; DD-015, TD §9.1).

The wheel ships only the library, never server code; wheel and sdist carry the expected
version, declared extras, resolvable console scripts and no repository-only paths.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.check_wheel import check_sdist, check_wheel, main

VERSION = "0.1.0.dev0"
DIST_INFO = f"reflex_mcphost-{VERSION}.dist-info"
SDIST_ROOT = f"reflex_mcphost-{VERSION}"


def _metadata(
    version: str = VERSION,
    extras: tuple[str, ...] = ("pydantic-ai", "langgraph", "all"),
    content_type: str = "text/markdown",
) -> str:
    lines = ["Metadata-Version: 2.4", "Name: reflex-mcphost", f"Version: {version}"]
    lines += [f"Provides-Extra: {e}" for e in extras]
    lines.append(f"Description-Content-Type: {content_type}")
    return "\n".join(lines) + "\n\n# reflex-mcphost\n"


def _wheel_files(**overrides: str) -> dict[str, str]:
    files = {
        "reflex_mcphost/__init__.py": "import mcp\n",
        f"{DIST_INFO}/METADATA": _metadata(),
        f"{DIST_INFO}/WHEEL": "Wheel-Version: 1.0\n",
    }
    files.update(overrides)
    return files


def _wheel(tmp_path: Path, files: dict[str, str], version: str = VERSION) -> Path:
    path = tmp_path / f"reflex_mcphost-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def _sdist(tmp_path: Path, files: dict[str, str] | None = None, version: str = VERSION) -> Path:
    root = f"reflex_mcphost-{version}"
    members = {
        f"{root}/PKG-INFO": _metadata(version),
        f"{root}/pyproject.toml": "[project]\n",
        f"{root}/src/reflex_mcphost/__init__.py": "import mcp\n",
    }
    members.update(files or {})
    path = tmp_path / f"{root}.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, body in members.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


# ------------------------------------------------------------------ wheel


def test_clean_wheel_passes(tmp_path: Path) -> None:
    """Art. 1: a wheel with only library modules and complete metadata passes."""
    whl = _wheel(tmp_path, _wheel_files())
    assert check_wheel(whl, expect_version=VERSION) == []


def test_tests_and_spikes_are_rejected(tmp_path: Path) -> None:
    """Art. 7: tests/, spikes/ and other repository-only paths never ship."""
    whl = _wheel(
        tmp_path,
        _wheel_files(
            **{
                "tests/test_a.py": "",
                "spikes/S-1/a.py": "",
                "specs/a.md": "",
                "changes/a.md": "",
                ".claude/a.md": "",
            }
        ),
    )
    problems = check_wheel(whl)
    assert len(problems) == 5
    assert all("must not ship" in p for p in problems)


def test_mcp_server_import_is_rejected(tmp_path: Path) -> None:
    """Art. 1: no shipped module imports mcp.server, in either import form."""
    whl = _wheel(
        tmp_path,
        _wheel_files(
            **{
                "reflex_mcphost/a.py": "from mcp.server import Server\n",
                "reflex_mcphost/b.py": "import mcp.server.fastmcp\n",
                "reflex_mcphost/c.py": "from mcp import server\n",
            }
        ),
    )
    assert len(check_wheel(whl)) == 3


def test_wheel_version_mismatch_is_named(tmp_path: Path) -> None:
    """TD §9.1: the wheel file name and METADATA carry the expected (tag) version."""
    whl = _wheel(tmp_path, _wheel_files())
    problems = check_wheel(whl, expect_version="0.1.0a1")
    assert len(problems) == 2
    assert all("version" in p and "0.1.0a1" in p for p in problems)


def test_metadata_version_mismatch_alone_is_named(tmp_path: Path) -> None:
    """A METADATA Version that disagrees with the file name is reported on its own."""
    whl = _wheel(tmp_path, _wheel_files(**{f"{DIST_INFO}/METADATA": _metadata("9.9.9")}))
    problems = check_wheel(whl, expect_version=VERSION)
    assert problems == [f"{whl.name}: METADATA version 9.9.9 != expected {VERSION}"]


def test_console_script_target_missing_is_named(tmp_path: Path) -> None:
    """D4: a console_scripts entry point must resolve to a module shipped in the wheel."""
    entry_points = "[console_scripts]\nreflex-mcphost = reflex_mcphost.app.cli:main\n"
    whl = _wheel(tmp_path, _wheel_files(**{f"{DIST_INFO}/entry_points.txt": entry_points}))
    problems = check_wheel(whl)
    assert len(problems) == 1
    assert "reflex-mcphost" in problems[0]
    assert "reflex_mcphost.app.cli" in problems[0]


def test_console_script_attribute_missing_is_named(tmp_path: Path) -> None:
    """The entry point's attribute must be defined at the module's top level."""
    entry_points = "[console_scripts]\nreflex-mcphost = reflex_mcphost.cli:main\n"
    whl = _wheel(
        tmp_path,
        _wheel_files(
            **{
                f"{DIST_INFO}/entry_points.txt": entry_points,
                "reflex_mcphost/cli.py": "def other() -> None: ...\n",
            }
        ),
    )
    problems = check_wheel(whl)
    assert len(problems) == 1
    assert "main" in problems[0]


def test_resolvable_console_script_passes(tmp_path: Path) -> None:
    """A console script whose module and function ship in the wheel passes."""
    entry_points = "[console_scripts]\nreflex-mcphost = reflex_mcphost.cli:main\n"
    whl = _wheel(
        tmp_path,
        _wheel_files(
            **{
                f"{DIST_INFO}/entry_points.txt": entry_points,
                "reflex_mcphost/cli.py": "def main() -> int:\n    return 0\n",
            }
        ),
    )
    assert check_wheel(whl) == []


def test_missing_extra_is_named(tmp_path: Path) -> None:
    """D4: METADATA declares the extras pydantic-ai, langgraph and all."""
    meta = _metadata(extras=("pydantic-ai", "all"))
    whl = _wheel(tmp_path, _wheel_files(**{f"{DIST_INFO}/METADATA": meta}))
    problems = check_wheel(whl)
    assert len(problems) == 1
    assert "langgraph" in problems[0]


def test_non_markdown_long_description_is_named(tmp_path: Path) -> None:
    """PyPI renders the README as Markdown."""
    meta = _metadata(content_type="text/x-rst")
    whl = _wheel(tmp_path, _wheel_files(**{f"{DIST_INFO}/METADATA": meta}))
    problems = check_wheel(whl)
    assert len(problems) == 1
    assert "Description-Content-Type" in problems[0]


def test_wheel_without_metadata_is_named(tmp_path: Path) -> None:
    """A wheel with no dist-info METADATA is rejected rather than silently passing."""
    whl = _wheel(tmp_path, {"reflex_mcphost/__init__.py": ""})
    problems = check_wheel(whl)
    assert len(problems) == 1
    assert "METADATA" in problems[0]


# ------------------------------------------------------------------ sdist


def test_clean_sdist_passes(tmp_path: Path) -> None:
    """The sdist holds only the package sources and release metadata."""
    assert check_sdist(_sdist(tmp_path), expect_version=VERSION) == []


@pytest.mark.parametrize("prefix", ["tests", "spikes", "specs", "changes", ".claude"])
def test_sdist_forbidden_prefix_is_named(tmp_path: Path, prefix: str) -> None:
    """D4: tests/, spikes/, specs/, changes/ and .claude/ are not in the sdist."""
    sdist = _sdist(tmp_path, {f"{SDIST_ROOT}/{prefix}/a.py": ""})
    problems = check_sdist(sdist)
    assert problems == [f"{sdist.name}: {SDIST_ROOT}/{prefix}/a.py: must not ship"]


def test_sdist_version_mismatch_is_named(tmp_path: Path) -> None:
    """The sdist file name and PKG-INFO carry the expected version."""
    problems = check_sdist(_sdist(tmp_path), expect_version="0.1.0a1")
    assert len(problems) == 2
    assert all("0.1.0a1" in p for p in problems)


def test_sdist_mcp_server_import_is_rejected(tmp_path: Path) -> None:
    """Art. 1 holds for the sources in the sdist as well."""
    sdist = _sdist(tmp_path, {f"{SDIST_ROOT}/src/reflex_mcphost/a.py": "import mcp.server\n"})
    assert len(check_sdist(sdist)) == 1


# ------------------------------------------------------------------ CLI


def test_main_checks_wheel_and_sdist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A dist directory with a clean wheel and sdist passes with the expected version."""
    _wheel(tmp_path, _wheel_files())
    _sdist(tmp_path)
    assert main([str(tmp_path), "--expect-version", VERSION]) == 0
    assert "OK" in capsys.readouterr().out


def test_main_fails_on_version_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--expect-version reports every distribution with the wrong version."""
    _wheel(tmp_path, _wheel_files())
    _sdist(tmp_path)
    assert main([str(tmp_path), "--expect-version", "0.1.0a1"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert out.count("0.1.0a1") >= 4


def test_main_requires_an_sdist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A dist directory without an sdist is incomplete for a release."""
    _wheel(tmp_path, _wheel_files())
    assert main([str(tmp_path)]) == 1
    assert "no sdist" in capsys.readouterr().out


def test_main_requires_a_wheel(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty dist directory fails."""
    assert main([str(tmp_path)]) == 1
    assert "no wheel" in capsys.readouterr().out
