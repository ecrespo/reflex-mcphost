"""Project-Server trust store (F2-05b, DM §2.4, Q-T1, API §4.10)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from keyring.backends import fail
from pydantic import SecretStr
from sqlmodel import select

from reflex_mcphost.core.config import mcp_json
from reflex_mcphost.core.config.models import (
    ConfigOrigin,
    HttpTransport,
    ServerDefinition,
    SourceKind,
    StdioTransport,
)
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.db_models import TrustedServerRow
from reflex_mcphost.core.hmac_key import InstallKey, load_or_create_install_key
from reflex_mcphost.core.prompter import RISKY_ENV_KEYS, Prompter, TrustRequest
from reflex_mcphost.core.secret_store import SecretStore
from reflex_mcphost.core.trust import TrustStore, destination, requires_trust, strip_userinfo

pytestmark = pytest.mark.anyio

ENV_SECRET = "env-value-0123456789"  # noqa: S105 - test sentinel
HEADER_SECRET = "Bearer header-secret-0123456789"  # noqa: S105 - test sentinel


class FakePrompter:
    supports_elicitation = False

    def __init__(self, answer: bool = True, *, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.requests: list[TrustRequest] = []

    async def confirm_trust(self, request: TrustRequest) -> bool:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.answer


def _origin(kind: SourceKind = SourceKind.MCP_JSON_PROJECT) -> ConfigOrigin:
    return ConfigOrigin(kind=kind, path=Path("/proj/.mcp.json"), precedence=3)


def stdio_def(
    *,
    kind: SourceKind = SourceKind.MCP_JSON_PROJECT,
    command: str = "node",
    args: tuple[str, ...] = ("server.js",),
    cwd: Path | None = Path("/proj"),
    env: Mapping[str, str] | None = None,
) -> ServerDefinition:
    values = {"API_KEY": ENV_SECRET, "NODE_OPTIONS": "--require x"} if env is None else env
    return ServerDefinition(
        id="proj",
        transport=StdioTransport(
            command=command,
            args=args,
            cwd=cwd,
            env={k: SecretStr(v) for k, v in values.items()},
        ),
        origin=_origin(kind),
    )


def http_def(
    *,
    url: str = "https://mcp.example.com/mcp?key=url-secret-0123",
    headers: Mapping[str, str] | None = None,
) -> ServerDefinition:
    values = {"Authorization": HEADER_SECRET} if headers is None else headers
    return ServerDefinition(
        id="remote",
        transport=HttpTransport(
            url=url,  # type: ignore[arg-type]
            headers={k: SecretStr(v) for k, v in values.items()},
        ),
        origin=_origin(),
    )


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[tuple[Database, InstallKey]]:
    db = open_database(tmp_path / "data")
    key = await load_or_create_install_key(
        SecretStore(tmp_path / "data", db, keyring_backend=fail.Keyring())
    )
    yield db, key
    db.close()


@pytest.fixture
def store(env: tuple[Database, InstallKey]) -> TrustStore:
    return TrustStore(*env)


def _rows(db: Database) -> list[TrustedServerRow]:
    with db.session() as s:
        return list(s.exec(select(TrustedServerRow)).all())


def test_fake_prompter_satisfies_protocol() -> None:
    """REQ-REG-011: the minimal Prompter protocol is confirm_trust + supports_elicitation."""
    prompter: Prompter = FakePrompter()
    assert prompter.supports_elicitation is False


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (SourceKind.MCPHOST_PROJECT, True),
        (SourceKind.MCP_JSON_PROJECT, True),
        (SourceKind.CURSOR_PROJECT, True),
        (SourceKind.VSCODE_PROJECT, True),
        (SourceKind.MCPHOST_USER, False),
        (SourceKind.EXTRA, False),
        (SourceKind.CURSOR_USER, False),
        (SourceKind.VSCODE_USER, False),
        (SourceKind.CLAUDE_DESKTOP, False),
    ],
)
def test_requires_trust_only_for_project_sources(kind: SourceKind, expected: bool) -> None:
    """REQ-REG-011: only project sources (1, 3, 4, 5) need trust; extra and user never."""
    assert requires_trust(stdio_def(kind=kind)) is expected


def test_http_project_definitions_require_trust() -> None:
    """REQ-REG-011: HTTP definitions from project sources need trust too (delta
    2026-10-project-trust): a project URL/header may expand host env secrets."""
    assert requires_trust(http_def()) is True


async def test_first_launch_asks_once_second_does_not(store: TrustStore) -> None:
    """REQ-REG-011: confirmed once, then remembered for the same fingerprint."""
    prompter = FakePrompter(True)
    assert await store.require_trust(stdio_def(), prompter) is True
    assert await store.require_trust(stdio_def(), prompter) is True
    assert len(prompter.requests) == 1


@pytest.mark.parametrize(
    "changed",
    [
        stdio_def(env={"API_KEY": "another-value-0000", "NODE_OPTIONS": "--require x"}),
        stdio_def(env={"API_KEY": ENV_SECRET, "NODE_OPTIONS": "--require y"}),
        stdio_def(env={"API_KEY": ENV_SECRET}),
        stdio_def(args=("server.js", "--evil")),
        stdio_def(cwd=Path("/elsewhere")),
        stdio_def(command="python"),
    ],
    ids=["env-value", "risky-env-value", "env-name", "arg", "cwd", "command"],
)
async def test_changes_re_ask(store: TrustStore, changed: ServerDefinition) -> None:
    """REQ-REG-011: a change to command, args, cwd or any env name/value re-asks."""
    prompter = FakePrompter(True)
    assert await store.require_trust(stdio_def(), prompter)
    assert await store.require_trust(changed, prompter)
    assert len(prompter.requests) == 2


async def test_origin_path_change_re_asks(store: TrustStore) -> None:
    """REQ-REG-011: the origin path is part of the fingerprint."""
    prompter = FakePrompter(True)
    base = stdio_def()
    moved = base.model_copy(
        update={
            "origin": ConfigOrigin(kind=base.origin.kind, path=Path("/x/.mcp.json"), precedence=3)
        }
    )
    assert await store.require_trust(base, prompter)
    assert await store.require_trust(moved, prompter)
    assert len(prompter.requests) == 2


async def test_trust_request_has_keys_but_no_values(store: TrustStore) -> None:
    """REQ-REG-011: TrustRequest lists env keys (risky ones flagged), never values."""
    prompter = FakePrompter(True)
    await store.require_trust(stdio_def(), prompter)
    (request,) = prompter.requests
    assert request.server_id == "proj"
    assert request.transport == "stdio"
    assert request.command == "node"
    assert request.args == ("server.js",)
    assert request.cwd == Path("/proj")
    assert request.env_keys == ("API_KEY", "NODE_OPTIONS")
    assert request.risky_env_keys == ("NODE_OPTIONS",)
    assert request.id.startswith("req_")
    dumped = request.model_dump_json() + repr(request)
    assert ENV_SECRET not in dumped
    assert "--require x" not in dumped


def test_risky_env_list_matches_api() -> None:
    """REQ-REG-011: the built-in list of code-loading env variables (API §4.10)."""
    assert {
        "NODE_OPTIONS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PERL5OPT",
        "RUBYOPT",
        "JAVA_TOOL_OPTIONS",
        "BASH_ENV",
        "ENV",
        # security review 2026-10-31: binary/module search paths and helper commands
        "PATH",
        "PATHEXT",
        "NODE_PATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "GIT_SSH_COMMAND",
        "LD_AUDIT",
        "DYLD_FRAMEWORK_PATH",
    } == RISKY_ENV_KEYS


async def test_stored_row_has_no_env_value(
    store: TrustStore, env: tuple[Database, InstallKey]
) -> None:
    """REQ-REG-011: the trusted_server row holds id, keyed fingerprint and origin path only."""
    await store.require_trust(stdio_def(), FakePrompter(True))
    (row,) = _rows(env[0])
    assert row.server_id == "proj"
    assert row.fingerprint.startswith("hmac-sha256:")
    assert row.origin_path == str(Path("/proj/.mcp.json"))
    assert ENV_SECRET not in repr(row)


@pytest.mark.parametrize(
    "kind", [SourceKind.EXTRA, SourceKind.MCPHOST_USER, SourceKind.CLAUDE_DESKTOP]
)
async def test_extra_and_user_sources_never_ask(
    store: TrustStore, env: tuple[Database, InstallKey], kind: SourceKind
) -> None:
    """REQ-REG-011: non-project sources are trusted without a prompt and without a row."""
    prompter = FakePrompter(False)
    assert await store.require_trust(stdio_def(kind=kind), prompter) is True
    assert await store.require_trust(stdio_def(kind=kind), None) is True
    assert prompter.requests == []
    assert _rows(env[0]) == []


async def test_decline_is_untrusted_and_not_stored(
    store: TrustStore, env: tuple[Database, InstallKey]
) -> None:
    """REQ-REG-011: a decline fails closed, is not remembered and asks again next time."""
    prompter = FakePrompter(False)
    assert await store.require_trust(stdio_def(), prompter) is False
    assert await store.require_trust(stdio_def(), prompter) is False
    assert len(prompter.requests) == 2
    assert _rows(env[0]) == []


async def test_no_prompter_is_untrusted(store: TrustStore) -> None:
    """REQ-REG-011: no prompter → untrusted (fail closed)."""
    assert await store.require_trust(stdio_def(), None) is False


async def test_prompter_raising_is_untrusted(
    store: TrustStore, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-REG-011: a prompter exception → untrusted; the log names no env value."""
    prompter = FakePrompter(error=RuntimeError(f"ui crashed {ENV_SECRET}"))
    with caplog.at_level(logging.DEBUG, logger="reflex_mcphost"):
        assert await store.require_trust(stdio_def(), prompter) is False
    assert "untrusted" in caplog.text
    assert ENV_SECRET not in caplog.text


