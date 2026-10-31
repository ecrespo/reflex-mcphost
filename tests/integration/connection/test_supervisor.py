"""ServerSupervisor against the fixture Servers (F2-07, DD-002, API §4.5, Flow B)."""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import anyio.lowlevel
import httpx2
import mcp_types as types
import pytest
from mcp import Client, StdioServerParameters
from pydantic import SecretStr

from reflex_mcphost.core.config.models import (
    ConfigOrigin,
    HttpTransport,
    ServerDefinition,
    SourceKind,
    StdioTransport,
)
from reflex_mcphost.core.connection.state import (
    JITTER_MAX,
    JITTER_MIN,
    MAX_DELAY_S,
    MAX_FAILURES,
    ServerState,
    ServerStatus,
)
from reflex_mcphost.core.connection.stderr import LineSink
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.connection.transports import ObservedTransport, build_transport
from reflex_mcphost.core.errors import ServerUnavailableError
from reflex_mcphost.core.events import EventBus, ServerStateChanged
from tests.conftest import RecordedHttp

pytestmark = pytest.mark.anyio
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
S = ServerState
TIMEOUT = 10.0


def stdio_def(
    params: StdioServerParameters,
    *,
    server_id: str = "modern",
    kind: SourceKind = SourceKind.MCPHOST_USER,
) -> ServerDefinition:
    return ServerDefinition(
        id=server_id,
        transport=StdioTransport(command=params.command, args=tuple(params.args)),
        origin=ConfigOrigin(kind=kind, path=Path("/proj/.mcp.json"), precedence=3),
    )


def http_def(url: str, *, kind: SourceKind = SourceKind.MCPHOST_USER) -> ServerDefinition:
    return ServerDefinition(
        id="remote",
        transport=HttpTransport(
            url=url,  # type: ignore[arg-type]
            headers={"X-Key": SecretStr("header-secret-000")},
        ),
        origin=ConfigOrigin(kind=kind, path=Path("/proj/.mcp.json"), precedence=3),
    )


NEVER_STARTS = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"])


class FakeSleep:
    """Records requested delays and returns at once (deterministic, fast backoff tests)."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await anyio.lowlevel.checkpoint()


class CountingFactory:
    def __init__(self, http_transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self.calls = 0
        self.last: ObservedTransport | None = None
        self._http = http_transport

    def __call__(self, definition: ServerDefinition, on_stderr: LineSink) -> ObservedTransport:
        self.calls += 1
        self.last = build_transport(definition, on_stderr=on_stderr, http_transport=self._http)
        return self.last


class Recorder:
    """Collects every server.state_changed of one bus."""

    def __init__(self) -> None:
        self.events: list[ServerStateChanged] = []

    def pairs(self) -> list[tuple[ServerState, ServerState]]:
        return [(e.old, e.new) for e in self.events]


@asynccontextmanager
async def running(
    definition: ServerDefinition, **kwargs: Any
) -> AsyncIterator[tuple[ServerSupervisor, Recorder]]:
    bus = EventBus()
    rec = Recorder()
    kwargs.setdefault("sleep", FakeSleep())
    async with anyio.create_task_group() as tg, bus.subscribe(buffer=1024) as events:

        async def collect() -> None:
            async for event in events:
                if isinstance(event, ServerStateChanged):
                    rec.events.append(event)

        tg.start_soon(collect)
        sup = ServerSupervisor(definition, events=bus, **kwargs)
        sup.start(tg)
        try:
            yield sup, rec
        finally:
            with anyio.fail_after(TIMEOUT):
                await sup.aclose()
            await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()


async def wait_state(sup: ServerSupervisor, *states: ServerState) -> ServerStatus:
    with anyio.fail_after(TIMEOUT):
        return await sup.wait_for(*states)


async def _call_text(sup: ServerSupervisor, tool: str) -> str:
    async with sup.lease() as client:
        result = await client.call_tool(tool, {})
    return str(result.content[0].text)  # type: ignore[union-attr]


async def test_modern_stdio_reaches_ready(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-001, REQ-CON-002, REQ-CON-010: a modern stdio Server reaches ready with era,
    protocol version, serverInfo and capabilities; each transition is one event."""
    async with running(stdio_def(modern_stdio)) as (sup, rec):
        status = await wait_state(sup, S.READY)
        assert status.era == "modern"
        assert status.protocol_version == "2026-07-28"
        assert status.server_info is not None
        assert status.server_info.name == "fixture-modern"
        assert status.capabilities is not None
        assert status.capabilities.tools is not None
        assert status.consecutive_failures == 0
        assert status.legacy_transport is False
        assert sup.status is status
    assert rec.pairs()[:2] == [(S.DISCONNECTED, S.CONNECTING), (S.CONNECTING, S.READY)]
    assert rec.pairs()[2:] == [(S.READY, S.DISCONNECTED)]


