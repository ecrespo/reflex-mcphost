"""Era interop matrix and crash isolation (F2-14; Tech Design §8, API §4.5, Flow B).

Cells: modern Host ↔ modern Server (stdio, HTTP), modern Host ↔ legacy Server (``auto``
fallback; stdio, HTTP), no shared version (``failed`` with both version lists), cached-era
invalidation (one definition served by the modern, then the legacy Server). Crash
isolation: three Servers, one crashing over and over, the other two stay ``ready``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from keyring.backends import fail
from mcp import StdioServerParameters

from reflex_mcphost.core.connection.era_cache import EraCache
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.db import open_database
from reflex_mcphost.core.events import EventBus, ServerStateChanged
from reflex_mcphost.core.hmac_key import load_or_create_install_key
from reflex_mcphost.core.secret_store import SecretStore
from tests.conftest import MODERN, SERVERS
from tests.integration.connection.test_supervisor import (
    TIMEOUT,
    FakeSleep,
    http_def,
    running,
    stdio_def,
    wait_state,
)

pytestmark = pytest.mark.anyio
S = ServerState


@pytest.fixture
async def era_cache(tmp_path: Path) -> AsyncIterator[EraCache]:
    db = open_database(tmp_path / "data")
    key = await load_or_create_install_key(
        SecretStore(tmp_path / "data", db, keyring_backend=fail.Keyring())
    )
    yield EraCache(db, key)
    db.close()


async def test_modern_host_modern_server_stdio(modern_stdio: StdioServerParameters) -> None:
    """REQ-CON-001: modern ↔ modern over stdio: server/discover, era modern, 2026-07-28."""
    async with running(stdio_def(modern_stdio)) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert (status.era, status.protocol_version) == ("modern", "2026-07-28")


async def test_modern_host_modern_server_http(modern_http: str) -> None:
    """REQ-CON-001: modern ↔ modern over Streamable HTTP."""
    async with running(http_def(modern_http)) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert (status.era, status.protocol_version) == ("modern", "2026-07-28")


@pytest.mark.interop
async def test_modern_host_legacy_server_stdio(legacy_stdio: StdioServerParameters) -> None:
    """REQ-CON-001: the `auto` probe falls back to initialize for a legacy stdio Server."""
    async with running(stdio_def(legacy_stdio, server_id="legacy")) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert (status.era, status.protocol_version) == ("legacy", "2025-11-25")


@pytest.mark.interop
async def test_modern_host_legacy_server_http(legacy_http: str) -> None:
    """REQ-CON-001: the `auto` probe falls back to initialize for a legacy HTTP Server."""
    async with running(http_def(legacy_http)) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert (status.era, status.protocol_version) == ("legacy", "2025-11-25")


async def test_no_version_overlap_fails_with_both_versions() -> None:
    """REQ-CON-004: a Server that shares no protocol version → failed, the reason names the
    requested and the supported versions, and no retry is made."""
    params = StdioServerParameters(
        command=sys.executable, args=[str(MODERN), "--protocol-versions", "2099-01-01,2099-06-01"]
    )
    sleep = FakeSleep()
    async with running(stdio_def(params), sleep=sleep) as (sup, rec):
        status = await wait_state(sup, S.FAILED)
        await anyio.sleep(0.1)
        assert sup.status.state is S.FAILED
    assert status.reason == (
        "unsupported_protocol_version: requested 2026-07-28, supported 2099-01-01, 2099-06-01"
    )
    assert sleep.delays == []
    assert S.RECONNECTING not in {new for _, new in rec.pairs()}


@pytest.mark.interop
async def test_cached_era_is_invalidated_when_the_server_changes_era(
    tmp_path: Path, legacy_stdio: StdioServerParameters, era_cache: EraCache
) -> None:
    """REQ-CON-019: the detected modern era is cached for the stdio process key; when the same
    definition is later served by a legacy Server, the cached era fails with an
    era-indicating error, is dropped and re-detected (legacy) without a failure."""
    argv_file = tmp_path / "argv.json"
    argv_file.write_text(json.dumps([sys.executable, str(MODERN)]), encoding="utf-8")
    switch = StdioServerParameters(
        command=sys.executable, args=[str(SERVERS / "era_switch.py"), str(argv_file)]
    )
    definition = stdio_def(switch, server_id="switch")
    async with running(definition, era_cache=era_cache) as (sup, _):
        assert (await wait_state(sup, S.READY)).era == "modern"
    cached = await era_cache.get(definition)
    assert cached is not None
    assert cached.era == "modern"
    legacy_argv = [legacy_stdio.command, *legacy_stdio.args]
    argv_file.write_text(json.dumps(legacy_argv), encoding="utf-8")
    sleep = FakeSleep()
    async with running(definition, era_cache=era_cache, sleep=sleep) as (sup, rec):
        status = await wait_state(sup, S.READY)
        assert status.era == "legacy"
        assert status.consecutive_failures == 0
    assert sleep.delays == []
    assert S.RECONNECTING not in {new for _, new in rec.pairs()}
    cached = await era_cache.get(definition)
    assert cached is not None
    assert cached.era == "legacy"


async def test_crashing_server_never_disturbs_the_others(
    modern_stdio: StdioServerParameters,
) -> None:
    """REQ-CON-011: of three Servers, one crashes right after every connect; it cycles
    through reconnecting while the other two stay ready throughout (no transition)."""
    crasher = StdioServerParameters(
        command=sys.executable, args=[str(MODERN), "--crash-after", "0.3"]
    )
    bus = EventBus()
    transitions: list[ServerStateChanged] = []
    sleep = FakeSleep()
    async with anyio.create_task_group() as tg, bus.subscribe(buffer=4096) as events:

        async def collect() -> None:
            async for event in events:
                if isinstance(event, ServerStateChanged):
                    transitions.append(event)

        tg.start_soon(collect)
        sups = [
            ServerSupervisor(stdio_def(p, server_id=i), events=bus, sleep=sleep)
            for i, p in (("a", modern_stdio), ("b", modern_stdio), ("crash", crasher))
        ]
        for sup in sups:
            sup.start(tg)
        for sup in sups[:2]:
            await wait_state(sup, S.READY)

        def crashes() -> int:
            return sum(1 for e in transitions if e.server_id == "crash" and e.new is S.RECONNECTING)

        with anyio.fail_after(TIMEOUT * 2):
            while crashes() < 3:  # noqa: ASYNC110 - transitions are collected by a task
                await anyio.sleep(0.05)
        assert [s.status.state for s in sups[:2]] == [S.READY, S.READY]
        others = [(e.server_id, e.old, e.new) for e in transitions if e.server_id != "crash"]
        assert sorted(others) == sorted(
            [(i, S.DISCONNECTED, S.CONNECTING) for i in "ab"]
            + [(i, S.CONNECTING, S.READY) for i in "ab"]
        )
        with anyio.fail_after(TIMEOUT):
            for sup in sups:
                await sup.aclose()
        tg.cancel_scope.cancel()
