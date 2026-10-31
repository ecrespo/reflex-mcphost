"""Stored-token reuse and refresh-once against the fixture AS (F4-08, Flow E, REQ-AUTH-005).

Every test runs on the modern and the legacy protected fixture (``protected_http``).
"""

from __future__ import annotations

import anyio
import pytest

from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import Browser
from tests.integration.auth.helpers import SERVER, TIMEOUT, S, host_for, sign_in, wait_state
from tests.integration.test_host import Setup

pytestmark = pytest.mark.anyio


def _refreshes(oauth_as: AuthServerState) -> list[dict[str, object]]:
    return [r for r in oauth_as.requests("token") if r["grant_type"] == "refresh_token"]


async def test_restart_reuses_tokens_only_after_discovery(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-004, REQ-AUTH-011: after a restart the stored tokens connect the Server
    with no prompt — but no bearer token is presented before discovery (PRM → AS metadata)
    has re-established the issuer binding."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
    oauth_as.journal.clear()
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.READY)
    endpoints = [r["endpoint"] for r in oauth_as.journal]
    assert "authorize" not in endpoints
    assert endpoints.index("metadata") < endpoints.index("introspect")
    assert all(r["active"] for r in oauth_as.requests("introspect"))


async def test_expired_token_refreshes_once_without_prompt(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-005: an expired stored access token is refreshed exactly once — at the
    discovered AS token endpoint — and the Server is ready without a new authorization."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
    oauth_as.expire_access_tokens()
    oauth_as.journal.clear()
    async with host_for(setup, protected_http) as host:
        await wait_state(host, S.READY)
    [refresh] = _refreshes(oauth_as)
    assert refresh["issuer"] == oauth_as.issuer
    assert oauth_as.requests("authorize") == []


async def test_rejected_token_refreshes_once_while_ready(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-005: a 401 on a live session's request with an unexpired, refreshable token
    replays the request once after exactly one refresh; the request succeeds, no prompt."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        oauth_as.revoke_access_tokens()
        oauth_as.journal.clear()
        client = host.connections._live_client(SERVER)
        assert client is not None
        with anyio.fail_after(TIMEOUT):
            result = await client.list_tools()
        assert result.tools
        assert len(_refreshes(oauth_as)) == 1
        assert host.connections.status(SERVER).state is S.READY
        assert oauth_as.requests("authorize") == []


async def test_refresh_rejected_parks_in_auth_required(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-005: when the one refresh is rejected too, the 401 on the live session
    closes it and the supervisor lands in auth_required (no browser, no authorize)."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        oauth_as.revoke_access_tokens()
        oauth_as.revoke_refresh_tokens()
        oauth_as.journal.clear()
        client = host.connections._live_client(SERVER)
        assert client is not None
        with pytest.raises(Exception):  # noqa: B017, PT011 - the SDK's error type is not the point
            await client.list_tools()
        status = await wait_state(host, S.AUTH_REQUIRED)
        assert status.reason == "auth_required"
        assert len(_refreshes(oauth_as)) == 1  # the dead tokens are dropped, never retried
        assert oauth_as.requests("authorize") == []