@pytest.mark.interop
async def test_legacy_stdio_reaches_ready(legacy_stdio: StdioServerParameters) -> None:
    """REQ-CON-001, REQ-CON-002: a legacy stdio Server reaches ready as era legacy."""
    async with running(stdio_def(legacy_stdio, server_id="legacy")) as (sup, _):
        status = await wait_state(sup, S.READY)
        assert status.era == "legacy"
        assert status.protocol_version == "2025-11-25"
        assert status.server_info is not None
        assert status.capabilities is not None
        assert status.capabilities.tools is not None
        assert json.loads(await _call_text(sup, "client_caps")) in ({}, None)


async def test_no_client_capability_declared(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-003: with no host-side callbacks registered, the Server sees no client
    capability (Phase 2 part; elicitation/sampling/roots arrive in F4-05)."""
    async with running(stdio_def(modern_stdio)) as (sup, _):
        await wait_state(sup, S.READY)
        assert json.loads(await _call_text(sup, "client_caps")) == {}


@posix_only
async def test_child_killed_reconnects(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-005, REQ-CON-010: killing the child → reconnecting (backoff) → ready."""
    sleep = FakeSleep()
    async with running(stdio_def(modern_stdio), sleep=sleep, rng=lambda: 0.5) as (sup, rec):
        await wait_state(sup, S.READY)
        pid = int(await _call_text(sup, "pid"))
        os.kill(pid, signal.SIGKILL)
        await wait_state(sup, S.RECONNECTING)
        status = await wait_state(sup, S.READY)
        assert int(await _call_text(sup, "pid")) != pid
        assert status.consecutive_failures == 0
    assert sleep.delays == [1.0]
    assert rec.pairs()[:4] == [
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.READY),
        (S.READY, S.RECONNECTING),
        (S.RECONNECTING, S.READY),
    ]


@pytest.mark.parametrize("jitter", [0.0, 1.0])
async def test_never_starting_server_fails_after_ten_attempts(jitter: float) -> None:
    """REQ-CON-005, REQ-CON-006, REQ-CON-010: exactly 10 attempts, delays within the jittered
    bounds and <= 60 s, then failed; each transition emitted once."""
    sleep = FakeSleep()
    factory = CountingFactory()
    async with running(
        stdio_def(NEVER_STARTS), sleep=sleep, rng=lambda: jitter, transport_factory=factory
    ) as (sup, rec):
        status = await wait_state(sup, S.FAILED)
        assert status.consecutive_failures == MAX_FAILURES
        assert status.next_retry_at is None
        assert status.reason is not None
        assert status.reason.startswith("transport_error")
        await anyio.sleep(0.05)
        assert factory.calls == MAX_FAILURES  # parked: no further attempt
    assert len(sleep.delays) == MAX_FAILURES - 1
    for n, delay in enumerate(sleep.delays):
        low, high = (2**n) * JITTER_MIN, (2**n) * JITTER_MAX
        assert min(low, MAX_DELAY_S) <= delay <= min(high, MAX_DELAY_S)
    assert max(sleep.delays) <= MAX_DELAY_S
    assert rec.pairs() == [
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.RECONNECTING),
        (S.RECONNECTING, S.FAILED),
        (S.FAILED, S.DISCONNECTED),
    ]


async def test_reconnecting_status_reports_next_retry() -> None:
    """REQ-CON-005: while reconnecting, the status carries failures and next_retry_at."""
    gate = anyio.Event()
    seen: list[ServerStatus] = []

    async def slow_sleep(delay: float) -> None:
        await gate.wait()

    async with running(stdio_def(NEVER_STARTS), sleep=slow_sleep) as (sup, _):
        status = await wait_state(sup, S.RECONNECTING)
        seen.append(status)
        gate.set()
    assert seen[0].consecutive_failures == 1
    assert seen[0].next_retry_at is not None
    assert seen[0].next_retry_at > seen[0].since


