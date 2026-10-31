"""Auth fixtures (F4-09a): the fixture AS, OAuth-protected fixture Servers and a "browser".

``oauth_as`` runs ``tests/fixtures/servers/auth_server.py`` in a thread (toggles are
attributes of the yielded ``AuthServerState``). ``protected_modern_http`` /
``protected_legacy_http`` run the HTTP fixture Servers behind ``--bearer-check`` and yield
the MCP endpoint URL. ``browser`` follows an authorization URL to the AS and then to the
loopback callback, like a user's browser would (optionally tampering with the redirect).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator

import httpx2
import pytest

from tests.conftest import MODERN, _free_port, _legacy_command, _serve_http
from tests.fixtures.servers.auth_server import AuthServerState, running_auth_server


@pytest.fixture
def oauth_as() -> Iterator[AuthServerState]:
    with running_auth_server() as state:
        yield state


@pytest.fixture
def protected_modern_http(oauth_as: AuthServerState) -> Iterator[str]:
    port = _free_port()
    cmd = [sys.executable, str(MODERN), "--transport", "http", "--port", str(port)]
    yield from _serve_http([*cmd, "--bearer-check", oauth_as.base], port)


@pytest.fixture
def protected_legacy_http(_legacy_env: None, oauth_as: AuthServerState) -> Iterator[str]:
    port = _free_port()
    cmd = _legacy_command("--transport", "http", "--port", str(port))
    yield from _serve_http([*cmd, "--bearer-check", oauth_as.base], port)


@pytest.fixture(params=["modern", "legacy"])
def protected_http(request: pytest.FixtureRequest) -> str:
    """Both eras: every AUTH test runs on the modern and the legacy protected fixture."""
    name = "protected_modern_http" if request.param == "modern" else "protected_legacy_http"
    url: str = request.getfixturevalue(name)
    return url


class Browser:
    """Follows ``/authorize`` (auto-approved by the fixture AS) to the loopback callback."""

    def __init__(self) -> None:
        self.callbacks: list[httpx2.Response] = []

    async def open(
        self, url: str, *, tamper: Callable[[str], str] | None = None
    ) -> httpx2.Response:
        async with httpx2.AsyncClient(timeout=10) as client:
            approval = await client.get(url)
            if approval.status_code != 302:
                return approval
            location = approval.headers["location"]
            if tamper is not None:
                location = tamper(location)
            response = await client.get(location)
        self.callbacks.append(response)
        return response


@pytest.fixture
def browser() -> Browser:
    return Browser()
