"""Self-tests of the fixture OAuth AS and the protected fixture Servers (F4-09a).

Test infrastructure (cites no REQ by design, Tasks F4-09a): these prove the fixtures behave
like the RFCs the Host relies on, so the AUTH tests built on them mean something.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest

from reflex_mcphost.core.config.models import DEFAULT_OAUTH_CLIENT_METADATA_URL
from tests.fixtures.servers.auth_server import TENANT2, AuthServerState

pytestmark = pytest.mark.anyio
REDIRECT = "http://127.0.0.1:50999/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _register(client: httpx2.AsyncClient, state: AuthServerState) -> str:
    response = await client.post(
        f"{state.issuer}/register",
        json={"redirect_uris": [REDIRECT], "application_type": "native"},
    )
    assert response.status_code == 201
    client_id: str = response.json()["client_id"]
    return client_id


async def _authorize(
    client: httpx2.AsyncClient,
    state: AuthServerState,
    client_id: str,
    challenge: str,
    redirect: str = REDIRECT,
) -> httpx2.Response:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "state": "st-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return await client.get(f"{state.issuer}/authorize?{urlencode(query)}")


def _query(response: httpx2.Response) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


async def test_metadata_advertises_toggles(oauth_as: AuthServerState) -> None:
    """F4-09a: RFC 8414 metadata reflects the cimd / dcr / iss toggles, per issuer."""
    async with httpx2.AsyncClient() as client:
        meta = (await client.get(f"{oauth_as.base}/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"] == oauth_as.base
        assert meta["client_id_metadata_document_supported"] is True
        assert meta["registration_endpoint"] == f"{oauth_as.base}/register"
        oauth_as.dcr = oauth_as.cimd = oauth_as.iss_supported = False
        url = f"{oauth_as.base}/.well-known/oauth-authorization-server{TENANT2}"
        meta = (await client.get(url)).json()
    assert meta["issuer"] == oauth_as.base + TENANT2
    assert "registration_endpoint" not in meta
    assert meta["client_id_metadata_document_supported"] is False
    assert meta["authorization_response_iss_parameter_supported"] is False


async def test_wrong_pkce_verifier_is_rejected(oauth_as: AuthServerState) -> None:
    """F4-09a: the token endpoint verifies PKCE S256; a wrong verifier gets invalid_grant
    and the right one (on a fresh code) gets tokens."""
    verifier, challenge = _pkce()
    async with httpx2.AsyncClient() as client:
        client_id = await _register(client, oauth_as)
        for attempt_verifier, status in (("wrong-" + verifier, 400), (verifier, 200)):
            code = _query(await _authorize(client, oauth_as, client_id, challenge))["code"]
            response = await client.post(
                f"{oauth_as.issuer}/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "client_id": client_id,
                    "code_verifier": attempt_verifier,
                },
            )
            assert response.status_code == status
    body = response.json()
    assert oauth_as.active(body["access_token"])
    assert verifier in oauth_as.seen_secrets


async def test_iss_is_emitted_when_enabled(oauth_as: AuthServerState) -> None:
    """F4-09a (RFC 9207): ``iss`` in the redirect when supported, absent when not, and the
    mix-up injection value when overridden."""
    _, challenge = _pkce()
    async with httpx2.AsyncClient() as client:
        client_id = await _register(client, oauth_as)
        assert _query(await _authorize(client, oauth_as, client_id, challenge))["iss"] == (
            oauth_as.issuer
        )
        oauth_as.iss_override = "https://evil.example"
        assert _query(await _authorize(client, oauth_as, client_id, challenge))["iss"] == (
            "https://evil.example"
        )
        oauth_as.iss_override, oauth_as.iss_supported = None, False
        assert "iss" not in _query(await _authorize(client, oauth_as, client_id, challenge))


async def test_cimd_client_needs_a_loopback_redirect(oauth_as: AuthServerState) -> None:
    """F4-09a: the CIMD client (resolved from docs/oauth/client.json) is accepted only with
    a loopback redirect; any port only while port variance is on; never with CIMD off."""
    _, challenge = _pkce()
    cimd = DEFAULT_OAUTH_CLIENT_METADATA_URL
    async with httpx2.AsyncClient() as client:
        assert (await _authorize(client, oauth_as, cimd, challenge)).status_code == 302
        evil = "http://evil.example/callback"
        assert (await _authorize(client, oauth_as, cimd, challenge, evil)).status_code == 400
        oauth_as.port_variance = False
        assert (await _authorize(client, oauth_as, cimd, challenge)).status_code == 400
        portless = "http://127.0.0.1/callback"
        assert (await _authorize(client, oauth_as, cimd, challenge, portless)).status_code == 302
        oauth_as.cimd = False
        assert (await _authorize(client, oauth_as, cimd, challenge, portless)).status_code == 400


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "fixture-test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream"}


async def _token_for(client: httpx2.AsyncClient, state: AuthServerState) -> str:
    verifier, challenge = _pkce()
    client_id = await _register(client, state)
    code = _query(await _authorize(client, state, client_id, challenge))["code"]
    response = await client.post(
        f"{state.issuer}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    token: str = response.json()["access_token"]
    return token


async def test_protected_fixture_requires_a_live_token(
    oauth_as: AuthServerState, protected_http: str
) -> None:
    """F4-09a (RFC 9728, both eras): without a token → 401 naming resource_metadata, whose
    document points at the AS's current issuer; with a live token → 200."""
    async with httpx2.AsyncClient(timeout=10) as client:
        denied = await client.post(protected_http, json=INIT, headers=HEADERS)
        assert denied.status_code == 401
        challenge = denied.headers["www-authenticate"]
        assert 'resource_metadata="' in challenge
        prm_url = challenge.split('resource_metadata="')[1].rstrip('"')
        prm = (await client.get(prm_url)).json()
        assert prm == {
            "resource": protected_http,
            "authorization_servers": [oauth_as.issuer],
            "bearer_methods_supported": ["header"],
        }
        token = await _token_for(client, oauth_as)
        auth = {**HEADERS, "Authorization": f"Bearer {token}"}
        allowed = await client.post(protected_http, json=INIT, headers=auth)
        assert allowed.status_code == 200
        oauth_as.revoke_access_tokens()
        assert (await client.post(protected_http, json=INIT, headers=auth)).status_code == 401
