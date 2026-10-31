"""Config parsers: own mcphost.toml and the read-only third-party formats (API §4.2, §5)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr

from reflex_mcphost.core.config import claude, cursor, mcp_json, own, vscode
from reflex_mcphost.core.config.models import (
    ConfigOrigin,
    ConfigProblem,
    ConfigProblemCode,
    HostConfig,
    HttpTransport,
    ParseOutcome,
    ServerDefinition,
    SourceKind,
    StdioTransport,
)
from reflex_mcphost.core.errors import ConfigError
from reflex_mcphost.core.secrets import MASK, redact

FIXTURES = Path(__file__).parents[3] / "fixtures" / "configs"
FS_TOKEN = "sekret-fs-7f3a9c"  # noqa: S105 - seeded secret, asserted absent from output
API_KEY = "sekret-api-2b8e1d"
ENV = {"FS_TOKEN": FS_TOKEN, "API_KEY": API_KEY}
CWD = Path("/work/proj")
SECRETS = (FS_TOKEN, API_KEY)

Parser = Callable[..., ParseOutcome]


def _origin(kind: SourceKind = SourceKind.CLAUDE_DESKTOP, precedence: int = 8) -> ConfigOrigin:
    return ConfigOrigin(kind=kind, path=Path("/cfg/source.json"), precedence=precedence)


ORIGIN = _origin()


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _expected() -> list[ServerDefinition]:
    return [
        ServerDefinition(
            id="fs",
            transport=StdioTransport(
                command="npx", args=("-y", "srv"), env={"TOKEN": SecretStr(FS_TOKEN)}
            ),
            origin=ORIGIN,
        ),
        ServerDefinition(
            id="remote",
            transport=HttpTransport(
                url="https://example.com/mcp",  # type: ignore[arg-type]  # pydantic coerces
                headers={"Authorization": SecretStr(f"Bearer {API_KEY}")},
            ),
            origin=ORIGIN,
        ),
    ]


def _normalize(
    defs: list[ServerDefinition] | tuple[ServerDefinition, ...],
) -> list[ServerDefinition]:
    return sorted((d.model_copy(update={"origin": ORIGIN}) for d in defs), key=lambda d: d.id)


def _codes(problems: tuple[ConfigProblem, ...] | list[ConfigProblem]) -> list[ConfigProblemCode]:
    return [p.code for p in problems]


def _assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


def _user(tmp_path: Path, text: str, env: Mapping[str, str] = ENV) -> HostConfig:
    path = tmp_path / "mcphost.toml"
    path.write_text(text, encoding="utf-8")
    return HostConfig.load(path, cwd=CWD, env=env)


THIRD_PARTY: list[tuple[Parser, str]] = [
    (claude.parse, "claude_desktop_config.json"),
    (cursor.parse, "cursor_mcp.json"),
    (mcp_json.parse, "dot_mcp.json"),
    (vscode.parse, "vscode_mcp.json"),
]


# ------------------------------------------------------------------ same server, 5 formats


@pytest.mark.parametrize(("parse", "name"), THIRD_PARTY)
def test_third_party_formats_parse_to_the_same_definitions(parse: Parser, name: str) -> None:
    """REQ-REG-002: `mcpServers` and `servers` files parse into the same ServerDefinition."""
    outcome = parse(_text(name), ORIGIN, env=ENV, cwd=CWD)
    assert outcome.problems == ()
    assert _normalize(outcome.definitions) == _expected()
    assert all(d.origin == ORIGIN for d in outcome.definitions)


def test_own_user_file_parses_to_the_same_definitions(tmp_path: Path) -> None:
    """REQ-REG-002: the own mcphost.toml format yields the same ServerDefinition model."""
    config = _user(tmp_path, _text("mcphost.toml"))
    assert config.problems == ()
    assert _normalize(list(config.servers.values())) == _expected()
    assert config.consent_timeout_s == 90
    origin = config.servers["fs"].origin
    assert origin.kind is SourceKind.MCPHOST_USER
    assert origin.precedence == 2
    assert origin.path == tmp_path / "mcphost.toml"


def test_own_project_file_parses_to_the_same_definitions() -> None:
    """REQ-REG-002, REQ-REG-014: the project file contributes its `[servers.*]`."""
    origin = _origin(SourceKind.MCPHOST_PROJECT, 1)
    outcome = own.parse_project(_text("mcphost.toml"), origin, env=ENV, cwd=CWD)
    assert _normalize(outcome.definitions) == _expected()
    assert _codes(outcome.problems) == [ConfigProblemCode.PROJECT_SETTING_IGNORED]


# ------------------------------------------------------------------ third-party mapping


def _one(parse: Parser, entry: str, env: Mapping[str, str] = ENV) -> ParseOutcome:
    return parse(f'{{"mcpServers": {{"s": {entry}}}}}', ORIGIN, env=env, cwd=CWD)


def test_type_sse_maps_to_legacy_http_transport() -> None:
    """REQ-REG-002: `type: "sse"` → HttpTransport with the legacy flag (API §5.3)."""
    outcome = vscode.parse(
        '{"servers": {"s": {"type": "sse", "url": "https://x.example/sse"}}}',
        ORIGIN,
        env=ENV,
        cwd=CWD,
    )
    (definition,) = outcome.definitions
    assert isinstance(definition.transport, HttpTransport)
    assert definition.transport.legacy_sse is True


@pytest.mark.parametrize("kind", ["http", "streamable-http"])
def test_explicit_http_types(kind: str) -> None:
    """REQ-REG-002: `type` is honoured in `mcpServers` files."""
    outcome = _one(cursor.parse, json.dumps({"type": kind, "url": "https://x.example/mcp"}))
    (definition,) = outcome.definitions
    assert isinstance(definition.transport, HttpTransport)
    assert definition.transport.legacy_sse is False


@pytest.mark.parametrize(
    "entry",
    [
        '{"command": "a", "url": "https://x.example/mcp"}',
        "{}",
        '{"type": "websocket", "url": "wss://x.example"}',
        '{"type": "stdio", "url": "https://x.example/mcp"}',
        '{"type": "http", "command": "a"}',
        '{"command": "a", "args": "not-a-list"}',
        '{"url": "not a url"}',
        '"just a string"',
    ],
)
def test_invalid_entries_are_reported_and_skipped(entry: str) -> None:
    """REQ-REG-006: an invalid entry → `invalid_definition`, never an exception."""
    outcome = _one(claude.parse, entry)
    assert outcome.definitions == ()
    assert _codes(outcome.problems) == [ConfigProblemCode.INVALID_DEFINITION]
    assert outcome.problems[0].server_id == "s"
    assert outcome.problems[0].origin == ORIGIN


def test_invalid_entry_message_does_not_echo_values() -> None:
    """REQ-REG-007: validation messages are built from field locations, not input values."""
    outcome = _one(
        claude.parse, json.dumps({"command": "a", "env": {"K": 12}, "args": [{"x": FS_TOKEN}]})
    )
    (problem,) = outcome.problems
    assert "args" in problem.message
    _assert_no_secrets(problem.message, repr(problem))


def test_unknown_fields_are_ignored_and_disabled_maps_to_enabled() -> None:
    """REQ-REG-002: unknown third-party fields are ignored; `disabled: true` → disabled."""
    outcome = _one(claude.parse, '{"command": "a", "disabled": true, "autoApprove": ["x"]}')
    (definition,) = outcome.definitions
    assert definition.enabled is False


def test_missing_top_level_key_yields_nothing() -> None:
    """REQ-REG-001: a file without its top-level key contributes no Servers."""
    outcome = claude.parse('{"globalShortcut": "x"}', ORIGIN, env=ENV, cwd=CWD)
    assert outcome == ParseOutcome()


@pytest.mark.parametrize("text", ['["a"]', '{"mcpServers": []}'])
def test_wrong_top_level_shape_is_a_parse_error(text: str) -> None:
    """REQ-REG-006: a file of the wrong shape is reported, not raised."""
    outcome = claude.parse(text, ORIGIN, env=ENV, cwd=CWD)
    assert _codes(outcome.problems) == [ConfigProblemCode.PARSE_ERROR]


def test_workspace_folder_expands_to_cwd_in_vscode_files() -> None:
    """REQ-REG-004: VS Code `${workspaceFolder}` expands to the project directory."""
    outcome = vscode.parse(
        '{"servers": {"s": {"command": "a", "args": ["${workspaceFolder}/x"]}}}',
        ORIGIN,
        env=ENV,
        cwd=CWD,
    )
    (definition,) = outcome.definitions
    assert isinstance(definition.transport, StdioTransport)
    assert definition.transport.args == (f"{CWD}/x",)


# ------------------------------------------------------------------ malformed files


@pytest.mark.parametrize("parse", [p for p, _ in THIRD_PARTY])
def test_malformed_json_is_one_parse_error(parse: Parser) -> None:
    """REQ-REG-006: a malformed file → one `parse_error` naming the path and error."""
    outcome = parse(_text("malformed.json"), ORIGIN, env=ENV, cwd=CWD)
    assert outcome.definitions == ()
    (problem,) = outcome.problems
    assert problem.code is ConfigProblemCode.PARSE_ERROR
    assert problem.server_id is None
    assert str(ORIGIN.path) in problem.message
    assert "line 1" in problem.message


def test_malformed_project_toml_is_one_parse_error() -> None:
    """REQ-REG-006: a malformed project mcphost.toml is reported and skipped."""
    origin = _origin(SourceKind.MCPHOST_PROJECT, 1)
    outcome = own.parse_project(_text("malformed.toml"), origin, env=ENV, cwd=CWD)
    assert outcome.definitions == ()
    assert _codes(outcome.problems) == [ConfigProblemCode.PARSE_ERROR]
    assert str(origin.path) in outcome.problems[0].message


@pytest.mark.parametrize("text", ["[servers.a]\ncommand = 'x'\n", "schema_version = 7\n"])
def test_project_file_with_bad_schema_version_is_a_parse_error(text: str) -> None:
    """REQ-REG-006: a project file without a supported `schema_version` is skipped."""
    outcome = own.parse_project(text, _origin(SourceKind.MCPHOST_PROJECT, 1), env=ENV, cwd=CWD)
    assert outcome.definitions == ()
    assert _codes(outcome.problems) == [ConfigProblemCode.PARSE_ERROR]


# ------------------------------------------------------------------ project-level filter


def test_project_settings_are_ignored_one_problem_per_key() -> None:
    """REQ-REG-014: project `[host]`/`[sources]` → one `project_setting_ignored` per key."""
    text = (
        "schema_version = 1\n[host]\nconsent_timeout_s = 1\n[sources]\ncursor = false\n"
        "[overrides.x]\nenabled = false\n"
        '[servers.a]\ntransport = "stdio"\ncommand = "a"\n'
    )
    outcome = own.parse_project(text, _origin(SourceKind.MCPHOST_PROJECT, 1), env=ENV, cwd=CWD)
    assert [d.id for d in outcome.definitions] == ["a"]
    ignored = [p for p in outcome.problems if p.code is ConfigProblemCode.PROJECT_SETTING_IGNORED]
    assert len(ignored) == 3
    for key, problem in zip(["host", "sources", "overrides"], ignored, strict=True):
        assert key in problem.message


def test_invalid_project_server_is_reported_not_raised() -> None:
    """REQ-REG-006: an invalid project `[servers.*]` table → `invalid_definition`."""
    text = (
        'schema_version = 1\n[servers.a]\ntransport = "stdio"\nurl = "https://x.example"\n'
        '[servers.b]\ntransport = "stdio"\ncommand = "b"\ntypo = 1\n'
        '[servers.c]\ntransport = "stdio"\ncommand = "c"\n'
    )
    outcome = own.parse_project(text, _origin(SourceKind.MCPHOST_PROJECT, 1), env=ENV, cwd=CWD)
    assert [d.id for d in outcome.definitions] == ["c"]
    assert _codes(outcome.problems) == [ConfigProblemCode.INVALID_DEFINITION] * 2
    assert [p.server_id for p in outcome.problems] == ["a", "b"]


# ------------------------------------------------------------------ server ids


@pytest.mark.parametrize("bad", ["a::b", "has space", "x" * 65, "ünï"])
def test_invalid_server_id_is_reported_and_others_load(bad: str) -> None:
    """REQ-REG-012: an invalid `server_id` is rejected with a problem naming the Server."""
    text = json.dumps({"mcpServers": {bad: {"command": "a"}, "ok": {"command": "b"}}})
    outcome = claude.parse(text, ORIGIN, env=ENV, cwd=CWD)
    assert [d.id for d in outcome.definitions] == ["ok"]
    (problem,) = outcome.problems
    assert problem.code is ConfigProblemCode.INVALID_SERVER_ID
    assert bad[:20] in problem.message


def test_invalid_server_id_in_user_file_is_a_problem(tmp_path: Path) -> None:
    """REQ-REG-012: the user file reports invalid ids as problems, too."""
    config = _user(
        tmp_path,
        'schema_version = 1\n[servers."a::b"]\ntransport = "stdio"\ncommand = "a"\n'
        '[overrides."bad id"]\nenabled = false\n',
    )
    assert config.servers == {}
    assert config.overrides == {}
    assert _codes(config.problems) == [ConfigProblemCode.INVALID_SERVER_ID] * 2


# ------------------------------------------------------------------ variables


def test_unresolved_variable_keeps_server_with_problem() -> None:
    """REQ-REG-005: an unset variable keeps the Server and names the variable and field."""
    outcome = _one(claude.parse, '{"command": "a", "env": {"TOKEN": "${NOPE}"}}')
    (definition,) = outcome.definitions
    assert isinstance(definition.transport, StdioTransport)
    assert definition.transport.env["TOKEN"].get_secret_value() == "${NOPE}"
    (problem,) = outcome.problems
    assert problem.code is ConfigProblemCode.UNRESOLVED_VARIABLE
    assert problem.server_id == "s"
    assert "NOPE" in problem.message
    assert "env.TOKEN" in problem.message


def test_unsupported_input_variable_in_vscode() -> None:
    """REQ-REG-005: VS Code `${input:…}` → `unsupported_variable`."""
    outcome = vscode.parse(
        '{"servers": {"s": {"type": "http", "url": "https://x.example/mcp",'
        ' "headers": {"Authorization": "Bearer ${input:key}"}}}}',
        ORIGIN,
        env=ENV,
        cwd=CWD,
    )
    assert [d.id for d in outcome.definitions] == ["s"]
    (problem,) = outcome.problems
    assert problem.code is ConfigProblemCode.UNSUPPORTED_VARIABLE
    assert "input:key" in problem.message
    assert "headers.Authorization" in problem.message


def test_unresolved_variable_in_url_keeps_server() -> None:
    """REQ-REG-005: a URL that cannot be expanded is kept raw, flagged by its problem."""
    outcome = _one(claude.parse, '{"url": "https://${HOST}/mcp"}')
    (definition,) = outcome.definitions
    assert definition.id == "s"
    assert _codes(outcome.problems) == [ConfigProblemCode.UNRESOLVED_VARIABLE]
    assert "url" in outcome.problems[0].message


def test_variables_in_every_expandable_field() -> None:
    """REQ-REG-004: `${VAR}`/`${VAR:-default}` expand in command, args, env and cwd."""
    env = {"BIN": "node", "DIR": "/srv"}
    outcome = _one(
        claude.parse,
        '{"command": "${BIN}", "args": ["${DIR}/a", "${MISSING:-dflt}"], "cwd": "${DIR}",'
        ' "env": {"A": "${DIR}"}}',
        env=env,
    )
    (definition,) = outcome.definitions
    transport = definition.transport
    assert isinstance(transport, StdioTransport)
    assert transport.command == "node"
    assert transport.args == ("/srv/a", "dflt")
    assert transport.cwd == Path("/srv")
    assert transport.env["A"].get_secret_value() == "/srv"


def test_expanded_values_are_registered_for_redaction() -> None:
    """REQ-REG-007: expanded env/header values are registered as secrets."""
    token = "sekret-registered-91d2"  # noqa: S105
    _one(claude.parse, '{"command": "a", "env": {"T": "${T}"}}', env={"T": token})
    assert redact(f"value={token}") == f"value={MASK}"


# ------------------------------------------------------------------ secrets never rendered


def test_no_model_or_problem_renders_secret_values(tmp_path: Path) -> None:
    """REQ-REG-007: str()/repr() of models and every problem message omit env/header values."""
    outputs: list[str] = []
    for parse, name in THIRD_PARTY:
        outcome = parse(_text(name), ORIGIN, env=ENV, cwd=CWD)
        outputs += [str(outcome), repr(outcome)]
        outputs += [str(d) for d in outcome.definitions] + [repr(d) for d in outcome.definitions]
    config = _user(tmp_path, _text("mcphost.toml"))
    outputs += [str(config), repr(config)]
    bad = _one(
        claude.parse,
        json.dumps({"command": "a", "env": {"X": "${NOPE}", "Y": FS_TOKEN}, "args": [1, API_KEY]}),
    )
    unresolved = _one(
        claude.parse, json.dumps({"command": "a", "env": {"X": "${NOPE}", "Y": FS_TOKEN}})
    )
    outputs += [p.message for p in (*bad.problems, *unresolved.problems)]
    outputs += [repr(p) for p in (*bad.problems, *unresolved.problems)]
    _assert_no_secrets(*outputs)


# ------------------------------------------------------------------ user file strictness


@pytest.mark.parametrize(
    "text",
    [
        "schema_version = 1\ntypo = 1\n",
        "schema_version = 1\n[host]\nconsent_timeout = 5\n",
        "schema_version = 1\n[sources]\nclaude = false\n",
        'schema_version = 1\n[servers.a]\ntransport = "stdio"\ncommand = "a"\ntypo = 1\n',
        'schema_version = 1\n[servers.a]\ntransport = "stdio"\nurl = "https://x.example"\n',
        'schema_version = 1\n[servers.a]\ntransport = "http"\ncommand = "a"\n',
        "schema_version = 1\n[overrides.a]\nenabled = 'maybe'\n",
        "schema_version = 2\n",
        "[host]\n",
        "schema_version = 1\n[servers\n",
    ],
)
def test_invalid_user_file_raises_config_error(tmp_path: Path, text: str) -> None:
    """REQ-REG-006: the own user-level file is strict — security settings never silently drop."""
    with pytest.raises(ConfigError):
        _user(tmp_path, text)


def test_user_config_error_never_echoes_values(tmp_path: Path) -> None:
    """REQ-REG-007: ConfigError messages name fields, never input values."""
    text = f'schema_version = 1\n[host]\nconsent_timeout_s = "{FS_TOKEN}"\n'
    with pytest.raises(ConfigError) as info:
        _user(tmp_path, text)
    assert "consent_timeout_s" in str(info.value)
    _assert_no_secrets(str(info.value), repr(info.value))


def test_missing_explicit_user_file_raises(tmp_path: Path) -> None:
    """REQ-REG-006: an explicitly given file that does not exist is a ConfigError."""
    with pytest.raises(ConfigError):
        HostConfig.load(tmp_path / "nope.toml", cwd=CWD, env=ENV)


def test_missing_default_user_file_is_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-REG-001: no user-level file → default settings and no own Servers."""
    default = tmp_path / "cfg" / "mcphost.toml"
    monkeypatch.setattr(own, "default_user_file", lambda: default)
    config = HostConfig.load(cwd=CWD, env=ENV)
    assert config.servers == {}
    assert config.config_path == default
    assert config.cwd == CWD
    assert config.consent_timeout_s == 120.0


