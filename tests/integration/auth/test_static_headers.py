"""Static headers disable OAuth (REQ-AUTH-009, delta D6; F4-07 "no provider" part)."""

from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from pydantic import SecretStr

from reflex_mcphost.core.config.models import HttpTransport, ServerDefinition
from reflex_mcphost.core.connection.transports import build_transport
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.helpers import SERVER, S, wait_state
from tests.integration.test_host import Setup

pytestmark = pytest.mark.anyio
API_KEY = "header-secret-static-0001"


async def test_401_with_static_headers_is_http_unauthorized() -> None:
    """REQ-AUTH-009, D6: a 401 on a Server with static headers is a permanent failure
    (``fatal_reason = http_unauthorized``), never an auth challenge."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="x"'})

    definition = ServerDefinition(
        id=SERVER,
        transport=HttpTransport(
            url="http://mcp.test/mcp",  # type: ignore[arg-type]
            headers={"X-Api-Key": SecretStr(API_KEY)},
        ),
    )
    transport = build_transport(definition, http_transport=httpx2.MockTransport(handler))
    with pytest.raises(Exception):  # noqa: B017, PT011 - the SDK's error type is not the point
        async with Client(transport):
            pass
    assert transport.fatal_reason == "http_unauthorized"
    assert transport.auth_challenge is False


async def test_static_header_server_never_contacts_the_as(
    setup: Setup, oauth_as: AuthServerState, protected_modern_http: str
) -> None:
    """REQ-AUTH-009: the Host sends the static header, starts no OAuth (no controller, no
    AS request) and parks the Server in ``failed`` with reason ``http_unauthorized``."""
    table = "\n".join(
        [
            'transport = "http"',
            f"url = {json.dumps(protected_modern_http)}",
            f'headers = {{ X-Api-Key = "{API_KEY}" }}',
        ]
    )
    host = setup.host(setup.config({SERVER: table}))
    async with host:
        status = await wait_state(host, S.FAILED)
        assert status.reason == "http_unauthorized"
        assert host.connections.oauth_controller(SERVER) is None
    assert oauth_as.journal == []
