"""``Host`` composition root and ``ConnectionManager`` (F2-10; API §4.1, §4.5, Flow A).

Every Host here runs on a fake home, a tmp data dir and the fail keyring (secrets go to the
0600 file fallback): no test touches the real home, config or keyring.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from keyring.backends import fail
from sqlmodel import select

import reflex_mcphost
from reflex_mcphost.core.config.models import HostConfig, ServerDefinition, StdioTransport
from reflex_mcphost.core.config.paths import Platform
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.db_models import EraCacheRow
from reflex_mcphost.core.errors import (
    HostClosedError,
    MCPHostError,
    ServerUnavailableError,
    UnknownServerError,
)
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.prompter import DenyAllPrompter, TrustRequest
from reflex_mcphost.core.types import Consumer
from tests.conftest import MODERN, ThirdPartyFiles
from tests.process import pid_alive
from tests.unit.core.consent.factories import tool_request

pytestmark = pytest.mark.anyio
S = ServerState
TIMEOUT = 10.0
PY = sys.executable
SLEEPER = [PY, "-c", "import sys; sys.stdin.read()"]  # never speaks MCP; exits on EOF


def _toml_server(argv: list[str], **extra: Any) -> str:
    lines = ['transport = "stdio"', f"command = {json.dumps(argv[0])}"]
    lines.append(f"args = {json.dumps(argv[1:])}")
    for key, value in extra.items():
        if isinstance(value, dict):
            pairs = ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items())
            lines.append(f"{key} = {{ {pairs} }}")
        else:
            lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines)


@dataclass
class Setup:
    files: ThirdPartyFiles
    data_dir: Path
    env: dict[str, str]

    def write(self, servers: Mapping[str, str]) -> Path:
        body = [
            "schema_version = 1",
            "[host]",
            f"data_dir = {json.dumps(str(self.data_dir))}",
            "[sources]",
            "claude_desktop = false",
            "cursor = false",
            "vscode = false",
        ]
        for server_id, table in servers.items():
            body += [f"[servers.{server_id}]", table]
        path = self.files.user_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path

    def config(self, servers: Mapping[str, str]) -> HostConfig:
        return HostConfig.load(self.write(servers), cwd=self.files.cwd, env=self.env)

    def host(self, config: HostConfig | None, **kwargs: Any) -> Host:
        return Host(
            config,
            platform=self.files.platform,
            env=self.env,
            keyring_backend=fail.Keyring(),
            **kwargs,
        )


@pytest.fixture
def setup(third_party_files: ThirdPartyFiles, tmp_path: Path) -> Setup:
    return Setup(third_party_files, tmp_path / "data", {"PATH": "/usr/bin:/bin"})


MODERN_TABLE = _toml_server([PY, str(MODERN)])


@asynccontextmanager
async def started(host: Host) -> AsyncIterator[Host]:
    with anyio.fail_after(TIMEOUT * 2):
        async with host:
            yield host


async def _pid(host: Host, server_id: str) -> int:
    supervisor = host.connections.supervisor(server_id)
    async with supervisor.lease() as client:
        result = await client.call_tool("pid", {})
    return int(result.content[0].text)  # type: ignore[union-attr]


# ------------------------------------------------------------------ lifecycle (Flow A)


async def test_start_returns_before_ready_then_wait_ready(setup: Setup) -> None:
    """REQ-REG-001, REQ-CON-001, API §4.1: start() loads every definition with id, transport
    and origin, spawns one supervisor each and returns before any is ready; wait_ready
    resolves both."""
    host = setup.host(setup.config({"a": MODERN_TABLE, "b": MODERN_TABLE}))
    async with started(host):
        assert {d.id for d in host.registry.definitions()} == {"a", "b"}
        assert all(d.origin.path == setup.files.user_file for d in host.registry.definitions())
        assert {s.state for s in host.connections.statuses()} <= {S.DISCONNECTED, S.CONNECTING}
        for server_id in ("a", "b"):
            status = await host.connections.wait_ready(server_id, timeout=TIMEOUT)
            assert status.state is S.READY
            assert status.era == "modern"
        assert [s.server_id for s in host.connections.statuses()] == ["a", "b"]


async def test_init_does_no_io_and_start_loads_config(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API §4.1: __init__ performs no I/O; with config=None start() calls HostConfig.aload."""
    config = setup.config({})
    calls: list[Any] = []

    async def fake_aload(*args: Any, **kwargs: Any) -> HostConfig:
        calls.append(kwargs)
        return config

    monkeypatch.setattr(HostConfig, "aload", fake_aload)
    host = setup.host(None)
    assert calls == []
    assert not setup.data_dir.exists()
    for member in ("registry", "config", "database"):
        with pytest.raises(RuntimeError):
            getattr(host, member)
    assert host.prompter is None
    async with started(host):
        with pytest.raises(RuntimeError):
            await host.start()  # already started
        assert host.config is config
        assert len(calls) == 1
        assert calls[0]["env"] == setup.env
        assert (setup.data_dir / "mcphost.db").exists()
        assert host.registry.definitions() == []


