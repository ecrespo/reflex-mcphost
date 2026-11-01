"""The Servers view against a real Host (F6-04; REQ-UI-003): modern and legacy stdio, and
the HTTP OAuth row (fixture AS + protected modern Server, F4-09a).

Actions run through the ``HostState`` handlers on a ``Tab`` with the Host served by
``HostRuntime``; rows come from a watcher; waits are on state changes and Host events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.config.models import HostConfig, SourceKind
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.events import ServerLog
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui.state import HostState
from tests.conftest import MODERN
from tests.integration.auth.conftest import Browser, browser, oauth_as, protected_modern_http
from tests.integration.auth.helpers import http_table
from tests.integration.test_host import MODERN_TABLE, TIMEOUT, Setup, _pid, _toml_server, setup
from tests.integration.ui.rig import Tab, watched
from tests.unit.ui.rig import published, redirect_target

pytestmark = pytest.mark.anyio
__all__ = ["browser", "oauth_as", "protected_modern_http", "setup"]  # re-exported fixtures
S = ServerState


@pytest.fixture(params=["modern", "legacy"])
def era_command(request: pytest.FixtureRequest) -> list[str]:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return [params.command, *params.args]
    return [sys.executable, str(MODERN)]


@asynccontextmanager
async def serving(host: Host) -> AsyncIterator[Tab]:
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            with published(host):
                async with watched(host, Tab()) as tab:
                    yield tab


def _row(tab: Tab, server_id: str) -> dict[str, Any]:
    return next((r for r in tab.state.servers if r["id"] == server_id), {})


async def _state(tab: Tab, server_id: str, state: ServerState) -> None:
    await tab.until(lambda s: _row(tab, server_id).get("state") == state.value)


async def test_disconnect_reconnect_and_logs(setup: Setup, era_command: list[str]) -> None:
    """REQ-UI-003, REQ-CON-010, REQ-HSH-030 (both eras): Disconnect → the row is
    ``disconnected``; Reconnect → ``ready``; the fixture's stderr lines reach the log view
    (``server.log`` events and the buffered log of the selected Server)."""
    host = setup.host(setup.config({"srv": _toml_server(era_command)}))
    async with serving(host) as tab:
        await _state(tab, "srv", S.READY)
        await tab.run(HostState.server_action, "srv", "disconnect")
        await _state(tab, "srv", S.DISCONNECTED)
        await tab.run(HostState.server_action, "srv", "reconnect")
        await _state(tab, "srv", S.READY)
        assert tab.state.server_notice == "Reconnect srv: done"
        await host.connections.wait_ready("srv", timeout=TIMEOUT)
        await host.consent.grant(
            effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server("srv")
        )
        async with host.events.subscribe() as events:
            result = await host.invoker.call("srv::stderr", {"lines": 2}, consumer=Consumer.human())
            assert result.ok, result.error
            with anyio.fail_after(TIMEOUT):
                async for event in events:
                    if isinstance(event, ServerLog) and event.entry.text.endswith("line 1"):
                        break
        await tab.until(
            lambda s: "fixture stderr line 1" in [x["text"] for x in s.logs.get("srv", [])]
        )
        tab.sync(HostState.select_server, "srv")
        texts = [line["text"] for line in tab.state.server_logs]
        assert {"fixture stderr line 0", "fixture stderr line 1"} <= set(texts)


def _sha256(path: Any) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_desktop_source(setup: Setup, command: list[str]) -> HostConfig:
    entry = {"command": command[0], "args": command[1:]}
    desktop = setup.files.install(
        SourceKind.CLAUDE_DESKTOP, text=json.dumps({"mcpServers": {"desk": entry}})
    )
    assert desktop.exists()
    path = setup.write({})
    text = path.read_text(encoding="utf-8").replace("claude_desktop = false\n", "")
    path.write_text(text, encoding="utf-8")
    return HostConfig.load(path, cwd=setup.files.cwd, env=setup.env)


async def test_disable_third_party_writes_only_the_override(
    setup: Setup, era_command: list[str]
) -> None:
    """REQ-REG-010, REQ-REG-008, Art. 6 (both eras): disabling a Server from a third-party
    file writes ``[overrides.<id>] enabled = false`` to ``mcphost.toml``, stops it, and
    leaves the source file byte-identical; Enable starts it again."""
    host = setup.host(_with_desktop_source(setup, era_command))
    desktop = setup.files.path(SourceKind.CLAUDE_DESKTOP)
    before = _sha256(desktop)
    async with serving(host) as tab:
        await _state(tab, "desk", S.READY)
        assert _row(tab, "desk")["origin_kind"] == SourceKind.CLAUDE_DESKTOP.value
        await tab.run(HostState.server_action, "desk", "disable")
        await _state(tab, "desk", S.DISABLED)
        own = setup.files.user_file.read_text(encoding="utf-8")
        assert "[overrides.desk]" in own
        assert "enabled = false" in own
        assert _sha256(desktop) == before
        await tab.run(HostState.server_action, "desk", "enable")
        await _state(tab, "desk", S.READY)
    assert _sha256(desktop) == before


async def test_reload_reconnects_only_the_changed_server(
    setup: Setup, era_command: list[str]
) -> None:
    """REQ-REG-013 (both eras): after editing the own ``mcphost.toml``, Reload reports one
    changed Server and only that Server reconnects (the other keeps its process)."""
    servers = {"a": _toml_server(era_command), "b": MODERN_TABLE}
    host = setup.host(setup.config(servers))
    async with serving(host) as tab:
        for server_id in servers:
            await host.connections.wait_ready(server_id, timeout=TIMEOUT)
        pid_a, pid_b = await _pid(host, "a"), await _pid(host, "b")
        setup.write(
            {**servers, "b": _toml_server([sys.executable, str(MODERN), "--list-delay", "0"])}
        )
        await tab.run(HostState.reload_servers)
        assert tab.state.server_notice == "Reloaded: 0 added, 0 removed, 1 changed"
        await host.connections.wait_ready("b", timeout=TIMEOUT)
        assert await _pid(host, "a") == pid_a
        assert await _pid(host, "b") != pid_b


async def test_oauth_row_authorize_and_sign_out(
    setup: Setup,
    oauth_as: Any,
    protected_modern_http: str,
    browser: Browser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-AUTH-002 (UI start), REQ-AUTH-007, REQ-AUTH-008, REQ-UI-003: the row is
    ``auth_required``; Authorize yields an external redirect to the AS; the URL and its
    ``state`` are in no state var and in no log line of the Host or the UI; the flow
    completes; Sign out → the next Connect parks in ``auth_required`` again."""
    caplog.set_level(logging.DEBUG)
    host = setup.host(setup.config({"remote": http_table(protected_modern_http)}))
    async with serving(host) as tab:
        await _state(tab, "remote", S.AUTH_REQUIRED)
        assert _row(tab, "remote")["reason"] == "auth_required"
        flow_events = HostState.authorize.fn(tab.proxy, "remote")
        spec = await flow_events.__anext__()
        url, external = redirect_target(spec)
        assert external is True
        assert url.startswith(f"{oauth_as.issuer}/authorize?")
        state_param = url.split("state=", 1)[1].split("&", 1)[0]
        await browser.open(url)
        with anyio.fail_after(TIMEOUT), pytest.raises(StopAsyncIteration):
            await flow_events.__anext__()
        assert tab.state.server_notice == "Authorize remote: signed in"
        await _state(tab, "remote", S.READY)
        dumped = json.dumps(tab.state.dict(), default=str)
        assert state_param not in dumped
        assert url not in dumped
        await tab.run(HostState.server_action, "remote", "sign_out")
        await _state(tab, "remote", S.DISCONNECTED)
        await tab.run(HostState.server_action, "remote", "connect")
        await _state(tab, "remote", S.AUTH_REQUIRED)
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("reflex_mcphost")]
    assert ours  # the Host and the UI did log
    assert not [m for m in ours if state_param in m]  # (the test's own browser logs it)