async def test_non_bool_answer_is_untrusted(store: TrustStore) -> None:
    """REQ-REG-011: only a literal True confirms (fail closed)."""
    prompter: Any = FakePrompter(True)
    prompter.answer = "yes"
    assert await store.require_trust(stdio_def(), prompter) is False


async def test_db_failure_is_untrusted(env: tuple[Database, InstallKey]) -> None:
    """REQ-REG-011: a trust store that cannot be read fails closed (Art. 3)."""
    db, key = env

    class BrokenDatabase(Database):
        async def run(self, fn: Any, *args: Any) -> Any:
            raise sqlite3.OperationalError("disk I/O error")

    broken = TrustStore(BrokenDatabase(db.path, db.engine), key)
    prompter = FakePrompter(True)
    assert await broken.require_trust(stdio_def(), prompter) is False
    assert prompter.requests == []


async def test_http_project_trust(store: TrustStore) -> None:
    """REQ-REG-011: an HTTP project definition asks once; header keys shown, values never;
    the expanded URL's query/userinfo never reach the TrustRequest."""
    prompter = FakePrompter(True)
    assert await store.require_trust(http_def(), prompter)
    assert await store.require_trust(http_def(), prompter)
    (request,) = prompter.requests
    assert request.transport == "http"
    assert request.header_keys == ("Authorization",)
    assert request.command == ""
    assert request.env_keys == ()
    assert request.destination == "https://mcp.example.com"
    assert request.url_template is None  # API-built definition: no file template
    dumped = request.model_dump_json() + repr(request)
    assert HEADER_SECRET not in dumped
    assert "url-secret-0123" not in dumped