def test_full_user_file_settings(tmp_path: Path) -> None:
    """REQ-REG-010: every documented table parses (host, sources, sampling, overrides)."""
    text = (
        "schema_version = 1\n"
        '[host]\ndata_dir = "~/data"\ndefault_tool_timeout_s = 5\nlegacy_sse_fallback = true\n'
        '[sources]\nclaude_desktop = false\nextra = ["x.json"]\n'
        '[sampling]\nprovider = "pkg.mod:factory"\n'
        '[servers.linear]\ntransport = "http"\nmode = "modern"\nurl = "https://mcp.example/mcp"\n'
        "review_sampling_responses = true\nenabled = false\ntimeout_s = 3\n"
        'roots = ["file:///r"]\nlog_level = "info"\n'
        '[servers.linear.oauth]\nscopes = ["read"]\nclient_id = "cid"\nclient_secret = "cs"\n'
        '[overrides.github]\nenabled = false\ntimeout_s = 120\nroots = []\nmode = "legacy"\n'
    )
    config = _user(tmp_path, text)
    assert config.data_dir == Path("~/data").expanduser()
    assert config.default_tool_timeout_s == 5
    assert config.legacy_sse_fallback is True
    assert config.sources.claude_desktop is False
    assert config.sources.extra == ("x.json",)
    assert config.sampling.provider == "pkg.mod:factory"
    linear = config.servers["linear"]
    assert linear.mode == "modern"
    assert linear.enabled is False
    assert linear.timeout_s == 3
    assert linear.roots == ("file:///r",)
    assert linear.log_level == "info"
    assert linear.review_sampling_responses is True
    assert isinstance(linear.transport, HttpTransport)
    assert linear.transport.oauth is not None
    assert linear.transport.oauth.scopes == ("read",)
    assert linear.transport.oauth.client_secret is not None
    assert "cs" not in repr(linear)
    override = config.overrides["github"]
    assert (override.enabled, override.timeout_s, override.roots, override.mode) == (
        False,
        120,
        (),
        "legacy",
    )


