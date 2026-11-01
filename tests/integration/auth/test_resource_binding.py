"""Stored credentials are bound to the Server URL (RFC 8707 resource; F4-08, DM §3).

Every test runs on the modern and the legacy protected fixture (``protected_http``).
"""

from __future__ import annotations

import pytest

from reflex_mcphost.core.auth.token_store import entry_name
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import Browser
from tests.integration.auth.helpers import (
    SERVER,
    S,
    clear_journal,
    host_for,
    http_table,
    sign_in,
    wait_state,
)
from tests.integration.test_host import Setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("enabled", [True, False])
async def test_url_change_clears_the_stored_entry(
    setup: Setup,
    oauth_as: AuthServerState,
    protected_http: str,
    browser: Browser,
    enabled: bool,
) -> None:
    """REQ-AUTH-011, RFC 8707: when a definition's URL changes under the same id, the
    ``oauth:{id}`` entry is deleted on reconcile (gone while the moved Server is disabled)
    and the old tokens are never sent to the new URL — the Server parks in auth_required
    and whatever is stored afterwards is bound to the new URL, without tokens."""
    moved = protected_http.replace("127.0.0.1", "localhost")
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        secrets = host._secrets
        assert secrets is not None
        assert await secrets.get(entry_name(SERVER)) is not None
        await clear_journal(oauth_as)
        table = http_table(moved) + ("" if enabled else "\nenabled = false")
        setup.write({SERVER: table})
        diff = await host.registry.reload()
        assert diff.changed == (SERVER,)
        if not enabled:
            assert await secrets.get(entry_name(SERVER)) is None
            return
        assert (await wait_state(host, S.AUTH_REQUIRED)).state is S.AUTH_REQUIRED
        entry = await secrets.get(entry_name(SERVER))
        assert entry is None or (entry["resource"], entry["tokens"]) == (moved, None)
    # no bearer token reached the new URL (the old one may see its session closed)
    assert not [r for r in oauth_as.requests("introspect") if "localhost" in r["host"]]
    assert oauth_as.requests("token") == []


async def test_other_changes_keep_the_stored_entry(
    setup: Setup, oauth_as: AuthServerState, protected_http: str, browser: Browser
) -> None:
    """REQ-AUTH-004: a change that keeps the URL (here ``timeout_s``) keeps the tokens; the
    restarted Server is ready again without a prompt."""
    async with host_for(setup, protected_http) as host:
        await sign_in(host, browser)
        setup.write({SERVER: http_table(protected_http) + "\ntimeout_s = 30"})
        diff = await host.registry.reload()
        assert diff.changed == (SERVER,)
        await wait_state(host, S.READY)
        assert await host._secrets.get(entry_name(SERVER)) is not None  # type: ignore[union-attr]
    assert len(oauth_as.requests("authorize")) == 1
