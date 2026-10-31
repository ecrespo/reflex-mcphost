"""Client registration order and issuer binding against the fixture AS (F4-08, REQ-AUTH-003,
REQ-AUTH-011).

Every test runs on the modern and the legacy protected fixture (``protected_http``).
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from reflex_mcphost.core.config.models import DEFAULT_OAUTH_CLIENT_METADATA_URL
from reflex_mcphost.core.errors import AuthorizationError
from tests.fixtures.servers.auth_server import TENANT2, AuthServerState
from tests.integration.auth.conftest import Browser
from tests.integration.auth.helpers import (
    SERVER,
    TIMEOUT,
    S,
    host_for,
    query,
    sign_in,
    wait_state,
)
from tests.integration.test_host import Setup

pytestmark = pytest.mark.anyio
CONFIGURED_ID = "configured-client-1"


def _client_ids(oauth_as: AuthServerState, issuer: str) -> set[str]:
    return {r["client_id"] for r in oauth_as.journal if r["issuer"] == issuer and r["client_id"]}


async def test_cimd_as_uses_the_metadata_document_url(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-003: an AS advertising CIMD gets the CIMD URL as ``client_id``; no
    ``/register`` call; the stored source is ``cimd``."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        assert controller.store.client_id_source == "cimd"
    [authorize] = oauth_as.requests("authorize")
    assert authorize["client_id"] == DEFAULT_OAUTH_CLIENT_METADATA_URL
    assert oauth_as.requests("register") == []


async def test_non_cimd_as_falls_back_to_dcr(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-003: without CIMD support the Host registers dynamically (deprecated,
    compatibility) as a native app; source ``dcr``; the authorization uses the registered
    loopback redirect (its port re-bound)."""
    oauth_as.cimd = False
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        assert controller.store.client_id_source == "dcr"
    [register] = oauth_as.requests("register")
    assert register["application_type"] == "native"
    [authorize] = oauth_as.requests("authorize")
    assert authorize["client_id"].startswith("dcr-")
    assert authorize["redirect_uri"] in register["redirect_uris"]


async def test_neither_cimd_nor_dcr_requires_a_client_id(
    setup: Setup, oauth_as: AuthServerState, protected_http: str
) -> None:
    """REQ-AUTH-003, API §3.4: with neither CIMD nor DCR the Server parks in auth_required
    with reason ``client_id_required``, and ``authorize`` fails with that reason and the
    configuration hint; no authorization request is made."""
    oauth_as.cimd = oauth_as.dcr = False
    async with host_for(setup, protected_http) as host:
        status = await wait_state(host, S.AUTH_REQUIRED)
        assert status.reason == "client_id_required"
        flow = await host.connections.authorize(SERVER)
        with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
            await flow.wait()
        assert err.value.reason == "client_id_required"
        assert f"[servers.{SERVER}.oauth] client_id" in str(err.value)
        assert (await wait_state(host, S.AUTH_REQUIRED)).reason == "client_id_required"
    assert oauth_as.requests("authorize") == []


async def test_configured_client_id_skips_cimd_and_dcr(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-003: a configured ``client_id`` is used as is — neither the CIMD URL nor a
    DCR registration (even though the AS supports both)."""
    oauth_as.preregister(CONFIGURED_ID)
    async with host_for(setup, protected_http, client_id=CONFIGURED_ID) as host:
        await sign_in(host, browser)
        controller = host.connections.oauth_controller(SERVER)
        assert controller is not None
        assert controller.store.client_id_source == "configured"
    assert _client_ids(oauth_as, oauth_as.issuer) == {CONFIGURED_ID}
    assert oauth_as.requests("register") == []


@pytest.mark.parametrize("kind", ["cimd", "dcr", "configured"])
async def test_issuer_switch_never_reuses_old_credentials(
    setup: Setup,
    oauth_as: AuthServerState,
    protected_http: str,
    browser: Browser,
    kind: str,
) -> None:
    """REQ-AUTH-011 (mix-up, SEP-2352): after the Server's AS switches issuer, tokens of
    the old issuer are never presented (to the Server or to the new token endpoint), a DCR
    client re-registers with the new issuer and its old ``client_id`` never reaches it; the
    Server parks in auth_required and a new authorization at the new issuer succeeds."""
    kwargs: dict[str, Any] = {}
    if kind == "dcr":
        oauth_as.cimd = False
    elif kind == "configured":
        oauth_as.preregister(CONFIGURED_ID)
        kwargs["client_id"] = CONFIGURED_ID
    old_issuer = oauth_as.issuer
    async with host_for(setup, protected_http, **kwargs) as host:
        await sign_in(host, browser)
    old_ids = _client_ids(oauth_as, old_issuer)
    oauth_as.switch_issuer(TENANT2)
    new_issuer = oauth_as.issuer
    assert new_issuer != old_issuer
    oauth_as.journal.clear()
    async with host_for(setup, protected_http, **kwargs) as host:
        await wait_state(host, S.AUTH_REQUIRED)
        assert oauth_as.requests("introspect") == []  # no old bearer reached the Server
        assert oauth_as.requests("token") == []  # nor the new token endpoint
        flow = await host.connections.authorize(SERVER)
        assert flow.authorization_url is not None
        assert flow.authorization_url.startswith(f"{new_issuer}/authorize?")
        await browser.open(flow.authorization_url)
        with anyio.fail_after(TIMEOUT):
            await flow.wait()
        await wait_state(host, S.READY)
    assert {r["issuer"] for r in oauth_as.requests("token")} == {new_issuer}
    new_ids = _client_ids(oauth_as, new_issuer)
    if kind == "dcr":
        assert len(oauth_as.requests("register", new_issuer)) == 1
        assert not old_ids & new_ids
    else:
        assert new_ids == old_ids  # portable client ids (CIMD URL / configured)
    redirect = query(flow.authorization_url)["redirect_uri"]
    assert redirect.startswith("http://127.0.0.1:")
