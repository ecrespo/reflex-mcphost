"""Transport factory against the fixture Servers (F2-06, spike S-2, DD-001)."""

from __future__ import annotations

import logging
import os
import signal
import stat
import sys
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client, StdioServerParameters
from pydantic import SecretStr

from reflex_mcphost.core.config.models import HttpTransport, ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.stderr import LogEntry, LogRing
from reflex_mcphost.core.connection.transports import (
    CommandNotFoundError,
    ObservedTransport,
    build_transport,
    resolve_executable,
)
from reflex_mcphost.core.events import EventBus, HostEvent, ServerLog
from reflex_mcphost.core.secrets import register_secret
from tests.conftest import MODERN, RecordedHttp

pytestmark = pytest.mark.anyio
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

TOKEN = "Bearer static-token-0123456789"  # noqa: S105 - test sentinel


def stdio_definition(params: StdioServerParameters, **env: str) -> ServerDefinition:
    return ServerDefinition(
        id="modern",
        transport=StdioTransport(
            command=params.command,
            args=tuple(params.args),
            env={k: SecretStr(v) for k, v in env.items()},
        ),
    )


def http_definition(url: str, **headers: str) -> ServerDefinition:
    return ServerDefinition(
        id="remote",
        transport=HttpTransport(
            url=url,  # type: ignore[arg-type]
            headers={k: SecretStr(v) for k, v in headers.items()},
        ),
    )


def _text(result: Any) -> str:
    return str(result.content[0].text)


async def test_stdio_stderr_lands_in_ring_and_events(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-007: stderr lines of a stdio Server go to its ring buffer and to server.log."""
    bus = EventBus()
    three = anyio.Event()

    def publish(entry: LogEntry) -> None:
        bus.publish(ServerLog(server_id="modern", entry=entry))
        if len(ring) >= 3:
            three.set()

    ring = LogRing(50, on_entry=publish)
    seen: list[HostEvent] = []
    async with bus.subscribe() as events:
        transport = build_transport(stdio_definition(modern_stdio), on_stderr=ring.add)
        async with Client(transport) as client:
            await client.call_tool("stderr", {"lines": 3})
            with anyio.fail_after(5):
                await three.wait()
        with anyio.fail_after(5):
            async for event in events:
                seen.append(event)
                if len(seen) == 3:
                    break
    assert [e.text for e in ring.snapshot()][:3] == [f"fixture stderr line {i}" for i in range(3)]
    assert all(isinstance(e, ServerLog) and isinstance(e.entry, LogEntry) for e in seen)


async def test_stdio_env_reaches_child_and_is_redacted(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-007: env values reach the child; echoed to stderr they are masked."""
    secret = "child-env-secret-0123456789"  # noqa: S105 - test sentinel
    first = anyio.Event()
    ring = LogRing(50, on_entry=lambda _: first.set())
    code = "import os,sys; sys.stderr.write(os.environ['FIX_SECRET'] + chr(10)); sys.stderr.flush()"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", f"{code}; import runpy; runpy.run_path({str(MODERN)!r}, run_name='__main__')"],
    )
    transport = build_transport(stdio_definition(params, FIX_SECRET=secret), on_stderr=ring.add)
    async with Client(transport) as client:
        await client.list_tools()
        with anyio.fail_after(5):
            await first.wait()
    assert ring.snapshot()[0].text == "**********"
    assert secret not in repr(ring.snapshot())


