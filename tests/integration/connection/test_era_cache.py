"""Era cache (F2-08, DM §2.5, Q-E1, Flow B steps 2/5/9, REQ-CON-019)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import mcp_types as types
import pytest
from keyring.backends import fail
from mcp import StdioServerParameters
from pydantic import SecretStr
from sqlmodel import select

from reflex_mcphost.core.config.models import (
    HttpTransport,
    ServerDefinition,
    StdioTransport,
)
from reflex_mcphost.core.connection.era_cache import (
    MIN_FRESHNESS,
    CachedEra,
    EraCache,
)
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.db_models import EraCacheRow
from reflex_mcphost.core.hmac_key import InstallKey, load_or_create_install_key
from reflex_mcphost.core.secret_store import SecretStore
from tests.conftest import RecordedHttp
from tests.integration.connection.test_supervisor import (
    FakeSleep,
    http_def,
    running,
    stdio_def,
    wait_state,
)

pytestmark = pytest.mark.anyio
S = ServerState
ENV_SECRET = "era-env-secret-0123456789"  # noqa: S105 - test sentinel
T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def db_key(tmp_path: Path) -> AsyncIterator[tuple[Database, InstallKey]]:
    db = open_database(tmp_path / "data")
    key = await load_or_create_install_key(
        SecretStore(tmp_path / "data", db, keyring_backend=fail.Keyring())
    )
    yield db, key
    db.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(db_key: tuple[Database, InstallKey], clock: Clock) -> EraCache:
    return EraCache(*db_key, clock=clock)


def _rows(db: Database) -> list[EraCacheRow]:
    with db.session() as s:
        return list(s.exec(select(EraCacheRow)).all())


def _stdio(env: dict[str, str] | None = None, *, server_id: str = "s") -> ServerDefinition:
    values = {"API_KEY": ENV_SECRET} if env is None else env
    return ServerDefinition(
        id=server_id,
        transport=StdioTransport(
            command="node",
            args=("srv.js",),
            cwd=Path("/proj"),
            env={k: SecretStr(v) for k, v in values.items()},
        ),
    )


def _http(url: str, *, server_id: str = "h") -> ServerDefinition:
    return ServerDefinition(id=server_id, transport=HttpTransport(url=url))  # type: ignore[arg-type]


def _discover(ttl_ms: int = 0) -> types.DiscoverResult:
    return types.DiscoverResult(
        supported_versions=["2026-07-28"],
        capabilities=types.ServerCapabilities(tools=types.ToolsCapability()),
        result_type="complete",
        ttl_ms=ttl_ms,
        cache_scope="public",
    )


# ------------------------------------------------------------------ keys and Q-E1


async def test_stdio_key_covers_env_values_without_storing_them(cache: EraCache) -> None:
    """REQ-CON-019, DM §2.5: one key per stdio process config (command, args, cwd, env names
    and values via the install-keyed hash); the key never contains a value."""
    key = cache.key_for(_stdio())
    assert key.startswith("sha256:")
    assert len(key) == len("sha256:") + 64
    assert key == cache.key_for(_stdio(server_id="other"))  # per process config, not per id
    assert key != cache.key_for(_stdio({"API_KEY": "another-value"}))
    assert key != cache.key_for(_stdio({"OTHER": ENV_SECRET}))
    assert ENV_SECRET not in key


async def test_http_key_is_origin_and_path(cache: EraCache) -> None:
    """REQ-CON-019, DM §2.5: HTTP key = scheme://host:port + path (default ports explicit;
    query, userinfo and fragment ignored)."""
    base = cache.key_for(_http("https://mcp.example.com/mcp"))
    assert base == cache.key_for(_http("https://MCP.example.com:443/mcp?token=x#f"))
    assert base == cache.key_for(_http("https://user:pw@mcp.example.com/mcp"))
    assert base != cache.key_for(_http("https://mcp.example.com:8443/mcp"))
    assert base != cache.key_for(_http("https://mcp.example.com/other"))
    assert base != cache.key_for(_http("http://mcp.example.com/mcp"))


async def test_put_get_drop_round_trip(
    cache: EraCache, db_key: tuple[Database, InstallKey]
) -> None:
    """REQ-CON-019, Q-E1: upsert modern (with DiscoverResult) and legacy (without), read
    back, overwrite per key, delete."""
    modern, legacy = _stdio(), _http("http://127.0.0.1:9/mcp")
    assert await cache.get(modern) is None
    await cache.put(modern, protocol_version="2026-07-28", discover_result=_discover())
    await cache.put(legacy, protocol_version="2025-11-25", discover_result=None)
    hit = await cache.get(modern)
    assert hit == CachedEra(
        era="modern", protocol_version="2026-07-28", discover_result=_discover(), fetched_at=T0
    )
    legacy_hit = await cache.get(legacy)
    assert legacy_hit is not None
    assert legacy_hit.era == "legacy"
    assert legacy_hit.discover_result is None
    await cache.put(modern, protocol_version="2025-11-25", discover_result=None)  # overwrite
    overwritten = await cache.get(modern)
    assert overwritten is not None
    assert overwritten.era == "legacy"
    assert len(_rows(db_key[0])) == 2
    await cache.drop(modern)
    assert await cache.get(modern) is None
    assert [r.server_id for r in _rows(db_key[0])] == ["h"]
    assert ENV_SECRET not in repr(_rows(db_key[0]))


async def test_freshness_is_max_of_ttl_and_24h(cache: EraCache, clock: Clock) -> None:
    """DM §2.5: an entry is used while now - fetched_at < max(ttl_ms, 24 h)."""
    short, long = _stdio(), _stdio({"K": "v"})
    await cache.put(short, protocol_version="2026-07-28", discover_result=_discover(ttl_ms=10))
    await cache.put(
        long, protocol_version="2026-07-28", discover_result=_discover(ttl_ms=48 * 3600 * 1000)
    )
    clock.now = T0 + MIN_FRESHNESS - timedelta(seconds=1)
    assert await cache.get(short) is not None
    clock.now = T0 + MIN_FRESHNESS
    assert await cache.get(short) is None
    assert await cache.get(long) is not None
    clock.now = T0 + timedelta(hours=48)
    assert await cache.get(long) is None


async def test_unreadable_row_is_a_miss(
    cache: EraCache, db_key: tuple[Database, InstallKey]
) -> None:
    """REQ-CON-019: a row whose DiscoverResult no longer validates is ignored and removed."""
    definition = _stdio()
    await cache.put(definition, protocol_version="2026-07-28", discover_result=_discover())
    db = db_key[0]
    with db.session() as s:
        row = s.exec(select(EraCacheRow)).one()
        row.discover_result = json.dumps({"capabilities": "nope"})
        s.add(row)
        s.commit()
    assert await cache.get(definition) is None
    assert _rows(db) == []


async def test_retain_prunes_rows_of_removed_definitions(
    cache: EraCache, db_key: tuple[Database, InstallKey]
) -> None:
    """DM §6: rows of Servers no longer defined (or whose key changed) are pruned."""
    kept, removed = _stdio(server_id="kept"), _http("http://127.0.0.1:9/mcp", server_id="gone")
    for definition in (kept, removed):
        await cache.put(definition, protocol_version="2025-11-25", discover_result=None)
    assert await cache.retain([kept, _http("http://127.0.0.1:10/mcp")]) == 1
    assert [r.server_id for r in _rows(db_key[0])] == ["kept"]


# ------------------------------------------------------------------ supervisor wiring


def _methods(recorded: RecordedHttp) -> list[str]:
    """``Mcp-Method`` per recorded request; ``""`` marks a legacy-era (handshake) request,
    which carries no such header (REQ-CON-016 is modern-only)."""
    return [r["headers"].get("mcp-method", "") for r in recorded.requests()]


async def test_second_connect_skips_discover(
    modern_http_recorded: RecordedHttp, cache: EraCache
) -> None:
    """REQ-CON-019, Flow B steps 2/5: after a modern connect the era is cached; the next
    connect pins 2026-07-28 with prior_discover and sends no server/discover."""
    definition = http_def(modern_http_recorded.url)
    async with running(definition, era_cache=cache) as (sup, _):
        first = await wait_state(sup, S.READY)
    assert "server/discover" in _methods(modern_http_recorded)
    hit = await cache.get(definition)
    assert hit is not None
    assert hit.era == "modern"
    assert hit.discover_result is not None
    modern_http_recorded.headers_file.unlink()
    async with running(definition, era_cache=cache) as (sup, rec):
        second = await wait_state(sup, S.READY)
    methods = _methods(modern_http_recorded)
    assert "server/discover" not in methods
    assert methods
    assert all(methods)  # modern requests only: no initialize handshake
    assert second.era == "modern"
    assert second.protocol_version == first.protocol_version
    assert second.capabilities == first.capabilities
    assert (S.CONNECTING, S.READY) in rec.pairs()


@pytest.mark.interop
async def test_stale_modern_entry_for_legacy_server_is_redetected(
    legacy_stdio: StdioServerParameters, cache: EraCache
) -> None:
    """REQ-CON-019, Flow B step 9: a cached modern entry for a Server now running legacy
    fails with an era-indicating error; the entry is dropped and the connect succeeds with
    `auto` — no failure counted, no reconnecting state."""
    definition = stdio_def(legacy_stdio, server_id="legacy")
    await cache.put(definition, protocol_version="2026-07-28", discover_result=_discover())
    sleep = FakeSleep()
    async with running(definition, era_cache=cache, sleep=sleep) as (sup, rec):
        status = await wait_state(sup, S.READY)
        assert status.era == "legacy"
        assert status.consecutive_failures == 0
    assert sleep.delays == []
    assert S.RECONNECTING not in {new for _, new in rec.pairs()}
    hit = await cache.get(definition)
    assert hit is not None
    assert hit.era == "legacy"


async def test_cached_legacy_entry_uses_legacy_mode(
    modern_http_recorded: RecordedHttp, cache: EraCache
) -> None:
    """DM §2.5: a legacy entry skips the probe (mode="legacy": initialize, no discover)."""
    definition = http_def(modern_http_recorded.url)
    await cache.put(definition, protocol_version="2025-11-25", discover_result=None)
    async with running(definition, era_cache=cache) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert status.era == "legacy"
    methods = _methods(modern_http_recorded)
    assert methods
    assert set(methods) == {""}  # initialize handshake only, no server/discover probe


async def test_forced_modes(modern_http_recorded: RecordedHttp, cache: EraCache) -> None:
    """REQ-CON-019 escape hatch (API §4.3 `mode`): "legacy" forces initialize; "modern"
    never falls back to initialize and still records the Server's real capabilities."""
    base = http_def(modern_http_recorded.url)
    legacy = base.model_copy(update={"mode": "legacy"})
    async with running(legacy, era_cache=cache) as (sup, _):
        assert (await wait_state(sup, S.READY)).era == "legacy"
    assert set(_methods(modern_http_recorded)) == {""}
    modern_http_recorded.headers_file.unlink()
    modern = base.model_copy(update={"mode": "modern"})
    async with running(modern) as (sup, _):
        status = await wait_state(sup, S.READY)
    assert status.era == "modern"
    assert status.capabilities is not None
    assert status.capabilities.tools is not None
    methods = _methods(modern_http_recorded)
    assert "server/discover" in methods
    assert all(methods)  # never fell back to initialize


class BrokenCache(EraCache):
    def __init__(self) -> None:
        pass

    def key_for(self, definition: ServerDefinition) -> str:
        raise RuntimeError("cache bug")


async def test_era_cache_errors_never_block_a_connect(
    modern_stdio: StdioServerParameters,
) -> None:
    """REQ-CON-011, REQ-CON-019: the cache is an optimisation; its failures fall back to
    `auto` and the Server still reaches ready."""
    async with running(stdio_def(modern_stdio), era_cache=BrokenCache()) as (sup, _):
        assert (await wait_state(sup, S.READY)).era == "modern"