async def test_reconnect_resets_failure_counter() -> None:
    """REQ-CON-006: reconnect() leaves failed, resets the counter and tries again."""
    factory = CountingFactory()
    async with running(stdio_def(NEVER_STARTS), transport_factory=factory) as (sup, rec):
        await wait_state(sup, S.FAILED)
        await sup.reconnect()
        status = await wait_state(sup, S.FAILED)
        assert status.consecutive_failures == MAX_FAILURES
        assert factory.calls == 2 * MAX_FAILURES
    assert (S.FAILED, S.CONNECTING) in rec.pairs()


async def test_lease_after_block_exit_raises(modern_stdio: StdioServerParameters) -> None:
    """DD-002: a lease used after its Client block exited raises ServerUnavailableError;
    lease() outside ready raises too."""
    async with running(stdio_def(modern_stdio)) as (sup, _):
        await wait_state(sup, S.READY)
        async with sup.lease() as client:
            assert isinstance(client, Client)
        held = sup.current_lease
        assert held is not None
        await sup.stop()
        assert sup.status.state is S.DISCONNECTED
        with pytest.raises(ServerUnavailableError):
            _ = held.client
        with pytest.raises(ServerUnavailableError) as info:
            async with sup.lease():
                pass
        assert info.value.state == "disconnected"


async def test_stop_disconnects_without_reconnect(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-010: stop() → disconnected and stays there; connect() starts again."""
    factory = CountingFactory()
    async with running(stdio_def(modern_stdio), transport_factory=factory) as (sup, rec):
        await wait_state(sup, S.READY)
        await sup.stop()
        await anyio.sleep(0.05)
        assert sup.status.state is S.DISCONNECTED
        assert factory.calls == 1
        await sup.connect()
        await wait_state(sup, S.READY)
        await sup.connect()  # no-op while ready
        assert factory.calls == 2
    assert rec.pairs()[:4] == [
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.READY),
        (S.READY, S.DISCONNECTED),
        (S.DISCONNECTED, S.CONNECTING),
    ]


async def test_reconnect_from_ready(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-006: reconnect() on a ready Server = disconnect + connect (new process)."""
    async with running(stdio_def(modern_stdio)) as (sup, rec):
        await wait_state(sup, S.READY)
        pid = await _call_text(sup, "pid")
        await sup.reconnect()
        await wait_state(sup, S.READY)
        assert await _call_text(sup, "pid") != pid
    assert rec.pairs()[2:5] == [
        (S.READY, S.DISCONNECTED),
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.READY),
    ]


async def test_command_not_found_fails_without_retry() -> None:
    """REQ-CON-007: a missing executable → failed `command_not_found: <exe>`, no retry."""
    sleep = FakeSleep()
    factory = CountingFactory()
    definition = stdio_def(StdioServerParameters(command="no-such-mcp-server-xyz"))
    async with running(definition, sleep=sleep, transport_factory=factory) as (sup, rec):
        status = await wait_state(sup, S.FAILED)
        assert status.reason == "command_not_found: no-such-mcp-server-xyz"
        await anyio.sleep(0.05)
    assert factory.calls == 1
    assert sleep.delays == []
    assert rec.pairs()[:2] == [(S.DISCONNECTED, S.CONNECTING), (S.CONNECTING, S.FAILED)]


async def test_untrusted_project_stdio_never_spawns(modern_stdio: StdioServerParameters) -> None:
    """REQ-REG-011: an untrusted project stdio Server → failed `untrusted_project_server`,
    no retry, no process (the transport factory is never called)."""
    factory = CountingFactory()
    definition = stdio_def(modern_stdio, kind=SourceKind.MCP_JSON_PROJECT)
    for trust_check in (None, _answer(False), _raise):
        async with running(definition, transport_factory=factory, trust_check=trust_check) as (
            sup,
            _,
        ):
            status = await wait_state(sup, S.FAILED)
            assert status.reason == "untrusted_project_server"
    assert factory.calls == 0


