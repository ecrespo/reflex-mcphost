"""``servers_panel`` and its actions with a fake Host (F6-04; API §8, REQ-UI-003)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.config.models import RegistryDiff
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.errors import AuthorizationError, UnknownServerError
from reflex_mcphost.ui import actions
from reflex_mcphost.ui.components.servers_panel import servers_panel
from reflex_mcphost.ui.state import HostState
from tests.unit.ui.rig import published, redirect_target, run_background

pytestmark = pytest.mark.anyio
AUTH_URL = "https://as.example/authorize?state=state-sentinel-0604&code_challenge=x"


class Recorder:
    """Records every awaited method call as ``(name, args)``."""

    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]], fail: bool = False) -> None:
        self._calls = calls
        self._fail = fail

    def __getattr__(self, name: str) -> Any:
        async def method(*args: Any) -> Any:
            self._calls.append((name, args))
            if self._fail:
                raise UnknownServerError(str(args[0]) if args else "x")
            if name == "reload":
                return RegistryDiff(added=("a",), changed=("b", "c"))
            return None

        return method


class FakeFlow:
    def __init__(self, url: str | None, reason: str | None = None) -> None:
        self.server_id = "remote"
        self.authorization_url = url
        self.done = False
        self._reason = reason
        self.opened = anyio.Event()

    async def wait(self) -> None:
        if self._reason is not None:
            raise AuthorizationError(self.server_id, self._reason)  # type: ignore[arg-type]


class FakeConnections(Recorder):
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]], flow: FakeFlow | None = None):
        super().__init__(calls)
        self.flow = flow

    async def authorize(self, server_id: str) -> FakeFlow:
        self._calls.append(("authorize", (server_id,)))
        assert self.flow is not None
        return self.flow

    def logs(self, server_id: str) -> list[LogEntry]:
        self._calls.append(("logs", (server_id,)))
        now = datetime(2026, 11, 1, tzinfo=UTC)
        return [LogEntry(level="info", logger="stderr", timestamp=now, text="<b>line</b>")]


class FakeHost:
    def __init__(self, *, fail: bool = False, flow: FakeFlow | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.connections = FakeConnections(self.calls, flow)
        self.registry = Recorder(self.calls)
        if fail:
            self.connections = FakeConnections(self.calls, flow)
            self.connections._fail = True
            self.registry = Recorder(self.calls, fail=True)


@pytest.mark.parametrize(
    ("action", "call"),
    [
        ("connect", ("connect", ("srv",))),
        ("disconnect", ("disconnect", ("srv",))),
        ("reconnect", ("reconnect", ("srv",))),
        ("sign_out", ("sign_out", ("srv",))),
        ("enable", ("set_enabled", ("srv", True))),
        ("disable", ("set_enabled", ("srv", False))),
    ],
)
async def test_each_action_calls_one_core_method_with_the_row_id(
    action: str, call: tuple[str, tuple[Any, ...]]
) -> None:
    """REQ-UI-003, REQ-REG-010, REQ-AUTH-007: every row action calls exactly one public
    core method with the row's id; the notice names the action."""
    host = FakeHost()
    notice = await actions.server_action(host, action, "srv")
    assert host.calls == [call]
    assert notice.endswith(": done")


async def test_reload_and_log_level_and_failures() -> None:
    """REQ-REG-013, REQ-HSH-031: reload calls ``registry.reload`` once and reports the
    diff; the log level calls ``set_log_level``; a core error is a notice, not an
    exception; an unknown action calls nothing."""
    host = FakeHost()
    assert await actions.reload_servers(host) == "Reloaded: 1 added, 0 removed, 2 changed"
    assert await actions.set_log_level(host, "srv", "debug") == "Log level for srv: debug"
    assert host.calls == [("reload", ()), ("set_log_level", ("srv", "debug"))]
    broken = FakeHost(fail=True)
    assert "failed" in await actions.server_action(broken, "connect", "srv")
    assert "failed" in await actions.reload_servers(broken)
    assert "failed" in await actions.set_log_level(broken, "srv", "debug")
    assert await actions.server_action(broken, "explode", "srv") == "unknown action 'explode'"
    assert broken.calls == [
        ("connect", ("srv",)),
        ("reload", ()),
        ("set_log_level", ("srv", "debug")),
    ]


async def test_server_action_handler_sets_the_notice() -> None:
    """REQ-UI-003: the background handler runs the action against the running Host."""
    host = FakeHost()
    state = HostState(_reflex_internal_init=True)
    with published(host):
        await run_background(HostState.server_action, state, "srv", "reconnect")
        await run_background(HostState.reload_servers, state)
        await run_background(HostState.set_log_level, state, "srv", "info")
    assert host.calls == [
        ("reconnect", ("srv",)),
        ("reload", ()),
        ("set_log_level", ("srv", "info")),
    ]
    assert state.server_notice == "Log level for srv: info"
    await run_background(HostState.server_action, state, "srv", "connect")
    assert state.server_notice == "The Host is not running."


