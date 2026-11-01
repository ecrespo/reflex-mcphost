"""ServerRegistry mutations and reload (API §4.3, DD-013)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import anyio
import pytest
from pydantic import SecretStr

from reflex_mcphost.core.config.models import (
    ConfigProblemCode,
    HostConfig,
    HttpTransport,
    OAuthSettings,
    RegistryDiff,
    ServerDefinition,
    SourceKind,
    StdioTransport,
)
from reflex_mcphost.core.config.paths import Platform
from reflex_mcphost.core.errors import ConfigError, UnknownServerError
from reflex_mcphost.core.events import EventBus, RegistryChanged
from reflex_mcphost.core.registry import ServerRegistry
from tests.conftest import ThirdPartyFiles

pytestmark = pytest.mark.anyio

ENV = {"FS_TOKEN": "tok", "API_KEY": "key"}
USER = """\
# my settings — keep this comment
schema_version = 1

[host]
consent_timeout_s = 90  # inline comment

# the filesystem server
[servers.mine]
transport = "stdio"
command = "npx"  # pinned
args = ["-y", "srv"]
env = { TOKEN = "${FS_TOKEN}" }
"""
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


class Recorder:
    def __init__(self) -> None:
        self.diffs: list[RegistryDiff] = []

    async def __call__(self, diff: RegistryDiff) -> None:
        self.diffs.append(diff)


async def make_registry(
    tp: ThirdPartyFiles,
    user_text: str | None = USER,
    env: dict[str, str] | None = None,
    events: EventBus | None = None,
) -> ServerRegistry:
    env = dict(ENV) if env is None else env
    if user_text is None:
        config = HostConfig(config_path=tp.user_file, cwd=tp.cwd)
    else:
        tp.user_file.parent.mkdir(parents=True, exist_ok=True)
        tp.user_file.write_text(user_text, encoding="utf-8")
        config = await HostConfig.aload(tp.user_file, cwd=tp.cwd, env=env)
    registry = ServerRegistry(config, platform=tp.platform, env=env, events=events)
    await registry.load()
    return registry


def _stdio(server_id: str, command: str = "run", **env: str) -> ServerDefinition:
    return ServerDefinition(
        id=server_id,
        transport=StdioTransport(command=command, env={k: SecretStr(v) for k, v in env.items()}),
    )


def _text(tp: ThirdPartyFiles) -> str:
    return tp.user_file.read_text(encoding="utf-8")


# ------------------------------------------------------------------ add / update / remove


async def test_add_then_reload_round_trip(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: `add` persists to the user-level file and the Server appears."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    bus = EventBus()
    registry = await make_registry(tp, events=bus)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    definition = ServerDefinition(
        id="web",
        transport=HttpTransport(
            url="https://web.example/mcp",  # type: ignore[arg-type]  # pydantic coerces
            headers={"X-Key": SecretStr("hv")},
            oauth=OAuthSettings(client_id="cid", scopes=("read",)),
        ),
        timeout_s=12,
        roots=("file:///w",),
        log_level="debug",
        review_sampling_responses=True,
        mode="modern",
        enabled=False,
    )
    async with bus.subscribe() as events:
        await registry.add(definition)
        event = await events.__anext__()
    assert isinstance(event, RegistryChanged)
    assert event.diff == RegistryDiff(added=("web",), unchanged=("fs", "mine", "remote"))
    assert recorder.diffs == [event.diff]

    added = registry.get("web")
    assert added.origin.kind is SourceKind.MCPHOST_USER
    assert added.origin.path == tp.user_file
    assert added.model_copy(update={"origin": definition.origin}) == definition
    assert "# my settings — keep this comment" in _text(tp)

    config = await HostConfig.aload(tp.user_file, cwd=tp.cwd, env=ENV)  # re-read from disk
    fresh = ServerRegistry(config, platform=tp.platform, env=ENV)
    await fresh.load()
    assert fresh.get("web").model_copy(update={"origin": definition.origin}) == definition


async def test_add_creates_missing_user_file(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: the first `add` creates the user-level file with `schema_version`."""
    tp = third_party_files
    registry = await make_registry(tp, user_text=None)
    await registry.add(_stdio("new", K="v"))
    assert tp.user_file.exists()
    reloaded = HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV)
    assert set(reloaded.servers) == {"new"}
    assert registry.get("new").origin.precedence == 2