def test_user_file_variable_problems_are_collected(tmp_path: Path) -> None:
    """REQ-REG-005: unresolved variables in the user file are problems, not errors."""
    config = _user(tmp_path, _text("mcphost.toml"), env={})
    assert set(config.servers) == {"fs", "remote"}
    assert _codes(config.problems) == [ConfigProblemCode.UNRESOLVED_VARIABLE] * 2
    assert {p.server_id for p in config.problems} == {"fs", "remote"}


@pytest.mark.anyio
async def test_aload_matches_load(tmp_path: Path) -> None:
    """REQ-REG-001: `aload()` reads through a worker thread with `load()` semantics."""
    path = tmp_path / "mcphost.toml"
    path.write_text(_text("mcphost.toml"), encoding="utf-8")
    loaded = await HostConfig.aload(path, cwd=CWD, env=ENV)
    assert loaded == HostConfig.load(path, cwd=CWD, env=ENV)


def test_unreadable_or_undecodable_user_file_raises(tmp_path: Path) -> None:
    """REQ-REG-006: an explicit user file that cannot be read or decoded is a ConfigError."""
    binary = tmp_path / "bin.toml"
    binary.write_bytes(b"\xff\xfeschema_version = 1")
    with pytest.raises(ConfigError, match="UTF-8"):
        HostConfig.load(binary, cwd=CWD, env=ENV)
    directory = tmp_path / "dir.toml"
    directory.mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        HostConfig.load(directory, cwd=CWD, env=ENV)


@pytest.mark.parametrize(
    ("text", "ids", "codes"),
    [
        ('{"servers": {"v": {"command": "a"}}}', ["v"], []),
        ('{"mcpServers": {"m": {"command": "a"}}}', ["m"], []),
        ("{not json", [], [ConfigProblemCode.PARSE_ERROR]),
    ],
)
def test_extra_files_accept_both_shapes(
    text: str, ids: list[str], codes: list[ConfigProblemCode]
) -> None:
    """REQ-REG-002, REQ-REG-006: `extra` files may be `mcpServers` or `servers` style."""
    outcome = mcp_json.parse_extra(text, _origin(SourceKind.EXTRA, 6), env=ENV, cwd=CWD)
    assert [d.id for d in outcome.definitions] == ids
    assert _codes(outcome.problems) == codes