async def test_untrusted_project_http_makes_no_request(
    modern_http_recorded: RecordedHttp,
) -> None:
    """REQ-REG-011: an untrusted project HTTP Server is never contacted (delta
    2026-10-project-trust): the fixture records no request at all."""
    definition = http_def(modern_http_recorded.url, kind=SourceKind.VSCODE_PROJECT)
    async with running(definition, trust_check=_answer(False)) as (sup, _):
        status = await wait_state(sup, S.FAILED)
        assert status.reason == "untrusted_project_server"
    assert modern_http_recorded.requests() == []


async def test_trusted_project_server_connects(modern_stdio: StdioServerParameters) -> None:
    """REQ-REG-011: a confirmed project stdio Server connects normally."""
    checked: list[str] = []

    async def trust(definition: ServerDefinition) -> bool:
        checked.append(definition.id)
        return True

    definition = stdio_def(modern_stdio, kind=SourceKind.CURSOR_PROJECT)
    async with running(definition, trust_check=trust) as (sup, _):
        await wait_state(sup, S.READY)
    assert checked == ["modern"]


async def test_modern_http_reaches_ready(modern_http: str) -> None:
    """REQ-CON-001, REQ-CON-002, REQ-CON-012: a modern Streamable HTTP Server reaches ready."""
    async with running(http_def(modern_http)) as (sup, _):
        status = await wait_state(sup, S.READY)
        assert status.era == "modern"


def _discover_handler(error: dict[str, Any] | None = None) -> Callable[..., httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST":
            return httpx2.Response(405)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx2.Response(202)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})

    return handler


async def test_unsupported_protocol_version_fails_without_retry() -> None:
    """REQ-CON-004: no mutual protocol version → failed (versions in the reason), no retry."""
    error = {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {"supported": ["2099-01-01"], "requested": "2026-07-28"},
    }
    factory = CountingFactory(httpx2.MockTransport(_discover_handler(error)))
    sleep = FakeSleep()
    async with running(http_def("http://mcp.test/mcp"), transport_factory=factory, sleep=sleep) as (
        sup,
        _,
    ):
        status = await wait_state(sup, S.FAILED)
        await anyio.sleep(0.05)
    assert status.reason == (
        "unsupported_protocol_version: requested 2026-07-28, supported 2099-01-01"
    )
    assert factory.calls == 1
    assert sleep.delays == []


async def test_http_401_with_metadata_is_auth_required() -> None:
    """REQ-AUTH-001 (state part; discovery is F4-07): HTTP 401 with WWW-Authenticate →
    auth_required, parked (no retry)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="x"'})

    factory = CountingFactory(httpx2.MockTransport(handler))
    async with running(http_def("http://mcp.test/mcp"), transport_factory=factory) as (sup, rec):
        status = await wait_state(sup, S.AUTH_REQUIRED)
        await anyio.sleep(0.05)
        assert status.reason == "auth_required"
    assert factory.calls == 1
    assert rec.pairs()[:2] == [(S.DISCONNECTED, S.CONNECTING), (S.CONNECTING, S.AUTH_REQUIRED)]


async def test_failures_are_isolated(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-011: one supervisor's failures (bad factory, raising on_ready hook, internal
    error) never affect another supervisor in the same task group."""

    def broken_factory(definition: ServerDefinition, on_stderr: LineSink) -> ObservedTransport:
        raise KeyError("factory bug")

    async def bad_hook(client: Client, status: ServerStatus) -> None:
        raise RuntimeError("hook bug")

    bus = EventBus()
    async with anyio.create_task_group() as tg:
        good = ServerSupervisor(stdio_def(modern_stdio), events=bus, on_ready=[bad_hook])
        bad = ServerSupervisor(
            stdio_def(modern_stdio, server_id="bad"),
            events=bus,
            transport_factory=broken_factory,
            sleep=FakeSleep(),
        )
        worse = ServerSupervisor(stdio_def(modern_stdio, server_id="worse"), events=bus)

        async def explode() -> None:
            raise RuntimeError("internal bug")

        worse._supervise_once = explode  # type: ignore[method-assign]
        for sup in (good, bad, worse):
            sup.start(tg)
        assert (await wait_state(bad, S.FAILED)).consecutive_failures == MAX_FAILURES
        assert (await wait_state(worse, S.FAILED)).reason == "internal_error"
        await wait_state(good, S.READY)
        assert json.loads(await _call_text(good, "client_caps")) == {}
        with anyio.fail_after(TIMEOUT):
            for sup in (good, bad, worse):
                await sup.aclose()


