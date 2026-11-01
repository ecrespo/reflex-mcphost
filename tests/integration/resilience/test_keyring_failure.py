"""Keyring backend failing at runtime (F8-03; REQ-NFR-020, REQ-CON-011, DD-008).

The keyring works while the Host starts (the HMAC install key is read from it) and then
raises ``KeyringError`` on ``get`` / ``set`` / ``delete`` of the OAuth entries — a locked or
crashed Secret Service, an expired keychain session. The Host never crashes: the OAuth
Server lands in ``auth_required`` (or ``disconnected`` after a sign-out), no stored or
stale token is presented, another Server keeps serving calls, authorization works again
once the keyring is back, and every failure is logged once at WARNING with the exception
type only (never the backend's message).

The SDK provider keeps tokens in memory for the life of a Host, so a reconnect does not
read the keyring: the ``get`` case starts a second Host on the same keyring.
"""

from __future__ import annotations

import logging
import traceback

import anyio
import pytest
from keyring.errors import KeyringError

from reflex_mcphost.core.consent.models import ConsentDecision
from reflex_mcphost.core.errors import AuthorizationError, SecretStoreError
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import actions
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import (  # noqa: F401 - fixtures used by this module
    Browser,
    browser,
    oauth_as,
    protected_modern_http,
)
from tests.integration.auth.helpers import (
    SERVER,
    TIMEOUT,
    S,
    clear_journal,
    http_table,
    sign_in,
    wait_state,
)
from tests.integration.handlers.helpers import HandlerPrompter, modern_stdio_table
from tests.integration.test_host import Setup, setup  # noqa: F401 - fixture
from tests.integration.test_invoker import wait_tools
from tests.unit.core.test_secret_store import MemoryKeyring

pytestmark = pytest.mark.anyio
LOCAL = "local"
BOT = Consumer.agent("bot")
BACKEND_TEXT = "backend-locked-detail"
"""Text of the backend's exception: never in a log record."""


class FlakyKeyring(MemoryKeyring):
    """A keyring whose ``get`` / ``set`` / ``delete`` of ``oauth:*`` entries raise while the
    operation is named in ``broken`` (the HMAC key entry keeps working: it is read once,
    at start, which succeeded)."""

    def __init__(self) -> None:
        super().__init__()
        self.broken: set[str] = set()
        self.failures: dict[str, int] = {}

    def _check(self, kind: str, username: str) -> None:
        if kind in self.broken and username.startswith("oauth:"):
            self.failures[kind] = self.failures.get(kind, 0) + 1
            raise KeyringError(BACKEND_TEXT)

    def get_password(self, service: str, username: str) -> str | None:
        self._check("get", username)
        return super().get_password(service, username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._check("set", username)
        super().set_password(service, username, password)

    def delete_password(self, service: str, username: str) -> None:
        self._check("delete", username)
        super().delete_password(service, username)


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="reflex_mcphost")
    return caplog


def _host(setup: Setup, protected: str, keyring: FlakyKeyring) -> Host:  # noqa: F811
    config = setup.config({SERVER: http_table(protected), LOCAL: modern_stdio_table()})
    return Host(
        config,
        platform=setup.files.platform,
        env=setup.env,
        keyring_backend=keyring,
        prompter=HandlerPrompter([], decision=ConsentDecision.ALLOW_ONCE),
    )


async def _local_serves(host: Host) -> None:
    await wait_tools(host, LOCAL)
    result = await host.invoker.call(f"{LOCAL}::echo", {"text": "alive"}, consumer=BOT)
    assert result.ok, result.error
    assert result.text == "alive"


def _check_logs(caplog: pytest.LogCaptureFixture, keyring: FlakyKeyring, kind: str) -> None:
    """At most one WARNING per failed keyring operation (at least one), type name only."""
    found = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name.startswith("reflex_mcphost")
        and "KeyringError" in r.getMessage()
    ]
    assert keyring.failures.get(kind, 0) >= 1
    assert 1 <= len(found) <= sum(keyring.failures.values()), found
    assert all(BACKEND_TEXT not in r.getMessage() for r in caplog.records)
    assert all(BACKEND_TEXT not in (r.exc_text or "") for r in caplog.records)


async def test_keyring_get_failure_lands_in_auth_required(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected_modern_http: str,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    records: pytest.LogCaptureFixture,
) -> None:
    """REQ-NFR-020, REQ-CON-011: stored tokens that cannot be read (keyring ``get``
    raising) leave the OAuth Server in ``auth_required`` with no bearer request; the other
    Server serves calls; once the keyring is back, authorization works again."""
    keyring = FlakyKeyring()
    with anyio.fail_after(TIMEOUT * 4):
        async with _host(setup, protected_modern_http, keyring) as host:
            await sign_in(host, browser)
        await clear_journal(oauth_as)
        keyring.broken.add("get")
        async with _host(setup, protected_modern_http, keyring) as host:
            status = await wait_state(host, S.AUTH_REQUIRED)
            assert status.reason == "auth_required"
            await _local_serves(host)
            assert oauth_as.bearer_requests() == []
            keyring.broken.clear()
            await sign_in(host, browser)
            await _local_serves(host)
    _check_logs(records, keyring, "get")


