"""ServerRegistry load and merge (API §4.3, PRD RF-REG)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

from reflex_mcphost.core import registry as registry_module
from reflex_mcphost.core.config.models import (
    ConfigProblemCode,
    HostConfig,
    HttpTransport,
    SourceKind,
    StdioTransport,
)
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.registry import ServerRegistry
from tests.conftest import ThirdPartyFiles

pytestmark = pytest.mark.anyio

ENV = {"FS_TOKEN": "tok", "API_KEY": "key"}
PROJECT_FS = 'schema_version = 1\n[servers.fs]\ntransport = "stdio"\ncommand = "project-fs"\n'


async def make_registry(
    tp: ThirdPartyFiles,
    user_text: str | None = None,
    env: dict[str, str] | None = None,
) -> ServerRegistry:
    env = ENV if env is None else env
    if user_text is None:
        config = HostConfig(config_path=tp.user_file, cwd=tp.cwd)
    else:
        tp.user_file.parent.mkdir(parents=True, exist_ok=True)
        tp.user_file.write_text(user_text, encoding="utf-8")
        config = await HostConfig.aload(tp.user_file, cwd=tp.cwd, env=env)
    registry = ServerRegistry(config, platform=tp.platform, env=env)
    await registry.load()
    return registry


async def test_highest_precedence_wins_and_others_are_shadowed(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-003: same id in sources 1, 3 and 8 → definition from 1, two shadowed."""
    tp = third_party_files
    tp.install_at(tp.cwd / "mcphost.toml", text=PROJECT_FS)
    tp.install(SourceKind.MCP_JSON_PROJECT)
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp)

    fs = registry.get("fs")
    assert fs.origin.precedence == 1
    assert fs.origin.kind is SourceKind.MCPHOST_PROJECT
    assert isinstance(fs.transport, StdioTransport)
    assert fs.transport.command == "project-fs"  # whole definition, no field merge
    assert fs.transport.env == {}

    shadowed = [s for s in registry.shadowed() if s.definition.id == "fs"]
    assert [s.definition.origin.precedence for s in shadowed] == [3, 8]
    assert [s.definition.origin.path for s in shadowed] == [
        tp.path(SourceKind.MCP_JSON_PROJECT),
        tp.path(SourceKind.CLAUDE_DESKTOP),
    ]
    assert all(s.shadowed_by == fs.origin for s in shadowed)
    # `remote` is only in 3 and 8: 3 wins, 8 is shadowed
    assert registry.get("remote").origin.precedence == 3


