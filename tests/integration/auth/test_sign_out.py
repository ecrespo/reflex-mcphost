"""``ConnectionManager.sign_out`` (F4-08, REQ-AUTH-007).

Every test runs on the modern and the legacy protected fixture (``protected_http``), with
a keyring backend and with the file fallback.
"""

from __future__ import annotations

import json

import anyio
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from sqlmodel import select

from reflex_mcphost.core.auth.token_store import entry_name
from reflex_mcphost.core.db_models import SecretIndexRow
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.secret_store import SERVICE
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import Browser
from tests.integration.auth.helpers import SERVER, TIMEOUT, S, http_table, sign_in, wait_state
from tests.integration.test_host import Setup
from tests.unit.core.test_secret_store import MemoryKeyring

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("backend", ["keyring", "file"])
async def test_sign_out_deletes_credentials_and_disconnects(
    setup: Setup,
    oauth_as: AuthServerState,
    protected_http: str,
    browser: Browser,
    backend: str,
) -> None:
    """REQ-AUTH-007: sign_out deletes the ``oauth:{id}`` entry and its ``secret_index`` row
    (keyring and file fallback), leaves the Server ``disconnected``; the next ``connect``
    lands in auth_required (no stored token is presented); repeating it is a no-op and an
    unknown id raises ``UnknownServerError``."""
    keyring: KeyringBackend = MemoryKeyring() if backend == "keyring" else fail.Keyring()
    config = setup.config({SERVER: http_table(protected_http)})
    host = Host(config, platform=setup.files.platform, env=setup.env, keyring_backend=keyring)
    name = entry_name(SERVER)
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            await sign_in(host, browser)
            assert await host._secrets.get(name) is not None  # type: ignore[union-attr]
            if isinstance(keyring, MemoryKeyring):
                assert (SERVICE, name) in keyring.data
            await host.connections.sign_out(SERVER)
            assert host.connections.status(SERVER).state is S.DISCONNECTED
            assert await host._secrets.get(name) is None  # type: ignore[union-attr]
            with host.database.session() as s:
                assert s.exec(select(SecretIndexRow).where(SecretIndexRow.name == name)).all() == []
            if isinstance(keyring, MemoryKeyring):
                assert (SERVICE, name) not in keyring.data
            else:
                path = setup.data_dir / "secrets.json"
                stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                assert name not in stored.get("entries", {})
            await host.connections.sign_out(SERVER)  # idempotent
            with pytest.raises(UnknownServerError):
                await host.connections.sign_out("nope")
            oauth_as.journal.clear()
            await host.connections.connect(SERVER)
            status = await wait_state(host, S.AUTH_REQUIRED)
            assert status.reason == "auth_required"
            assert oauth_as.requests("introspect") == []
            assert oauth_as.requests("token") == []


async def test_sign_out_cancels_a_running_flow(
    setup: Setup, oauth_as: AuthServerState, protected_http: str
) -> None:
    """REQ-AUTH-007, REQ-AUTH-006: sign_out during a flow cancels it (``cancelled``)."""
    config = setup.config({SERVER: http_table(protected_http)})
    with anyio.fail_after(TIMEOUT * 3):
        async with setup.host(config) as host:
            await wait_state(host, S.AUTH_REQUIRED)
            flow = await host.connections.authorize(SERVER)
            await host.connections.sign_out(SERVER)
            assert flow.done
            assert flow.failure == "cancelled"
            assert host.connections.status(SERVER).state is S.DISCONNECTED
    assert oauth_as.requests("token") == []