@POSIX
async def test_created_user_file_has_mode_0600(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: the user file is created with mode 0600 (it may hold secrets)."""
    tp = third_party_files
    registry = await make_registry(tp, user_text=None)
    await registry.add(_stdio("new"))
    assert stat.S_IMODE(tp.user_file.stat().st_mode) == 0o600


@POSIX
async def test_existing_file_mode_is_preserved(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: an atomic replace keeps the file's existing mode."""
    tp = third_party_files
    registry = await make_registry(tp)
    tp.user_file.chmod(0o640)
    await registry.add(_stdio("new"))
    assert stat.S_IMODE(tp.user_file.stat().st_mode) == 0o640


async def test_add_existing_or_invalid_raises(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009, REQ-REG-012: `add` rejects duplicates and invalid definitions."""
    tp = third_party_files
    registry = await make_registry(tp)
    before = _text(tp)
    with pytest.raises(ConfigError, match="already defined"):
        await registry.add(_stdio("mine"))
    bad_id = ServerDefinition.model_construct(
        id="bad::id", transport=StdioTransport(command="x"), origin=registry.get("mine").origin
    )
    with pytest.raises(ConfigError, match="invalid server id"):
        await registry.add(bad_id)
    sse = ServerDefinition(
        id="sse",
        transport=HttpTransport(url="https://x.example/sse", legacy_sse=True),  # type: ignore[arg-type]
    )
    with pytest.raises(ConfigError, match="legacy"):
        await registry.add(sse)
    assert _text(tp) == before


async def test_comment_survives_update_and_templates_are_kept(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-009, REQ-REG-007: edits keep user comments/order and `${VAR}` templates."""
    tp = third_party_files
    registry = await make_registry(tp)
    current = registry.get("mine")
    await registry.update(current.model_copy(update={"timeout_s": 5.0}))
    text = _text(tp)
    for kept in (
        "# my settings — keep this comment",
        "# inline comment",
        "# the filesystem server",
        "# pinned",
        'TOKEN = "${FS_TOKEN}"',
    ):
        assert kept in text
    assert '"tok"' not in text  # the expanded secret is never written back
    assert text.index("[host]") < text.index("[servers.mine]")
    assert registry.get("mine").timeout_s == 5.0


async def test_update_writes_new_values(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: `update` replaces the own definition in full."""
    tp = third_party_files
    registry = await make_registry(tp)
    await registry.update(_stdio("mine", command="uvx", TOKEN="literal", OTHER="o"))  # noqa: S106 - test value
    mine = registry.get("mine")
    assert isinstance(mine.transport, StdioTransport)
    assert mine.transport.command == "uvx"
    assert mine.transport.args == ()
    assert {k: v.get_secret_value() for k, v in mine.transport.env.items()} == {
        "TOKEN": "literal",
        "OTHER": "o",
    }
    assert "args" not in _text(tp)


async def test_remove_own_definition(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: `remove` deletes the own definition from the user file only."""
    tp = third_party_files
    registry = await make_registry(tp)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    await registry.remove("mine")
    assert "[servers.mine]" not in _text(tp)
    assert "# my settings — keep this comment" in _text(tp)
    assert recorder.diffs == [RegistryDiff(removed=("mine",))]
    with pytest.raises(UnknownServerError):
        registry.get("mine")


async def test_update_or_remove_third_party_raises(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-008, REQ-REG-009: third-party definitions are never edited."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp)
    before = _text(tp)
    with pytest.raises(ConfigError, match="third-party definition; use set_enabled or overrides"):
        await registry.update(_stdio("fs"))
    with pytest.raises(ConfigError, match="third-party definition"):
        await registry.remove("fs")
    with pytest.raises(UnknownServerError):
        await registry.remove("ghost")
    with pytest.raises(UnknownServerError):
        await registry.update(_stdio("ghost"))
    assert _text(tp) == before
    tp.verify()


# ------------------------------------------------------------------ set_enabled


async def test_disabling_claude_desktop_server_writes_override(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-010, REQ-REG-008: disabling a third-party Server writes `[overrides.<id>]`."""
    tp = third_party_files
    source = tp.install(SourceKind.CLAUDE_DESKTOP)
    before = source.read_bytes()
    registry = await make_registry(tp)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    await registry.set_enabled("fs", False)
    assert registry.get("fs").enabled is False
    assert HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV).overrides["fs"].enabled is False
    assert source.read_bytes() == before
    assert recorder.diffs[-1].changed == ("fs",)
    await registry.set_enabled("fs", True)
    assert registry.get("fs").enabled is True
    tp.verify()


async def test_set_enabled_on_own_server_edits_its_table(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-009: an own Server is enabled/disabled in its own table."""
    tp = third_party_files
    registry = await make_registry(tp)
    await registry.set_enabled("mine", False)
    config = HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV)
    assert config.servers["mine"].enabled is False
    assert config.overrides == {}
    await registry.set_enabled("mine", True)
    assert registry.get("mine").enabled is True
    with pytest.raises(UnknownServerError):
        await registry.set_enabled("ghost", False)


async def test_set_enabled_on_shadowed_own_server_uses_override(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-010: when a project file shadows an own Server, the override applies."""
    tp = third_party_files
    tp.install_at(
        tp.cwd / "mcphost.toml",
        text='schema_version = 1\n[servers.mine]\ntransport = "stdio"\ncommand = "p"\n',
    )
    registry = await make_registry(tp)
    await registry.set_enabled("mine", False)
    assert registry.get("mine").enabled is False
    assert registry.get("mine").origin.kind is SourceKind.MCPHOST_PROJECT
    tp.verify()


# ------------------------------------------------------------------ atomic writes


async def test_crash_between_write_and_replace_keeps_old_file(
    third_party_files: ThirdPartyFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-REG-009: an interrupted write leaves the old file intact and no temp file."""
    tp = third_party_files
    registry = await make_registry(tp)
    before = tp.user_file.read_bytes()

    def crash(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        await registry.add(_stdio("new"))
    assert tp.user_file.read_bytes() == before
    assert sorted(p.name for p in tp.user_file.parent.iterdir()) == ["mcphost.toml"]
    with pytest.raises(UnknownServerError):
        registry.get("new")


async def test_invalid_resulting_file_is_not_written(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: a user file that is already invalid is never overwritten."""
    tp = third_party_files
    registry = await make_registry(tp)
    tp.user_file.write_text("schema_version = 1\ntypo = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        await registry.add(_stdio("new"))
    assert _text(tp) == "schema_version = 1\ntypo = 1\n"


# ------------------------------------------------------------------ reload


async def test_reload_detects_env_value_change(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-013: changing only an env variable's value marks the Server `changed`."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    env = dict(ENV)
    registry = await make_registry(tp, env=env)
    assert await registry.reload() == RegistryDiff(unchanged=("fs", "mine", "remote"))
    env["FS_TOKEN"] = "rotated"  # noqa: S105 - test value
    diff = await registry.reload()
    assert diff.changed == ("fs", "mine")
    assert diff.unchanged == ("remote",)
    tp.verify()


async def test_reload_detects_added_removed_and_config_error(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-013, REQ-REG-005: reload reports added/removed and `config_error` changes."""
    tp = third_party_files
    env = dict(ENV)
    registry = await make_registry(tp, env=env)
    tp.install(SourceKind.CURSOR_USER)
    tp.user_file.write_text("schema_version = 1\n", encoding="utf-8")
    diff = await registry.reload()
    assert (diff.added, diff.removed) == (("fs", "remote"), ("mine",))
    del env["FS_TOKEN"]
    diff = await registry.reload()
    assert diff.changed == ("fs",)
    assert registry.config_errors() == frozenset({"fs"})
    assert [p.code for p in registry.problems()] == [ConfigProblemCode.UNRESOLVED_VARIABLE]


async def test_reload_with_invalid_user_file_keeps_state(
    third_party_files: ThirdPartyFiles,
) -> None:
    """REQ-REG-013: a broken user file aborts the reload; the registry is unchanged."""
    tp = third_party_files
    registry = await make_registry(tp)
    tp.user_file.write_text("schema_version = 9\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        await registry.reload()
    assert registry.get("mine").id == "mine"


async def test_reload_tolerates_deleted_user_file(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-013: a user file deleted after start means no own Servers."""
    tp = third_party_files
    registry = await make_registry(tp)
    tp.user_file.unlink()
    assert (await registry.reload()).removed == ("mine",)


async def test_concurrent_mutations_are_serialized(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-009: concurrent mutations never lose each other's writes."""
    tp = third_party_files
    registry = await make_registry(tp)
    async with anyio.create_task_group() as tg:
        for i in range(5):
            tg.start_soon(registry.add, _stdio(f"s{i}"))
    config = HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV)
    assert set(config.servers) == {"mine", "s0", "s1", "s2", "s3", "s4"}


async def test_user_file_path_with_unicode_round_trips(tmp_path: Path) -> None:
    """REQ-REG-009: files are written as UTF-8 regardless of the platform encoding."""
    path = tmp_path / "cfg" / "mcphost.toml"
    registry = ServerRegistry(
        HostConfig(config_path=path, cwd=tmp_path),
        platform=Platform(system="linux", home=tmp_path, env={}),
        env={},
    )
    await registry.load()
    await registry.add(_stdio("u", command="ñandú"))
    assert "ñandú" in path.read_text(encoding="utf-8")