async def test_authorize_redirects_externally_and_never_stores_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-AUTH-002 (UI start), REQ-AUTH-008: Authorize calls ``connections.authorize``
    once and yields an external redirect to the authorization URL; the URL (and its
    ``state``) is in no state var and no log line; the outcome is shown."""
    caplog.set_level("DEBUG")
    host = FakeHost(flow=FakeFlow(AUTH_URL))
    state = HostState(_reflex_internal_init=True)
    with published(host):
        events = await run_background(HostState.authorize, state, "remote")
    [spec] = events
    assert redirect_target(spec) == (AUTH_URL, True)
    assert host.calls == [("authorize", ("remote",))]
    assert state.server_notice == "Authorize remote: signed in"
    dumped = json.dumps(state.dict(), default=str)
    assert "state-sentinel-0604" not in dumped
    assert "state-sentinel-0604" not in caplog.text


async def test_authorize_failure_shows_the_reason_code() -> None:
    """REQ-AUTH-008: a failed flow shows its ``AuthorizationError.reason`` code."""
    state = HostState(_reflex_internal_init=True)
    with published(FakeHost(flow=FakeFlow(AUTH_URL, reason="access_denied"))):
        await run_background(HostState.authorize, state, "remote")
    assert state.server_notice == "Authorize remote failed: access_denied"

    class Refusing(FakeConnections):
        async def authorize(self, server_id: str) -> FakeFlow:
            raise AuthorizationError(server_id, "not_auth_required")

    host = FakeHost()
    host.connections = Refusing(host.calls)
    with published(host):
        assert await run_background(HostState.authorize, state, "remote") == []
    assert state.server_notice == "Authorize remote failed: not_auth_required"


def test_select_server_loads_the_buffered_log() -> None:
    """REQ-UI-003, REQ-HSH-030: selecting a Server shows its buffered log lines (plain
    text, at most 200 rendered)."""
    host = FakeHost()
    state = HostState(_reflex_internal_init=True)
    with published(host):
        HostState.select_server.fn(state, "srv")
    assert host.calls == [("logs", ("srv",))]
    assert [line["text"] for line in state.server_logs] == ["<b>line</b>"]
    state.logs = {"srv": [{"text": str(i)} for i in range(1_000)]}
    assert len(state.server_logs) == 200
    assert state.server_logs[-1]["text"] == "999"


def test_servers_view_is_bounded_and_filtered() -> None:
    """REQ-NFR-004 (D7): at most 200 rows are rendered plus the count of the rest; the
    filter narrows on every input change."""
    state = HostState(_reflex_internal_init=True)
    state.servers = [{"id": f"srv-{i:04d}"} for i in range(250)]
    assert (len(state.servers_shown), state.servers_more) == (200, 50)
    HostState.set_server_filter.fn(state, "srv-024")
    assert [r["id"] for r in state.servers_shown] == [f"srv-024{i}" for i in range(10)]


def test_servers_panel_columns_and_plain_text() -> None:
    """REQ-UI-003, REQ-REG-007, Art. 5: the panel (``data-testid="servers-panel"``) shows
    state, reason, era / protocol, origin, transport, capabilities, enabled, env / header
    keys, shadowed and problems, with every action wired; no Markdown or raw HTML."""
    rendered = str(servers_panel())
    assert "servers-panel" in rendered
    for header in (
        "State",
        "Era / protocol",
        "Origin",
        "Transport",
        "Capabilities",
        "Enabled",
        "Env / header keys",
        "Shadowed / problems",
    ):
        assert header in rendered
    for key in ("reason", "protocol_version", "env_keys", "header_keys", "shadowed", "problems"):
        assert key in rendered
    for action in ("connect", "disconnect", "reconnect", "enable", "disable", "sign_out"):
        assert f"server-{action}" in rendered
    assert "server-authorize" in rendered
    assert "deprecated" in rendered
    for forbidden in ("Markdown", "dangerouslySetInnerHTML"):
        assert forbidden not in rendered


async def test_non_http_authorization_url_is_never_redirected() -> None:
    """REQ-AUTH-008 (second layer): an authorization URL that is not http(s) with a host
    emits no redirect; the notice is fixed and does not echo it."""
    state = HostState(_reflex_internal_init=True)
    with published(FakeHost(flow=FakeFlow("javascript:alert('state-sentinel-0604')"))):
        assert await run_background(HostState.authorize, state, "remote") == []
    assert "not an http(s) address" in state.server_notice
    assert "state-sentinel-0604" not in json.dumps(state.dict(), default=str)
