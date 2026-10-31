"""``LoopbackCallbackServer``: the per-flow OAuth redirect listener (F4-07, DD-009, API §2.2)."""

from __future__ import annotations

import logging
import socket

import anyio
import httpx2
import pytest

from reflex_mcphost.core.auth.callback_server import (
    FAILURE_PAGE,
    SUCCESS_PAGE,
    CallbackResult,
    LoopbackCallbackServer,
)

pytestmark = pytest.mark.anyio
CODE = "code-secret-callback-0001"
STATE = "state-secret-callback-0002"


async def _get(url: str) -> httpx2.Response:
    async with httpx2.AsyncClient(timeout=5) as client:
        return await client.get(url)


async def test_binds_loopback_only_and_serves_one_callback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-AUTH-002, REQ-AUTH-008: bound to 127.0.0.1 on an ephemeral port; one callback is
    accepted (static page, no-store, no-referrer, no echo), then the listener is closed;
    neither the target nor the query string is logged."""
    caplog.set_level(logging.DEBUG)
    async with LoopbackCallbackServer() as server:
        assert server.host == "127.0.0.1"
        assert server.port > 0
        assert server.redirect_uri == f"http://127.0.0.1:{server.port}/callback"
        response = await _get(f"{server.redirect_uri}?code={CODE}&state={STATE}&iss=https://as")
        assert response.status_code == 200
        assert response.text == SUCCESS_PAGE
        assert CODE not in response.text
        assert STATE not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        with anyio.fail_after(5):
            result = await server.result()
        assert result == CallbackResult(code=CODE, state=STATE, iss="https://as", error=None)
        with anyio.fail_after(5):
            await server.closed.wait()
        with pytest.raises(httpx2.ConnectError):
            await _get(server.redirect_uri)
    ours = "\n".join(  # the test's own httpx2 client logs its URL; the Host must not
        r.getMessage() for r in caplog.records if r.name.startswith("reflex_mcphost")
    )
    assert "authorization callback received" in ours
    assert CODE not in ours
    assert STATE not in ours
    assert "callback?" not in ours


async def test_not_reachable_on_other_interfaces() -> None:
    """REQ-AUTH-002, DD-009: the socket is bound to 127.0.0.1 only (never 0.0.0.0/::)."""
    async with LoopbackCallbackServer() as server:
        assert server.bound_address == ("127.0.0.1", server.port)
        with socket.socket() as probe:  # the same port is still free on another address
            try:
                probe.bind(("127.0.0.2", server.port))
            except OSError:  # pragma: no cover - 127.0.0.2 not routable on this OS
                pytest.skip("127.0.0.2 not available")


async def test_failure_page_for_as_error_or_validator() -> None:
    """REQ-AUTH-006: an AS ``error`` or a validator rejection answers "Authorization failed"
    (the result still records it for the flow to abort)."""
    async with LoopbackCallbackServer() as server:
        response = await _get(f"{server.redirect_uri}?error=access_denied&state=s")
        assert response.text == FAILURE_PAGE
        assert (await server.result()).error == "access_denied"
    async with LoopbackCallbackServer() as server:
        server.validator = lambda result: result.state == "expected"
        response = await _get(f"{server.redirect_uri}?code=c&state=other")
        assert response.text == FAILURE_PAGE
        assert (await server.result()).state == "other"


async def test_wrong_path_and_method() -> None:
    """DD-009: only ``GET /callback`` — other paths 404, other methods 405; neither ends the
    flow."""
    async with LoopbackCallbackServer() as server:
        base = server.redirect_uri.removesuffix("/callback")
        assert (await _get(f"{base}/favicon.ico")).status_code == 404
        async with httpx2.AsyncClient(timeout=5) as client:
            assert (await client.post(server.redirect_uri)).status_code == 405
        assert not server.closed.is_set()
        assert (await _get(f"{server.redirect_uri}?code=c&state=s")).status_code == 200


async def test_oversized_request_head_is_rejected() -> None:
    """DD-009: a request head over 8 KiB is refused (431) and does not end the flow."""
    async with LoopbackCallbackServer() as server:
        response = await _get(f"{server.redirect_uri}?pad={'x' * 9000}")
        assert response.status_code == 431
        assert not server.closed.is_set()


async def test_garbage_and_idle_connections_are_dropped() -> None:
    """DD-009: a malformed request gets 400; a silent connection is closed after the
    per-connection read limit; neither ends the flow."""
    async with LoopbackCallbackServer(read_timeout_s=0.2) as server:
        stream = await anyio.connect_tcp("127.0.0.1", server.port)
        async with stream:
            await stream.send(b"NOT HTTP\r\n\r\n")
            data = await stream.receive()
            assert data.startswith(b"HTTP/1.1 400")
        idle = await anyio.connect_tcp("127.0.0.1", server.port)
        async with idle:
            with anyio.fail_after(5):
                ended = False
                while not ended:
                    try:
                        await idle.receive()
                    except (anyio.EndOfStream, anyio.BrokenResourceError):
                        ended = True
        assert not server.closed.is_set()


async def test_close_without_callback() -> None:
    """REQ-AUTH-006: leaving the context (deadline / cancel) closes the listener."""
    async with LoopbackCallbackServer() as server:
        port = server.port
    assert server.closed.is_set()
    with pytest.raises(httpx2.ConnectError):
        await _get(f"http://127.0.0.1:{port}/callback")


async def test_preferred_port_falls_back_to_ephemeral() -> None:
    """Flow E step 2: a preferred port is used when free, else an ephemeral one."""
    async with LoopbackCallbackServer() as first, LoopbackCallbackServer(port=first.port) as second:
        assert second.port != first.port
    async with LoopbackCallbackServer() as probe:
        free = probe.port
    async with LoopbackCallbackServer(port=free) as reused:
        assert reused.port == free