async def test_keyring_set_failure_fails_the_flow_not_the_host(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected_modern_http: str,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    records: pytest.LogCaptureFixture,
) -> None:
    """REQ-NFR-020, REQ-CON-011: credentials that cannot be stored (keyring ``set``
    raising) fail that authorization with ``AuthorizationError`` (reason code only); the
    OAuth Server is back in ``auth_required``, the other Server serves calls, and the next
    authorization succeeds once the keyring is back (from the tokens the SDK kept in memory,
    or through the browser)."""
    keyring = FlakyKeyring()
    keyring.broken.add("set")
    with anyio.fail_after(TIMEOUT * 4):
        async with _host(setup, protected_modern_http, keyring) as host:
            await wait_state(host, S.AUTH_REQUIRED)
            flow = await host.connections.authorize(SERVER)
            assert flow.authorization_url is not None
            await browser.open(flow.authorization_url)
            with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
                await flow.wait()
            assert BACKEND_TEXT not in str(err.value)
            await wait_state(host, S.AUTH_REQUIRED)
            await _local_serves(host)
            keyring.broken.clear()
            # The SDK kept the tokens it obtained in memory (only storing them failed): the
            # next authorization may complete without the browser; either way it succeeds.
            flow = await host.connections.authorize(SERVER)
            if flow.authorization_url is not None:
                await browser.open(flow.authorization_url)
            with anyio.fail_after(TIMEOUT):
                await flow.wait()
            await wait_state(host, S.READY)
            await _local_serves(host)
    _check_logs(records, keyring, "set")


async def test_keyring_delete_failure_signs_out_fail_closed(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected_modern_http: str,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    records: pytest.LogCaptureFixture,
) -> None:
    """REQ-NFR-020, REQ-AUTH-007, Art. 5: a sign-out whose keyring ``delete`` raises still
    disconnects the Server and forgets the in-memory credentials; the caller gets a
    ``SecretStoreError`` with a fixed message (no backend text in it, its traceback or the
    logs); the next ``connect`` presents no token and lands in ``auth_required``; the
    other Server serves calls; the Host closes cleanly."""
    keyring = FlakyKeyring()
    with anyio.fail_after(TIMEOUT * 4):
        async with _host(setup, protected_modern_http, keyring) as host:
            await sign_in(host, browser)
            keyring.broken.add("delete")
            with pytest.raises(SecretStoreError) as raised:
                await host.connections.sign_out(SERVER)
            assert (raised.value.server_id, raised.value.operation) == (SERVER, "delete")
            shown = "".join(traceback.format_exception(raised.value))
            assert BACKEND_TEXT not in shown
            assert "KeyringError" not in shown
            keyring.broken.add("get")  # the entry that survived stays unreadable
            assert host.connections.status(SERVER).state is S.DISCONNECTED
            await clear_journal(oauth_as)
            await host.connections.connect(SERVER)
            status = await wait_state(host, S.AUTH_REQUIRED, S.READY)
            assert status.state is S.AUTH_REQUIRED  # no stale in-memory token
            await _local_serves(host)
            assert oauth_as.bearer_requests() == []
            keyring.broken.clear()
            await host.connections.sign_out(SERVER)
            await host.connections.connect(SERVER)
            await sign_in(host, browser)
            await _local_serves(host)
    _check_logs(records, keyring, "delete")


async def test_ui_sign_out_with_a_failing_keyring_is_a_fixed_notice(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected_modern_http: str,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    records: pytest.LogCaptureFixture,
) -> None:
    """REQ-NFR-020, REQ-AUTH-007, REQ-UI-003, Art. 5 (F8-03 deviation 24): the Servers
    view's Sign out against a real Host whose keyring ``delete`` raises returns the fixed
    notice (no handler exception, no backend text in the notice or the logs) and the
    Server is not left signed in: the next Connect parks in ``auth_required`` without a
    bearer request."""
    keyring = FlakyKeyring()
    with anyio.fail_after(TIMEOUT * 4):
        async with _host(setup, protected_modern_http, keyring) as host:
            await sign_in(host, browser)
            keyring.broken.add("delete")
            notice = await actions.server_action(host, "sign_out", SERVER)
            keyring.broken.add("get")  # the entry that survived stays unreadable
            assert notice == actions.SIGN_OUT_STORE_FAILED.format(server_id=SERVER)
            assert BACKEND_TEXT not in notice
            assert host.connections.status(SERVER).state is S.DISCONNECTED
            await clear_journal(oauth_as)
            await host.connections.connect(SERVER)
            status = await wait_state(host, S.AUTH_REQUIRED, S.READY)
            assert status.state is S.AUTH_REQUIRED
            assert oauth_as.bearer_requests() == []
            await _local_serves(host)
    _check_logs(records, keyring, "delete")
