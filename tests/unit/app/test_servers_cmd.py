"""``reflex-mcphost servers`` (F6-08; REQ-UI-008, API §6).

A temporary user config plus a project ``.mcp.json`` in the working directory give an own
definition, a shadowed one and a problem; user-level third-party sources are disabled so
no real home directory is read.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from reflex_mcphost.app.cli import Runtime, main

SENTINEL = "sentinel-header-value-5d1b"  # not a real secret (gitleaks-safe)


def _runtime(out: io.StringIO, err: io.StringIO) -> Runtime:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("servers must not spawn, probe, open or materialize")

    return Runtime(
        spawn=forbidden,
        probe=forbidden,
        open_browser=forbidden,
        materialize=forbidden,
        stdout=out,
        stderr=err,
        install_signals=False,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """cwd with a project .mcp.json; returns the user config path."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "files": {"command": "shadowed-files"},
                    "web": {"url": "https://mcp.example.test/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SENTINEL_TOKEN", SENTINEL)
    monkeypatch.delenv("MISSING_VAR_FOR_TEST", raising=False)
    config = tmp_path / "mcphost.toml"
    config.write_text(
        textwrap.dedent(
            f"""\
            schema_version = 1
            [host]
            data_dir = "{(tmp_path / "data").as_posix()}"
            [sources]
            claude_desktop = false
            cursor = false
            vscode = false
            [servers.files]
            transport = "stdio"
            command = "own-files"
            env = {{ TOKEN = "${{SENTINEL_TOKEN}}" }}
            [servers.remote]
            transport = "http"
            url = "https://api.example.test/mcp"
            headers = {{ Authorization = "Bearer ${{SENTINEL_TOKEN}}" }}
            enabled = false
            [servers.broken]
            transport = "stdio"
            command = "${{MISSING_VAR_FOR_TEST}}"
            """
        ),
        encoding="utf-8",
    )
    return config


def _run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), runtime=_runtime(out, err))
    return code, out.getvalue(), err.getvalue()


def test_table_columns_and_rows(project: Path) -> None:
    """REQ-UI-008: ID TRANSPORT ORIGIN ENABLED STATUS, effective and shadowed rows."""
    code, out, _ = _run("servers", "--config", str(project))
    assert code == 0
    lines = out.splitlines()
    assert lines[0].split() == ["ID", "TRANSPORT", "ORIGIN", "ENABLED", "STATUS"]
    rows = {tuple(line.split()[:5]) for line in lines[1:] if line and not line.startswith(" ")}
    assert ("files", "stdio", "mcphost_user", "yes", "ok") in rows
    assert ("remote", "http", "mcphost_user", "no", "ok") in rows
    assert ("broken", "stdio", "mcphost_user", "yes", "config_error") in rows
    assert ("web", "http", "mcp_json_project", "yes", "ok") in rows
    assert ("files", "stdio", "mcp_json_project", "yes", "shadowed") in rows
    assert "MISSING_VAR_FOR_TEST" in out  # the problem is listed below the table
    assert SENTINEL not in out


def test_json_shape_with_secrets_masked(project: Path) -> None:
    """REQ-UI-008, REQ-REG-007: {"servers", "shadowed", "problems"} from the §4.3 models,
    env/header values as "**********"."""
    code, out, _ = _run("servers", "--json", "--config", str(project))
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"servers", "shadowed", "problems"}
    by_id = {s["id"]: s for s in data["servers"]}
    assert set(by_id) == {"files", "remote", "broken", "web"}
    assert by_id["files"]["transport"]["env"] == {"TOKEN": "**********"}
    assert by_id["remote"]["transport"]["headers"] == {"Authorization": "**********"}
    assert by_id["files"]["origin"]["kind"] == "mcphost_user"
    (shadow,) = data["shadowed"]
    assert shadow["definition"]["id"] == "files"
    assert shadow["definition"]["origin"]["kind"] == "mcp_json_project"
    assert shadow["shadowed_by"]["kind"] == "mcphost_user"
    assert any(p["code"] == "unresolved_variable" for p in data["problems"])
    assert SENTINEL not in out


def test_strict_with_a_problem_is_exit_1(project: Path) -> None:
    """API §6: --strict exits 1 when at least one ConfigProblem exists."""
    code, out, _ = _run("servers", "--strict", "--json", "--config", str(project))
    assert code == 1
    assert json.loads(out)["problems"]


def test_strict_without_problems_is_exit_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """API §6: --strict with a clean config exits 0."""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "user.toml"  # a ./mcphost.toml would also be read as the project file
    config.write_text(
        "schema_version = 1\n[sources]\nclaude_desktop = false\ncursor = false\n"
        "vscode = false\nproject_mcp_json = false\n",
        encoding="utf-8",
    )
    code, out, _ = _run("servers", "--strict", "--config", str(config))
    assert code == 0
    assert out.splitlines()[0].split() == ["ID", "TRANSPORT", "ORIGIN", "ENABLED", "STATUS"]


def test_invalid_explicit_config_is_exit_3(tmp_path: Path) -> None:
    """API §6: an invalid own config is a fatal error (3), also for servers."""
    bad = tmp_path / "bad.toml"
    bad.write_text("schema_version = [", encoding="utf-8")
    code, _, err = _run("servers", "--config", str(bad))
    assert code == 3
    assert "bad.toml" in err


def test_third_party_files_are_byte_identical(project: Path) -> None:
    """Art. 6: listing never writes a third-party file (nor the own one)."""
    mcp_json = Path.cwd() / ".mcp.json"
    before = (mcp_json.read_bytes(), project.read_bytes())
    _run("servers", "--json", "--config", str(project))
    _run("servers", "--config", str(project))
    assert (mcp_json.read_bytes(), project.read_bytes()) == before
    assert not (project.parent / "data").exists()  # no DB opened, no Host started


def test_servers_imports_no_reflex_and_starts_nothing(project: Path, tmp_path: Path) -> None:
    """REQ-UI-008: `servers` and `--version` import no reflex and spawn no process."""
    script = textwrap.dedent(
        f"""
        import io, subprocess, sys
        class NoSpawn(subprocess.Popen):
            def __init__(self, *a, **k):
                raise AssertionError("spawned a process")
        subprocess.Popen = NoSpawn
        from reflex_mcphost.app.cli import main
        assert main(["--version"]) == 0
        code = main(["servers", "--json", "--config", {str(project)!r}])
        assert code == 0, code
        loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("reflex", "reflex_base"))
        assert not loaded, loaded
        print("NO-REFLEX-OK")
        """
    )
    env = {**os.environ, "SENTINEL_TOKEN": SENTINEL}
    result = subprocess.run(  # noqa: S603 - our interpreter, our script
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "NO-REFLEX-OK" in result.stdout