@pytest.mark.parametrize(
    "changed",
    [
        http_def(headers={"Authorization": "Bearer other-secret-000000"}),
        http_def(url="https://mcp.example.com/mcp?key=other-secret-0123"),
        http_def(headers={"X-Api-Key": HEADER_SECRET}),
    ],
    ids=["header-value", "url", "header-name"],
)
async def test_http_changes_re_ask(store: TrustStore, changed: ServerDefinition) -> None:
    """REQ-REG-011: a change to the expanded URL or any header name/value re-asks."""
    prompter = FakePrompter(True)
    assert await store.require_trust(http_def(), prompter)
    assert await store.require_trust(changed, prompter)
    assert len(prompter.requests) == 2


async def test_fingerprints_differ_between_installs(tmp_path: Path, store: TrustStore) -> None:
    """REQ-REG-011: fingerprints are keyed per install (DM §2.4)."""
    other = TrustStore(store.db, InstallKey(b"\x01" * 32))
    assert store.fingerprint(stdio_def()) != other.fingerprint(stdio_def())
    assert store.fingerprint(stdio_def()) == store.fingerprint(stdio_def())


async def test_risky_env_keys_match_case_insensitively(store: TrustStore) -> None:
    """REQ-REG-011: risky names are flagged whatever their case (Windows env names are
    case-insensitive); the name is reported as written."""
    prompter = FakePrompter(True)
    env = {"Path": "/x", "node_options": "--require y", "HOME": "/h"}
    await store.require_trust(stdio_def(env=env), prompter)
    (request,) = prompter.requests
    assert request.risky_env_keys == ("Path", "node_options")