async def test_on_ready_hooks_receive_client(modern_stdio: StdioServerParameters) -> None:
    """DD-002, Flow B step 6: on_ready hooks (used by the Catalog in F2-11) get the
    live client and the ready status."""
    seen: list[tuple[str, ServerState]] = []
    done = anyio.Event()

    async def hook(client: Client, status: ServerStatus) -> None:
        tools = await client.list_tools()
        seen.append((tools.tools[0].name, status.state))
        done.set()

    async with running(stdio_def(modern_stdio), on_ready=[hook]) as (sup, _):
        await wait_state(sup, S.READY)
        with anyio.fail_after(TIMEOUT):
            await done.wait()
    assert seen == [("echo", S.READY)]


def _answer(value: bool) -> Callable[[ServerDefinition], Any]:
    async def check(definition: ServerDefinition) -> bool:
        return value

    return check


async def _raise(definition: ServerDefinition) -> bool:
    raise RuntimeError("prompter exploded")


async def test_http_401_while_reconnecting_is_auth_required() -> None:
    """REQ-AUTH-001 (state part), API §4.5 amendment: a 401 with auth metadata on a retry
    (state reconnecting) → auth_required, not failed; parked without further retries."""
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx2.ConnectError("refused")
        return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="x"'})

    factory = CountingFactory(httpx2.MockTransport(handler))
    async with running(http_def("http://mcp.test/mcp"), transport_factory=factory) as (sup, rec):
        status = await wait_state(sup, S.AUTH_REQUIRED)
        await anyio.sleep(0.05)
        assert status.reason == "auth_required"
        assert factory.calls == 2
    assert rec.pairs()[:3] == [
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.RECONNECTING),
        (S.RECONNECTING, S.AUTH_REQUIRED),
    ]


@posix_only
async def test_on_ready_hook_lives_while_ready(modern_stdio: StdioServerParameters) -> None:
    """DD-002, Flow B step 7 (F2-12): an on_ready hook may run for the whole ready period
    (Catalog watchers); it is cancelled when the transport is lost, and runs again on the
    next ready."""
    started = 0
    cancelled = anyio.Event()

    async def watcher(client: Client, status: ServerStatus) -> None:
        nonlocal started
        started += 1
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    async with running(stdio_def(modern_stdio), on_ready=[watcher]) as (sup, _):
        await wait_state(sup, S.READY)
        pid = int(await _call_text(sup, "pid"))
        os.kill(pid, signal.SIGKILL)
        with anyio.fail_after(TIMEOUT):
            await cancelled.wait()
        await wait_state(sup, S.RECONNECTING)
        await wait_state(sup, S.READY)
        with anyio.fail_after(TIMEOUT):
            while started < 2:  # noqa: ASYNC110 - hook start has no event
                await anyio.sleep(0.02)


async def test_client_uses_eight_input_rounds(modern_stdio: StdioServerParameters) -> None:
    """DD-001: catalog reads use the SDK auto-loop capped at 8 input rounds."""
    async with running(stdio_def(modern_stdio)) as (sup, _):
        await wait_state(sup, S.READY)
        async with sup.lease() as client:
            assert client.input_required_max_rounds == 8


@pytest.mark.interop
async def test_message_handler_receives_legacy_notifications(
    legacy_stdio: StdioServerParameters,
) -> None:
    """DD-002 (F2-12 legacy watcher seam): the supervisor's `message_handler` receives the
    legacy Server's notifications/tools/list_changed."""
    seen = anyio.Event()

    async def handler(message: object) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            seen.set()

    definition = stdio_def(legacy_stdio, server_id="legacy")
    async with running(definition, message_handler=handler) as (sup, _):
        await wait_state(sup, S.READY)
        await _call_text(sup, "add_tool")
        with anyio.fail_after(TIMEOUT):
            await seen.wait()