async def test_database_failure_raises_mcphost_error(setup: Setup) -> None:
    """API §4.1, REQ-CNS-008: any DB open failure makes start() raise MCPHostError."""
    setup.data_dir.parent.mkdir(parents=True, exist_ok=True)
    setup.data_dir.write_text("not a directory", encoding="utf-8")
    host = setup.host(setup.config({"a": MODERN_TABLE}))
    with pytest.raises(MCPHostError):
        await host.start()
    await host.aclose()


async def test_aclose_is_idempotent_and_later_calls_fail(setup: Setup) -> None:
    """REQ-CON-009, API §3.4: aclose() disconnects everything and is idempotent; any call
    afterwards raises HostClosedError."""
    host = setup.host(setup.config({"a": MODERN_TABLE}))
    async with started(host):
        await host.connections.wait_ready("a", timeout=TIMEOUT)
        pid = await _pid(host, "a")
    await host.aclose()
    assert not pid_alive(pid)
    with pytest.raises(HostClosedError):
        host.connections.status("a")
    with pytest.raises(HostClosedError):
        await host.connections.connect("a")
    with pytest.raises(HostClosedError):
        await host.start()


async def test_consent_wired_and_rules_survive_restart(setup: Setup) -> None:
    """REQ-CNS-006, REQ-CNS-010, API §4.1: ``host.consent`` loads persistent rules at start;
    headless Hosts deny unmatched operations; the member follows the Host lifecycle."""
    config = setup.config({})
    host = setup.host(config)
    with pytest.raises(RuntimeError):
        host.consent  # noqa: B018 - property access is the assertion
    async with started(host):
        assert isinstance(host.consent, ConsentPolicy)
        assert (await host.consent.evaluate(tool_request())).source == "no_prompter"
        host.set_prompter(DenyAllPrompter())
        assert (await host.consent.evaluate(tool_request())).source == "prompt"
        rule = await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("srv"),
            consumer=Consumer.agent("bot"),
        )
    with pytest.raises(HostClosedError):
        host.consent  # noqa: B018 - property access is the assertion
    async with started(setup.host(config)) as again:
        assert again.consent.rules() == [rule]
        outcome = await again.consent.evaluate(tool_request())
        assert (outcome.allowed, outcome.rule_id) == (True, rule.id)


# ------------------------------------------------------------------ ConnectionManager


async def test_wait_ready_raises_when_server_fails(setup: Setup) -> None:
    """API §4.5: wait_ready raises ServerUnavailableError once the state becomes failed."""
    table = _toml_server(["no-such-mcp-server-xyz"])
    async with started(setup.host(setup.config({"bad": table}))) as host:
        with pytest.raises(ServerUnavailableError) as info:
            await host.connections.wait_ready("bad", timeout=TIMEOUT)
        assert info.value.state == "failed"
        assert host.connections.status("bad").reason == "command_not_found: no-such-mcp-server-xyz"


