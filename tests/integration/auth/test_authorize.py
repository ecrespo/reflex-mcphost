"""``ConnectionManager.authorize`` and the loopback flow against the fixture AS (F4-07, Flow E).

Every test runs on the modern and the legacy protected fixture (``protected_http``).
"""

from __future__ import annotations

import logging
import socket

import anyio
import pytest

from reflex_mcphost.core.auth.token_store import entry_name
from reflex_mcphost.core.errors import AuthorizationError
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import Browser
from tests.integration.auth.helpers import (
    SERVER,
    TIMEOUT,
    S,
    host_for,
    query,
    replace_param,
    wait_state,
)
from tests.integration.test_host import Setup

pytestmark = pytest.mark.anyio


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


async def test_401_parks_in_auth_required_without_a_listener(
    setup: Setup, oauth_as: AuthServerState, protected_http: str
) -> None:
    """REQ-AUTH-001: a 401 with auth metadata sets auth_required after discovery (PRM →
    AS metadata) — no browser, no loopback listener, no authorization request."""
    async with host_for(setup, protected_http) as host:
        status = await wait_state(host, S.AUTH_REQUIRED)
        assert status.reason == "auth_required"
        assert oauth_as.requests("metadata")  # discovery ran (RFC 9728 → RFC 8414)
        assert oauth_as.requests("authorize") == []
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        assert controller.flow is None