@posix_only
async def test_closed_fires_quickly_after_child_killed(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-005: the transport's `closed` event fires < 1 s after the child is killed."""
    transport = build_transport(stdio_definition(modern_stdio))
    assert isinstance(transport, ObservedTransport)
    async with Client(transport) as client:
        pid = int(_text(await client.call_tool("pid", {})))
        assert not transport.closed.is_set()
        os.kill(pid, signal.SIGKILL)
        with anyio.fail_after(1):
            await transport.closed.wait()


async def test_transport_is_single_use(modern_stdio: StdioServerParameters) -> None:
    """DD-002: a transport (like the Client using it) is built per connection attempt."""
    transport = build_transport(stdio_definition(modern_stdio))
    async with Client(transport) as client:
        await client.list_tools()
    with pytest.raises(RuntimeError, match="single-use"):
        await transport.__aenter__()


async def test_missing_executable_raises_command_not_found(tmp_path: Path) -> None:
    """REQ-CON-007: a stdio command that cannot be resolved fails before any spawn."""
    definition = ServerDefinition(
        id="missing", transport=StdioTransport(command="no-such-mcp-server-xyz", cwd=tmp_path)
    )
    with pytest.raises(CommandNotFoundError) as info:
        async with Client(build_transport(definition)):
            pass
    assert info.value.executable == "no-such-mcp-server-xyz"
    assert "no-such-mcp-server-xyz" in str(info.value)


def test_resolve_executable_honours_cwd_and_path(tmp_path: Path) -> None:
    """REQ-CON-007: relative paths resolve against cwd; bare names against the env PATH."""
    name = "my-server.cmd" if sys.platform == "win32" else "my-server"  # PATHEXT on Windows
    tool = tmp_path / "bin" / name
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    assert resolve_executable(f"bin/{name}", cwd=tmp_path, path=None) is not None
    assert resolve_executable(f"bin/{name}", cwd=tmp_path / "bin", path=None) is None
    assert resolve_executable("my-server", cwd=None, path=str(tool.parent)) is not None
    assert resolve_executable("my-server", cwd=None, path=str(tmp_path)) is None
    assert resolve_executable(sys.executable, cwd=None, path="") == sys.executable


async def test_http_headers_reach_server(
    modern_http_recorded: RecordedHttp, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-CON-016, REQ-CON-017, REQ-AUTH-009: modern HTTP requests carry Accept,
    MCP-Protocol-Version, Mcp-Method, Mcp-Name (base64 sentinel for non-ASCII) and a mirrored
    Mcp-Param-* header; a static Authorization header reaches the Server and is never logged."""
    register_secret(TOKEN)
    definition = http_definition(modern_http_recorded.url, Authorization=TOKEN)
    with caplog.at_level(logging.DEBUG):
        async with Client(build_transport(definition)) as client:
            assert client.protocol_version == "2026-07-28"
            await client.list_tools()
            assert _text(await client.call_tool("regional", {"region": "eu-west"})) == "eu-west"
            assert _text(await client.call_tool("grüße", {})) == "hallo"
    posts = [r["headers"] for r in modern_http_recorded.requests() if r["method"] == "POST"]
    assert posts
    assert all(h["authorization"] == TOKEN for h in posts)
    assert all(
        "application/json" in h["accept"] and "text/event-stream" in h["accept"] for h in posts
    )
    assert all(h["mcp-protocol-version"] == "2026-07-28" for h in posts)
    calls = [h for h in posts if h.get("mcp-method") == "tools/call"]
    assert {h["mcp-name"] for h in calls} == {"regional", "=?base64?Z3LDvMOfZQ==?="}
    assert [h["mcp-param-region"] for h in calls if "mcp-param-region" in h] == ["eu-west"]
    assert "static-token-0123456789" not in caplog.text


async def test_invalid_x_mcp_header_tool_is_dropped(
    modern_http_recorded: RecordedHttp, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-CON-018: a tool with an invalid x-mcp-header annotation is excluded with a warning
    naming the tool and the reason (SDK ClientSession behaviour, DD-001)."""
    with caplog.at_level(logging.WARNING):
        async with Client(build_transport(http_definition(modern_http_recorded.url))) as client:
            names = {t.name for t in (await client.list_tools()).tools}
    assert "regional" in names
    assert "bad_header" not in names
    assert "dropping tool 'bad_header': invalid x-mcp-header" in caplog.text
    assert "only permitted on integer/string/boolean" in caplog.text


def _mock(handler: Any) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


async def test_http_401_with_auth_metadata_is_observed() -> None:
    """REQ-AUTH-001 (detection part): a 401 with WWW-Authenticate is recorded on the transport."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer resource_metadata="https://x/.well-known/r"'},
        )

    transport = build_transport(
        http_definition("http://mcp.test/mcp"), http_transport=_mock(handler)
    )
    assert isinstance(transport, ObservedTransport)
    with pytest.raises(Exception):  # noqa: B017, PT011 - the SDK's error type is not the point
        async with Client(transport):
            pass
    assert transport.auth_challenge is True


async def test_http_401_without_metadata_is_not_auth_challenge() -> None:
    """REQ-AUTH-001 (detection part): a bare 401 (no WWW-Authenticate) is a plain failure."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401)

    transport = build_transport(
        http_definition("http://mcp.test/mcp"), http_transport=_mock(handler)
    )
    with pytest.raises(Exception):  # noqa: B017, PT011
        async with Client(transport):
            pass
    assert transport.auth_challenge is False