async def test_wait_ready_times_out(setup: Setup) -> None:
    """API §4.5: wait_ready raises TimeoutError when not ready in time."""
    async with started(setup.host(setup.config({"mute": _toml_server(SLEEPER)}))) as host:
        with pytest.raises(TimeoutError):
            await host.connections.wait_ready("mute", timeout=0.2)


async def test_disabled_and_config_error_states(setup: Setup) -> None:
    """REQ-REG-005, API §4.5: disabled definitions get `disabled`, ones with a variable
    problem `config_error`; neither is launched and wait_ready raises at once."""
    servers = {
        "off": _toml_server([PY, str(MODERN)], enabled=False),
        "broken": _toml_server([PY, str(MODERN)], env={"TOKEN": "${MCP_UNSET_VAR}"}),
    }
    async with started(setup.host(setup.config(servers))) as host:
        assert host.connections.status("off").state is S.DISABLED
        assert host.connections.status("broken").state is S.CONFIG_ERROR
        for server_id in ("off", "broken"):
            with pytest.raises(ServerUnavailableError):
                await host.connections.wait_ready(server_id, timeout=TIMEOUT)
            await host.connections.connect(server_id)  # no-op: nothing to launch
            assert host.connections.logs(server_id) == []


async def test_unknown_server_ids_raise(setup: Setup) -> None:
    """API §4.5: every ConnectionManager method raises UnknownServerError for unknown ids."""
    async with started(setup.host(setup.config({}))) as host:
        manager = host.connections
        with pytest.raises(UnknownServerError):
            manager.status("nope")
        with pytest.raises(UnknownServerError):
            manager.logs("nope")
        for method in (manager.connect, manager.disconnect, manager.reconnect):
            with pytest.raises(UnknownServerError):
                await method("nope")
        with pytest.raises(UnknownServerError):
            await manager.wait_ready("nope", timeout=1)


async def test_phase4_methods_not_implemented(setup: Setup) -> None:
    """API §4.5: authorize and sign_out arrive with OAuth (F4-06…); set_log_level and
    set_roots (F4-03/F4-04) know the registry and raise UnknownServerError for an unknown id."""
    async with started(setup.host(setup.config({}))) as host:
        manager = host.connections
        with pytest.raises(NotImplementedError):
            await manager.authorize("x")
        with pytest.raises(NotImplementedError):
            await manager.sign_out("x")
        with pytest.raises(UnknownServerError):
            await manager.set_log_level("x", "info")
        with pytest.raises(UnknownServerError):
            await manager.set_roots("x", [])


async def test_disconnect_connect_reconnect_and_logs(setup: Setup) -> None:
    """REQ-CON-006, REQ-CON-007, API §4.5: disconnect → disconnected (no auto-reconnect);
    connect starts again; reconnect restarts the process; logs() holds stderr lines."""
    async with started(setup.host(setup.config({"a": MODERN_TABLE}))) as host:
        manager = host.connections
        await manager.wait_ready("a", timeout=TIMEOUT)
        await manager.disconnect("a")
        assert manager.status("a").state is S.DISCONNECTED
        await manager.connect("a")
        await manager.wait_ready("a", timeout=TIMEOUT)
        pid = await _pid(host, "a")
        await manager.reconnect("a")
        await manager.wait_ready("a", timeout=TIMEOUT)
        assert await _pid(host, "a") != pid
        async with manager.supervisor("a").lease() as client:
            await client.call_tool("stderr", {"lines": 1})
        with anyio.fail_after(TIMEOUT):
            while not any("fixture stderr line 0" in e.text for e in manager.logs("a")):  # noqa: ASYNC110 - stderr arrives asynchronously
                await anyio.sleep(0.02)


# ------------------------------------------------------------------ reconcile (REQ-REG-013)