async def test_authorize_completes_and_stores_tokens(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-002, REQ-AUTH-004: authorize() returns the AS URL (PKCE S256, state,
    resource); the browser completes the loopback callback; tokens are stored; ready."""
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        flow = await host.connections.authorize(SERVER)
        assert host.connections.status(SERVER).state is S.CONNECTING
        assert await host.connections.authorize(SERVER) is flow  # same flow while running
        url = flow.authorization_url
        assert url is not None
        assert url.startswith(f"{oauth_as.issuer}/authorize?")
        params = query(url)
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"]
        assert params["state"]
        assert params["resource"] == protected_http
        assert params["redirect_uri"].startswith("http://127.0.0.1:")
        callback = await browser.open(url)
        assert "You can close this tab" in callback.text
        with anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert host.connections.status(SERVER).state is S.READY
        assert flow.authorization_url == url
        entry = await host._secrets.get(entry_name(SERVER))  # type: ignore[union-attr]
        assert entry is not None
        assert entry["tokens"]["access_token"]
        assert entry["issuer"] == oauth_as.issuer
        [token] = oauth_as.requests("token")
        assert {"code", "code_verifier", "resource"} <= set(token["params"])
        port = int(params["redirect_uri"].split(":")[2].split("/")[0])
        assert not _listening(port)  # the listener closed after the one callback


async def test_authorize_outside_auth_required_is_rejected(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """API §4.5: authorize() only in auth_required → AuthorizationError(not_auth_required)."""
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        await browser.open(flow.authorization_url)
        await flow.wait()
        with pytest.raises(AuthorizationError) as err:
            await host.connections.authorize(SERVER)
        assert err.value.reason == "not_auth_required"


def _swap_state(location: str) -> str:
    return replace_param(location, "state", "tampered-state-value")


async def test_forged_callback_does_not_end_the_flow(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-006, REQ-AUTH-010 (login CSRF): callbacks with a foreign ``state`` or no
    parameters (another local page, a stray request) get "Authorization failed" but neither
    end the flow nor redeem the code; the genuine callback then completes it."""
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        redirect_uri = query(flow.authorization_url)["redirect_uri"]
        forged = await browser.open(flow.authorization_url, tamper=_swap_state)
        assert "Authorization failed" in forged.text
        assert (await browser.get(f"{redirect_uri}?code=forged&state=x")).status_code == 400
        assert (
            await browser.get(redirect_uri.replace("/callback", "/favicon.ico"))
        ).status_code == 404
        assert not flow.done
        assert oauth_as.requests("token") == []
        genuine = await browser.get(browser.locations[-1])
        assert "You can close this tab" in genuine.text
        with anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert host.connections.status(SERVER).state is S.READY


@pytest.mark.parametrize(
    ("toggle", "reason"),
    [("iss_override", "iss_mismatch"), ("omit_iss", "iss_mismatch"), ("deny", "access_denied")],
)
async def test_bad_callback_aborts_before_redemption(
    setup: Setup,
    oauth_as: AuthServerState,
    protected_http: str,
    browser: Browser,
    toggle: str,
    reason: str,
) -> None:
    """REQ-AUTH-006, REQ-AUTH-010: a wrong or missing-while-advertised ``iss`` is rejected
    before the code is redeemed (no token request) and, at the deadline, aborts the flow with
    ``iss_mismatch``; an AS error carrying our ``state`` aborts at once (``access_denied``).
    The browser sees "Authorization failed"; the Server stays auth_required."""
    if toggle == "iss_override":
        oauth_as.iss_override = "https://evil.example"
    else:
        setattr(oauth_as, toggle, True)
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        controller.deadline_s = 1.5
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        callback = await browser.open(flow.authorization_url)
        assert "Authorization failed" in callback.text
        with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert err.value.reason == reason
        assert (await wait_state(host, S.AUTH_REQUIRED)).state is S.AUTH_REQUIRED
        assert oauth_as.requests("token") == []


async def test_deadline_times_out(
    setup: Setup, oauth_as: AuthServerState, protected_http: str
) -> None:
    """REQ-AUTH-006: a flow not completed within its deadline aborts with ``timeout``, the
    listener closes and the Server is back in auth_required."""
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        controller.deadline_s = 0.5
        flow = await host.connections.authorize(SERVER)
        port = int(query(flow.authorization_url or "")["redirect_uri"].split(":")[2].split("/")[0])
        with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert err.value.reason == "timeout"
        assert host.connections.status(SERVER).state is S.AUTH_REQUIRED
        assert not _listening(port)
        assert oauth_as.requests("token") == []


async def test_cancel_aborts(setup: Setup, oauth_as: AuthServerState, protected_http: str) -> None:
    """REQ-AUTH-006: cancel() aborts the flow (``cancelled``); nothing is redeemed."""
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        flow = await host.connections.authorize(SERVER)
        await flow.cancel()
        with pytest.raises(AuthorizationError) as err:
            await flow.wait()
        assert err.value.reason == "cancelled"
        assert (await wait_state(host, S.AUTH_REQUIRED)).state is S.AUTH_REQUIRED
        assert oauth_as.requests("token") == []


async def test_no_secret_in_logs_errors_or_status(
    setup: Setup,
    oauth_as: AuthServerState,
    protected_http: str,
    browser: Browser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-AUTH-008: codes, state, verifiers and tokens of a mismatching and of a completed
    flow appear in no log record (incl. ``mcp.client.auth``), no AuthorizationError text and
    no ServerStatus."""
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        controller.deadline_s = 1.0
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        state = query(flow.authorization_url)["state"]
        await browser.open(flow.authorization_url, tamper=_swap_state)
        with pytest.raises(AuthorizationError) as err:
            await flow.wait()
        texts += [str(err.value), repr(err.value), repr(flow)]
        await wait_state(host, S.AUTH_REQUIRED)
        controller.deadline_s = 300
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        await browser.open(flow.authorization_url)
        await flow.wait()
        texts.append(repr(host.connections.status(SERVER)))
    seeded = [*oauth_as.seen_secrets, state, "tampered-state-value"]
    ours = [
        r.getMessage() + (r.exc_text or "")
        for r in caplog.records
        if not r.name.startswith(("httpx2", "httpcore2"))  # the test's browser client
    ]
    for value in seeded:
        for text in [*ours, *texts]:
            assert value not in text