async def test_every_definition_exposes_id_transport_and_origin(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-001, REQ-REG-008: all sources load; third-party files stay byte-identical."""
    tp = third_party_files
    tp.install_all()
    extra = tp.install_at(tp.home / "more.json", text='{"servers": {"x": {"command": "x"}}}')
    registry = await make_registry(
        tp,
        f'schema_version = 1\n[sources]\nextra = ["{extra.as_posix()}"]\n'
        '[servers.own]\ntransport = "http"\nurl = "https://own.example/mcp"\n',
    )
    by_id = {d.id: d for d in registry.definitions()}
    assert set(by_id) == {"fs", "remote", "own", "x"}
    for definition in by_id.values():
        assert definition.origin.path is not None
        assert isinstance(definition.transport, StdioTransport | HttpTransport)
    assert by_id["own"].origin.kind is SourceKind.MCPHOST_USER
    assert by_id["fs"].origin.kind is SourceKind.MCP_JSON_PROJECT
    assert by_id["x"].origin.kind is SourceKind.EXTRA
    assert by_id["x"].origin.precedence == 6
    # fs and remote are each defined by six third-party files: five shadowed per id
    assert len(registry.shadowed()) == 10
    assert registry.problems() == []
    tp.verify()


async def test_extra_file_with_mcp_servers_key(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-002: `extra` files may use either the `mcpServers` or the `servers` key."""
    tp = third_party_files
    extra = tp.install_at(tp.home / "extra.json", sample="claude_desktop_config.json")
    registry = await make_registry(
        tp, f'schema_version = 1\n[sources]\nextra = ["{extra.as_posix()}"]\n'
    )
    assert {d.id for d in registry.definitions()} == {"fs", "remote"}


async def test_malformed_file_is_skipped_and_others_load(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-006: a malformed file is reported with its path; the rest still loads."""
    tp = third_party_files
    bad = tp.install(SourceKind.MCP_JSON_PROJECT, sample="malformed.json")
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp)
    assert {d.id for d in registry.definitions()} == {"fs", "remote"}
    assert registry.get("fs").origin.kind is SourceKind.CLAUDE_DESKTOP
    (problem,) = registry.problems()
    assert problem.code is ConfigProblemCode.PARSE_ERROR
    assert str(bad) in problem.message


async def test_unreadable_and_undecodable_files_are_problems(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-006: unreadable or non-UTF-8 files are reported, never raised."""
    tp = third_party_files
    tp.path(SourceKind.CURSOR_PROJECT).mkdir(parents=True)  # a directory where a file goes
    vscode = tp.path(SourceKind.VSCODE_PROJECT)
    vscode.parent.mkdir(parents=True)
    vscode.write_bytes(b"\xff\xfe\x00garbage")
    registry = await make_registry(tp)
    assert registry.definitions() == []
    problems = registry.problems()
    assert [p.code for p in problems] == [ConfigProblemCode.PARSE_ERROR] * 2
    assert [p.origin.kind for p in problems] == [
        SourceKind.CURSOR_PROJECT,
        SourceKind.VSCODE_PROJECT,
    ]


async def test_disabled_sources_are_not_read(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-001: `[sources]` switches decide which third-party files are read."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp, "schema_version = 1\n[sources]\nclaude_desktop = false\n")
    assert registry.definitions() == []


async def test_overrides_apply_after_merge(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-010: `[overrides.<id>]` adjusts a third-party definition after the merge."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(
        tp,
        "schema_version = 1\n[overrides.fs]\nenabled = false\ntimeout_s = 7\n"
        'roots = ["file:///r"]\nmode = "legacy"\n[overrides.ghost]\nenabled = false\n',
    )
    fs = registry.get("fs")
    assert (fs.enabled, fs.timeout_s, fs.roots, fs.mode) == (False, 7, ("file:///r",), "legacy")
    assert fs.origin.kind is SourceKind.CLAUDE_DESKTOP
    remote = registry.get("remote")
    assert (remote.enabled, remote.timeout_s, remote.mode) == (True, None, "auto")
    # shadowed definitions are reported as read, without overrides
    assert all(s.definition.enabled for s in registry.shadowed())


async def test_variable_problems_keep_the_server_flagged(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-005: Servers with unresolved variables stay listed and are `config_error`."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp, env={"API_KEY": "key"})
    assert {d.id for d in registry.definitions()} == {"fs", "remote"}
    assert registry.config_errors() == frozenset({"fs"})
    (problem,) = registry.problems()
    assert problem.code is ConfigProblemCode.UNRESOLVED_VARIABLE
    assert problem.server_id == "fs"


async def test_shadowed_variable_problem_does_not_flag_the_winner(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-003, REQ-REG-005: only the effective definition's problems flag a Server."""
    tp = third_party_files
    tp.install_at(tp.cwd / "mcphost.toml", text=PROJECT_FS)
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp, env={"API_KEY": "key"})
    assert registry.config_errors() == frozenset()
    assert [p.server_id for p in registry.problems()] == ["fs"]  # still reported


async def test_user_file_problems_and_definitions_are_merged(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-001, REQ-REG-012: own definitions at precedence 2; their problems listed."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(
        tp,
        'schema_version = 1\n[servers.fs]\ntransport = "stdio"\ncommand = "mine"\n'
        '[servers."bad::id"]\ntransport = "stdio"\ncommand = "x"\n',
    )
    assert registry.get("fs").origin.kind is SourceKind.MCPHOST_USER
    assert [p.code for p in registry.problems()] == [ConfigProblemCode.INVALID_SERVER_ID]


async def test_get_unknown_server_raises(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-001: `get` of an id no source defines raises UnknownServerError."""
    registry = await make_registry(third_party_files)
    with pytest.raises(UnknownServerError):
        registry.get("nope")


async def test_files_are_read_in_a_worker_thread(
    third_party_files: ThirdPartyFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-REG-001: config files are read through `anyio.to_thread` (Art. 8)."""
    calls: list[Callable[..., Any]] = []
    real = anyio.to_thread.run_sync

    async def spy(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        calls.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(registry_module.anyio.to_thread, "run_sync", spy)
    third_party_files.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(third_party_files)
    assert calls
    assert {d.id for d in registry.definitions()} == {"fs", "remote"}


async def test_definitions_before_load_are_empty(tmp_path: Path) -> None:
    """REQ-REG-001: the constructor performs no I/O; state appears after `load()`."""
    registry = ServerRegistry(HostConfig(config_path=tmp_path / "x.toml", cwd=tmp_path))
    assert registry.definitions() == []
    assert registry.config.cwd == tmp_path
