"""File modes of everything the Host writes (F8-01; REQ-NFR-012, Art. 5, DD-008, DM §3).

A real Host session on the file fallback (``fail`` keyring) under a permissive process
umask (``0o022``, so nothing is ``0600`` by accident): the HMAC install key and OAuth tokens
land in ``secrets.json``, consent rules and audit records in ``mcphost.db`` (WAL mode: the
``-wal`` / ``-shm`` files exist while the Host runs). Every regular file in the data dir is
user-only (``0600``) — while running and after close — and a data dir the Host creates is
``0700``. Windows has no POSIX modes: the data dir is the per-user (ACL-inherited)
directory and a warning is logged (DD-008); the mode assertions are POSIX-only.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import anyio
import pytest

from reflex_mcphost.core.consent.models import ConsentDecision, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.db import DB_FILENAME
from reflex_mcphost.core.secret_store import SECRETS_FILENAME
from reflex_mcphost.core.types import Consumer
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import (  # noqa: F401 - fixtures used by this module
    Browser,
    browser,
    oauth_as,
    protected_modern_http,
)
from tests.integration.auth.helpers import SERVER, TIMEOUT, http_table, sign_in
from tests.integration.handlers.helpers import HandlerPrompter, modern_stdio_table
from tests.integration.test_host import Setup, setup  # noqa: F401 - fixture
from tests.integration.test_invoker import wait_tools

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows: per-user ACL"),
]
LOCAL = "local"


@pytest.fixture
def permissive_umask() -> Iterator[None]:
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _modes(directory: Path) -> dict[str, int]:
    """Mode of every regular file under ``directory`` (relative POSIX path → mode)."""
    return {
        p.relative_to(directory).as_posix(): stat.S_IMODE(p.stat().st_mode)
        for p in directory.rglob("*")
        if p.is_file()
    }


async def test_every_file_the_host_writes_is_user_only(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected_modern_http: str,  # noqa: F811 - pytest fixture
    browser: Browser,  # noqa: F811 - pytest fixture
    permissive_umask: None,
) -> None:
    """REQ-NFR-012, REQ-AUTH-004, DD-007/008: under umask ``0o022`` the created data dir is
    ``0700`` and the SQLite database (with its WAL and shared-memory files), the token
    fallback file holding the OAuth entry and the HMAC install key, and every other file
    in the data dir are ``0600`` while the Host runs and after it closed."""
    assert not setup.data_dir.exists()
    config = setup.config({SERVER: http_table(protected_modern_http), LOCAL: modern_stdio_table()})
    host = setup.host(config, prompter=HandlerPrompter([], decision=ConsentDecision.ALLOW_ONCE))
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            await sign_in(host, browser)
            await wait_tools(host, LOCAL)
            await host.consent.grant(
                effect="allow",
                operation=OperationKind.TOOL_CALL,
                target=ConsentTarget.tool(f"{LOCAL}::echo"),
                lifetime="always",
            )
            result = await host.invoker.call(
                f"{LOCAL}::echo", {"text": "x"}, consumer=Consumer.agent("bot")
            )
            assert result.ok, result.error
            live = _modes(setup.data_dir)
    closed = _modes(setup.data_dir)
    assert stat.S_IMODE(setup.data_dir.stat().st_mode) == 0o700
    assert {DB_FILENAME, f"{DB_FILENAME}-wal", SECRETS_FILENAME} <= set(live), live
    secrets = (setup.data_dir / SECRETS_FILENAME).read_text(encoding="utf-8")
    assert '"hmac-key"' in secrets
    assert f'"oauth:{SERVER}"' in secrets
    for when, modes in (("running", live), ("closed", closed)):
        wide = {name: oct(mode) for name, mode in modes.items() if mode != 0o600}
        assert wide == {}, when