def _tool(directory: Path, name: str = "mcp-tool") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bits")
async def test_trust_request_shows_resolved_command(store: TrustStore, tmp_path: Path) -> None:
    """REQ-REG-011 (security review): the TrustRequest names the executable that would
    actually run, resolved with the definition's cwd and PATH; None when unresolvable."""
    tool = _tool(tmp_path / "bin")
    prompter = FakePrompter(True)
    await store.require_trust(
        stdio_def(command="mcp-tool", env={"PATH": str(tool.parent)}), prompter
    )
    await store.require_trust(stdio_def(command="no-such-tool-xyz", env={}), prompter)
    resolved, missing = prompter.requests
    assert resolved.resolved_command == str(tool)
    assert missing.resolved_command is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bits")
async def test_resolved_command_change_re_asks(store: TrustStore, tmp_path: Path) -> None:
    """REQ-REG-011 (security review): the resolved executable is part of the fingerprint, so
    the same definition re-asks once its PATH lookup finds a different binary."""
    first, second = tmp_path / "a", tmp_path / "b"
    _tool(first)
    path = f"{first}{os.pathsep}{second}"
    definition = stdio_def(command="mcp-tool", env={"PATH": path})
    prompter = FakePrompter(True)
    assert await store.require_trust(definition, prompter)
    assert await store.require_trust(definition, prompter)
    assert len(prompter.requests) == 1
    (first / "mcp-tool").unlink()
    _tool(second)  # same definition, a different binary now wins the lookup
    assert await store.require_trust(definition, prompter)
    assert len(prompter.requests) == 2
    assert prompter.requests[1].resolved_command == str(second / "mcp-tool")


async def test_http_trust_request_shows_unexpanded_template(
    store: TrustStore, tmp_path: Path
) -> None:
    """REQ-REG-011 (amendment 2026-10-31): the HTTP TrustRequest shows the URL as written in
    the file (unexpanded ${VAR}), never the expanded value; the fingerprint still covers the
    expanded URL."""
    text = '{"mcpServers": {"remote": {"url": "https://${MCP_HOST}/mcp?key=${MCP_KEY}"}}}'
    origin = ConfigOrigin(
        kind=SourceKind.MCP_JSON_PROJECT, path=tmp_path / ".mcp.json", precedence=3
    )

    def parsed(key: str) -> ServerDefinition:
        env = {"MCP_HOST": "mcp.example.com", "MCP_KEY": key}
        (definition,) = mcp_json.parse(text, origin, env=env, cwd=tmp_path).definitions
        return definition

    prompter = FakePrompter(True)
    assert await store.require_trust(parsed("url-secret-0123"), prompter)
    (request,) = prompter.requests
    assert request.url_template == "https://${MCP_HOST}/mcp?key=${MCP_KEY}"
    assert request.destination == "https://mcp.example.com"  # authoritative: expanded host
    assert "url-secret-0123" not in request.model_dump_json() + repr(request)
    assert await store.require_trust(parsed("other-secret-0123"), prompter)
    assert len(prompter.requests) == 2