async def test_reload_reconciles_only_changed_servers(setup: Setup) -> None:
    """REQ-REG-013, DM §6: reload stops only the removed Server, restarts the changed one,
    connects the added one and leaves the unchanged one running; era-cache rows of removed
    Servers are pruned."""
    gone_table = _toml_server([PY, str(MODERN), "--stderr-interval", "120"])  # own cache key
    config = setup.config({"keep": MODERN_TABLE, "gone": gone_table, "edit": MODERN_TABLE})
    async with started(setup.host(config)) as host:
        manager = host.connections
        for server_id in ("keep", "gone", "edit"):
            await manager.wait_ready(server_id, timeout=TIMEOUT)
        keep_pid, edit_pid = await _pid(host, "keep"), await _pid(host, "edit")
        gone_supervisor = manager.supervisor("gone")
        changed = _toml_server([PY, str(MODERN), "--stderr-interval", "60"])
        setup.write({"keep": MODERN_TABLE, "edit": changed, "new": MODERN_TABLE})
        diff = await host.registry.reload()
        assert (diff.added, diff.removed, diff.changed) == (("new",), ("gone",), ("edit",))
        with pytest.raises(UnknownServerError):
            manager.status("gone")
        assert gone_supervisor.status.state is S.DISCONNECTED
        await manager.wait_ready("new", timeout=TIMEOUT)
        await manager.wait_ready("edit", timeout=TIMEOUT)
        assert await _pid(host, "keep") == keep_pid
        assert await _pid(host, "edit") != edit_pid
        assert "--stderr-interval" in manager.supervisor("edit").definition.transport.args  # type: ignore[union-attr]
        with host.database.session() as s:
            rows = {r.server_id for r in s.exec(select(EraCacheRow)).all()}
        assert "gone" not in rows


async def test_set_enabled_false_disables_running_server(setup: Setup) -> None:
    """REQ-REG-010, REQ-REG-013, API §4.5 (`any` → `disabled`): disabling a running Server
    stops it and reports `disabled`; enabling starts it again."""
    async with started(setup.host(setup.config({"a": MODERN_TABLE}))) as host:
        await host.connections.wait_ready("a", timeout=TIMEOUT)
        await host.registry.set_enabled("a", False)
        assert host.connections.status("a").state is S.DISABLED
        await host.registry.set_enabled("a", True)
        await host.connections.wait_ready("a", timeout=TIMEOUT)


# ------------------------------------------------------------------ trust (REQ-REG-011)


class FakePrompter:
    supports_elicitation = False

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[TrustRequest] = []

    async def confirm_trust(self, request: TrustRequest) -> bool:
        self.requests.append(request)
        return self.answer


def _project_server(setup: Setup) -> None:
    entry = {"command": PY, "args": [str(MODERN)]}
    setup.files.install_at(
        setup.files.cwd / ".mcp.json", text=json.dumps({"mcpServers": {"proj": entry}})
    )


async def test_untrusted_project_server_fails_others_ready(setup: Setup) -> None:
    """REQ-REG-011, REQ-CON-011: with no prompter a project stdio Server ends `failed`
    (`untrusted_project_server`) while the other Servers reach ready."""
    _project_server(setup)
    async with started(setup.host(setup.config({"a": MODERN_TABLE}))) as host:
        with pytest.raises(ServerUnavailableError):
            await host.connections.wait_ready("proj", timeout=TIMEOUT)
        assert host.connections.status("proj").reason == "untrusted_project_server"
        await host.connections.wait_ready("a", timeout=TIMEOUT)


async def test_set_prompter_is_used_by_the_trust_check(setup: Setup) -> None:
    """REQ-REG-011, API §4.1: set_prompter() replaces the prompter the trust check asks;
    a confirmation lets the project Server connect on reconnect()."""
    _project_server(setup)
    declining = FakePrompter(False)
    async with started(setup.host(setup.config({}), prompter=declining)) as host:
        with pytest.raises(ServerUnavailableError):
            await host.connections.wait_ready("proj", timeout=TIMEOUT)
        assert len(declining.requests) == 1
        accepting = FakePrompter(True)
        host.set_prompter(accepting)
        await host.connections.reconnect("proj")
        await host.connections.wait_ready("proj", timeout=TIMEOUT)
        assert accepting.requests[0].command == PY
        assert accepting.requests[0].resolved_command is not None


