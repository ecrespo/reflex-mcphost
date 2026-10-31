"""OAuth era matrix (F4-09b): {modern, legacy} Streamable HTTP x {CIMD, DCR, configured}
full flows, plus mix-up (``iss``), issuer switch and refresh, against the fixture AS.

The legacy cells are marked ``interop`` (they run the mcp 1.x fixture Server).
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from reflex_mcphost.core.config.models import DEFAULT_OAUTH_CLIENT_METADATA_URL
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.errors import AuthorizationError
from reflex_mcphost.core.types import Consumer
from tests.fixtures.servers.auth_server import TENANT2, AuthServerState
from tests.integration.auth.conftest import (  # noqa: F401 - fixtures used by this module
    Browser,
    browser,
    oauth_as,
    protected_legacy_http,
    protected_modern_http,
)
from tests.integration.auth.helpers import (
    SERVER,
    TIMEOUT,
    S,
    host_for,
    query,
    sign_in,
    wait_state,
)
from tests.integration.test_host import Setup, setup  # noqa: F401 - fixture
from tests.integration.test_invoker import wait_tools

pytestmark = pytest.mark.anyio
CONFIGURED_ID = "configured-client-interop"
BOT = Consumer.agent("bot")


@pytest.fixture(params=["modern", "legacy"])
def era(request: pytest.FixtureRequest) -> str:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
    return str(request.param)


@pytest.fixture
def protected(era: str, request: pytest.FixtureRequest) -> str:
    """The OAuth-protected fixture Server of ``era`` behind the fixture AS."""
    url: str = request.getfixturevalue(f"protected_{era}_http")
    return url


def _registration(auth: AuthServerState, kind: str) -> dict[str, Any]:
    """Prepare the AS for ``kind``; the extra ``oauth`` config keys it needs."""
    if kind == "dcr":
        auth.cimd = False
        return {}
    if kind == "configured":
        auth.preregister(CONFIGURED_ID)
        return {"client_id": CONFIGURED_ID}
    return {}


@pytest.mark.parametrize("kind", ["cimd", "dcr", "configured"])
async def test_full_flow_on_both_eras(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    protected: str,
    era: str,
    kind: str,
) -> None:
    """REQ-AUTH-001, REQ-AUTH-002, REQ-AUTH-003, REQ-AUTH-004: on each era, a 401 parks the
    Server in auth_required; the authorization (PKCE S256, ``state``, ``resource``) with a
    CIMD / DCR / configured client id completes over the loopback callback; tokens are
    stored and a tool call through the Invoker succeeds on the authorized session."""
    extra = _registration(oauth_as, kind)
    async with host_for(setup, protected, **extra) as host:
        await sign_in(host, browser)
        assert host.connections.status(SERVER).era == era
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        assert controller.store.client_id_source == kind
        await wait_tools(host, SERVER)
        await host.consent.grant(
            effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server(SERVER)
        )
        result = await host.invoker.call(f"{SERVER}::echo", {"text": "hi"}, consumer=BOT)
        assert result.ok, result.error
        assert result.text == "hi"
    [authorize] = oauth_as.requests("authorize")
    expected_id = {"cimd": DEFAULT_OAUTH_CLIENT_METADATA_URL, "configured": CONFIGURED_ID}
    if kind == "dcr":
        assert authorize["client_id"].startswith("dcr-")
    else:
        assert authorize["client_id"] == expected_id[kind]
    assert len(oauth_as.requests("register")) == (1 if kind == "dcr" else 0)
    [token] = oauth_as.requests("token")
    assert {"code", "code_verifier", "resource"} <= set(token["params"])
    assert authorize["redirect_uri"].startswith("http://127.0.0.1:")


async def test_mix_up_is_rejected_on_both_eras(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    protected: str,
) -> None:
    """REQ-AUTH-010, REQ-AUTH-006: an authorization response whose ``iss`` names another
    issuer (mix-up) is rejected before the code is redeemed; the flow aborts with
    ``iss_mismatch`` and the Server stays auth_required."""
    oauth_as.iss_override = "https://evil.example"
    async with host_for(setup, protected) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        controller.deadline_s = 1.0
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        assert "Authorization failed" in (await browser.open(flow.authorization_url)).text
        with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert err.value.reason == "iss_mismatch"
        assert (await wait_state(host, S.AUTH_REQUIRED)).state is S.AUTH_REQUIRED
    assert oauth_as.requests("token") == []


async def test_issuer_switch_on_both_eras(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    protected: str,
) -> None:
    """REQ-AUTH-011: after the Server's AS switches issuer, the old issuer's tokens and DCR
    client are never presented; the Host re-registers with the new issuer and a new
    authorization there succeeds."""
    oauth_as.cimd = False  # DCR: the issuer-bound registration is the demanding case
    async with host_for(setup, protected) as host:
        await sign_in(host, browser)
    oauth_as.switch_issuer(TENANT2)
    oauth_as.journal.clear()
    async with host_for(setup, protected) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        assert oauth_as.requests("introspect") == []
        assert oauth_as.requests("token") == []
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        assert flow.authorization_url.startswith(f"{oauth_as.issuer}/authorize?")
        await browser.open(flow.authorization_url)
        with anyio.fail_after(TIMEOUT):
            await flow.wait()
        await wait_state(host, S.READY)
    assert len(oauth_as.requests("register", oauth_as.issuer)) == 1
    assert {r["issuer"] for r in oauth_as.requests("token")} == {oauth_as.issuer}
    assert query(flow.authorization_url)["client_id"].startswith("dcr-")


async def test_refresh_on_both_eras(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    protected: str,
) -> None:
    """REQ-AUTH-005: an expired stored token is refreshed once on restart, and a token
    rejected mid-session is refreshed once and the request replayed — no new authorization
    on either era."""
    async with host_for(setup, protected) as host:
        await sign_in(host, browser)
    oauth_as.expire_access_tokens()
    oauth_as.journal.clear()
    async with host_for(setup, protected) as host:
        await wait_state(host, S.READY)
        oauth_as.revoke_access_tokens()
        client = host.connections._live_client(SERVER)
        assert client is not None
        with anyio.fail_after(TIMEOUT):
            assert (await client.list_tools()).tools
        assert host.connections.status(SERVER).state is S.READY
    refreshes = [r for r in oauth_as.requests("token") if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 2  # one on restart (expired), one mid-session (rejected)
    assert oauth_as.requests("authorize") == []