def test_url_template_is_private() -> None:
    """REQ-REG-011, API §4.3: the unexpanded template is kept privately: excluded from
    equality, dumps and repr of the definition."""
    text = '{"mcpServers": {"remote": {"url": "https://${MCP_HOST}/mcp"}}}'
    origin = ConfigOrigin(kind=SourceKind.MCP_JSON_PROJECT, path=Path("/p/.mcp.json"), precedence=3)
    env = {"MCP_HOST": "mcp.example.com"}
    (definition,) = mcp_json.parse(text, origin, env=env, cwd=Path("/p")).definitions
    plain = definition.model_copy(
        update={"transport": HttpTransport(url="https://mcp.example.com/mcp")}  # type: ignore[arg-type]
    )
    assert isinstance(definition.transport, HttpTransport)
    assert definition.transport.url_template == "https://${MCP_HOST}/mcp"
    assert definition == plain
    assert hash(definition.transport) == hash(plain.transport)
    assert "MCP_HOST" not in definition.model_dump_json() + repr(definition)


def _parsed_http(url: str, env: dict[str, str], tmp_path: Path) -> ServerDefinition:
    text = json.dumps({"mcpServers": {"remote": {"url": url}}})
    origin = ConfigOrigin(
        kind=SourceKind.MCP_JSON_PROJECT, path=tmp_path / ".mcp.json", precedence=3
    )
    (definition,) = mcp_json.parse(text, origin, env=env, cwd=tmp_path).definitions
    return definition


async def test_destination_reveals_host_hidden_by_template(
    store: TrustStore, tmp_path: Path
) -> None:
    """REQ-REG-011 (security review): a template like https://${HOST}/mcp cannot hide where
    requests go — `destination` is scheme://host[:port] of the expanded URL."""
    prompter = FakePrompter(True)
    definition = _parsed_http("https://${HOST}:8443/mcp", {"HOST": "evil.example"}, tmp_path)
    await store.require_trust(definition, prompter)
    (request,) = prompter.requests
    assert request.destination == "https://evil.example:8443"
    assert request.url_template == "https://${HOST}:8443/mcp"


async def test_userinfo_cannot_mislead(store: TrustStore, tmp_path: Path) -> None:
    """REQ-REG-011 (security review): `https://good.com@evil.com/mcp` → destination is
    evil.com and the shown template has its userinfo removed."""
    prompter = FakePrompter(True)
    definition = _parsed_http("https://good.com@evil.com/mcp", {}, tmp_path)
    await store.require_trust(definition, prompter)
    (request,) = prompter.requests
    assert request.destination == "https://evil.com"
    assert request.url_template == "https://evil.com/mcp"
    assert "good.com" not in request.model_dump_json()


async def test_path_secret_never_shown(store: TrustStore, tmp_path: Path) -> None:
    """REQ-REG-011, Art. 5: a secret expanded into the URL path or userinfo never appears in
    the TrustRequest."""
    prompter = FakePrompter(True)
    secret = "path-secret-0123456789"  # noqa: S105 - test sentinel
    definition = _parsed_http(
        "https://${CRED}@mcp.example.com/t/${TOKEN}/mcp",
        {"TOKEN": secret, "CRED": "user:" + secret},
        tmp_path,
    )
    await store.require_trust(definition, prompter)
    (request,) = prompter.requests
    dumped = request.model_dump_json() + repr(request)
    assert secret not in dumped
    assert request.destination == "https://mcp.example.com"
    assert request.url_template == "https://mcp.example.com/t/${TOKEN}/mcp"


def test_strip_userinfo_edge_cases() -> None:
    """REQ-REG-011: userinfo removal keeps scheme-less text and IPv6 destinations intact."""
    assert strip_userinfo("not a url") == "not a url"
    assert strip_userinfo("https://a@b@host:1?q=x@y") == "https://host:1?q=x@y"
    assert destination("http://[::1]:8080/mcp") == "http://[::1]:8080"