# ------------------------------------------------------------------ public surface


def test_public_reexports() -> None:
    """API §3.6: the Phase 2 surface is importable from `reflex_mcphost`."""
    for name in (
        "Host",
        "HostConfig",
        "ServerDefinition",
        "StdioTransport",
        "HttpTransport",
        "ServerState",
        "ServerStatus",
        "Consumer",
        "MCPHostError",
        "ServerUnavailableError",
        "UnknownServerError",
        "HostClosedError",
        "TrustRequest",
        "ConnectionManager",
        "ServerRegistry",
        "EventBus",
    ):
        assert name in reflex_mcphost.__all__, name
        assert getattr(reflex_mcphost, name) is not None
    assert reflex_mcphost.ServerDefinition is ServerDefinition
    assert reflex_mcphost.StdioTransport is StdioTransport
    assert reflex_mcphost.Host is Host
    assert reflex_mcphost.HostConfig is HostConfig
    assert isinstance(Platform, type)


PHASE3_EXPORTS = {
    "AuditLog": "reflex_mcphost.core.audit",
    "AuditUnavailableError": "reflex_mcphost.core.audit",
    "AuditRecordNotFoundError": "reflex_mcphost.core.audit",
    "ConsentStoreError": "reflex_mcphost.core.consent.store",
    "UnknownRuleError": "reflex_mcphost.core.consent.store",
    "AuditPage": "reflex_mcphost.core.audit",
    "AuditQuery": "reflex_mcphost.core.audit",
    "AuditRecord": "reflex_mcphost.core.audit",
    "ConsentAnswer": "reflex_mcphost.core.consent.models",
    "ConsentDecision": "reflex_mcphost.core.consent.models",
    "ConsentOutcome": "reflex_mcphost.core.consent.outcome",
    "ConsentPolicy": "reflex_mcphost.core.consent.policy",
    "ConsentRequest": "reflex_mcphost.core.consent.models",
    "ConsentRequested": "reflex_mcphost.core.events",
    "ConsentResolved": "reflex_mcphost.core.events",
    "ConsentRule": "reflex_mcphost.core.consent.models",
    "ConsentTarget": "reflex_mcphost.core.consent.models",
    "InvocationError": "reflex_mcphost.core.invoker",
    "InvocationFinished": "reflex_mcphost.core.events",
    "InvocationHandle": "reflex_mcphost.core.invoker",
    "InvocationOutcome": "reflex_mcphost.core.outcomes",
    "InvocationProgress": "reflex_mcphost.core.events",
    "InvocationResult": "reflex_mcphost.core.invoker",
    "InvocationStarted": "reflex_mcphost.core.events",
    "Invoker": "reflex_mcphost.core.invoker",
    "OperationKind": "reflex_mcphost.core.consent.outcome",
    "Progress": "reflex_mcphost.core.outcomes",
}


def test_phase3_surface_is_public() -> None:
    """API §3.6, §4.7-§4.9, §4.11: the consent, invoker and audit types the Host members
    expose are importable from `reflex_mcphost` and are the core objects."""
    import importlib

    for name, module in PHASE3_EXPORTS.items():
        assert name in reflex_mcphost.__all__, name
        assert getattr(reflex_mcphost, name) is getattr(importlib.import_module(module), name)


def test_package_import_is_light() -> None:
    """API §3.6: `import reflex_mcphost` loads neither the MCP SDK nor SQLModel; names
    resolve on first use."""
    import subprocess

    code = (
        "import sys, reflex_mcphost; "
        "assert 'mcp' not in sys.modules and 'sqlmodel' not in sys.modules, 'heavy import'; "
        "reflex_mcphost.Host; assert 'mcp' in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603 - fixed argv
    with pytest.raises(AttributeError):
        _ = reflex_mcphost.NoSuchName  # type: ignore[attr-defined]
    assert "Host" in dir(reflex_mcphost)
    assert set(reflex_mcphost.__all__) == {"__version__", *reflex_mcphost._EXPORTS}
