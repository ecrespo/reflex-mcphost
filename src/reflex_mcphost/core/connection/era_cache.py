"""Era cache: the detected era (and modern ``DiscoverResult``) per stdio process config or
HTTP endpoint, so reconnects and restarts skip the probe (REQ-CON-019, DM §2.5, Q-E1).

Keys (``sha256:<hex>``):

- stdio: ``sha256("stdio\\n" + canonical_json({command, args, cwd, env}))`` where ``env`` is
  the install-keyed hash (DD-007) of the env names and values — a changed value is another
  process config, yet no value is stored or guessable offline;
- HTTP: ``sha256("http\\n" + scheme://host:port + path)`` (origin + endpoint; userinfo,
  query and fragment ignored).

An entry is fresh while ``now - fetched_at < max(ttl_ms, 24 h)``. The cache is only an
optimisation: callers treat every error as a miss. All DB work goes through
``Database.run`` (Art. 8).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import mcp_types as types
from pydantic import ValidationError
from sqlmodel import col, delete, select

from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.state import Era, era_of
from reflex_mcphost.core.db import Database
from reflex_mcphost.core.db_models import EraCacheRow
from reflex_mcphost.core.hmac_key import InstallKey, canonical_json
from reflex_mcphost.core.secrets import reveal

__all__ = ["MIN_FRESHNESS", "CachedEra", "EraCache"]

MIN_FRESHNESS = timedelta(hours=24)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_us(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000)


def _from_us(us: int) -> datetime:
    return datetime.fromtimestamp(us / 1_000_000, UTC)


@dataclass(frozen=True)
class CachedEra:
    """One fresh cache entry; ``discover_result`` is set for modern entries only."""

    era: Era
    protocol_version: str
    discover_result: types.DiscoverResult | None
    fetched_at: datetime


class EraCache:
    """Q-E1 read / upsert / delete, plus pruning on registry reload (DM §6)."""

    def __init__(
        self,
        db: Database,
        key: InstallKey,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db = db
        self._key = key
        self._clock = clock

    def key_for(self, definition: ServerDefinition) -> str:
        transport = definition.transport
        if isinstance(transport, StdioTransport):
            env = self._key.keyed_hash({k: reveal(v) for k, v in transport.env.items()})
            material = "stdio\n" + canonical_json(
                {
                    "command": transport.command,
                    "args": list(transport.args),
                    "cwd": str(transport.cwd) if transport.cwd is not None else None,
                    "env": env,
                }
            )
        else:
            parts = urlsplit(str(transport.url))
            scheme = parts.scheme.lower()
            port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme)
            host = (parts.hostname or "").lower()
            material = f"http\n{scheme}://{host}:{port}{parts.path}"
        return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def get(self, definition: ServerDefinition) -> CachedEra | None:
        """The fresh entry for ``definition``, or ``None`` (absent, stale or unreadable)."""
        cache_key = self.key_for(definition)

        def read() -> EraCacheRow | None:
            with self._db.session() as s:
                return s.get(EraCacheRow, cache_key)

        row = await self._db.run(read)
        if row is None:
            return None
        fetched_at = _from_us(row.fetched_at_us)
        freshness = max(timedelta(milliseconds=row.ttl_ms), MIN_FRESHNESS)
        if self._clock() - fetched_at >= freshness:
            return None
        discover: types.DiscoverResult | None = None
        try:
            if row.discover_result is not None:
                discover = types.DiscoverResult.model_validate_json(row.discover_result)
            era: Era = "modern" if row.era == "modern" else "legacy"
            if (era == "modern") != (discover is not None):
                raise ValueError("era and discover_result disagree")
        except (ValidationError, ValueError):
            await self.drop(definition)
            return None
        return CachedEra(era, row.protocol_version, discover, fetched_at)

    async def put(
        self,
        definition: ServerDefinition,
        *,
        protocol_version: str,
        discover_result: types.DiscoverResult | None,
    ) -> None:
        """Upsert the detected era (modern iff ``discover_result`` is given)."""
        era = era_of(protocol_version)
        if (era == "modern") != (discover_result is not None):
            raise ValueError("a modern entry needs its DiscoverResult; a legacy one has none")
        row = EraCacheRow(
            cache_key=self.key_for(definition),
            server_id=definition.id,
            era=era,
            protocol_version=protocol_version,
            discover_result=(
                discover_result.model_dump_json(by_alias=True, exclude_none=True)
                if discover_result is not None
                else None
            ),
            fetched_at_us=_to_us(self._clock()),
            ttl_ms=max(0, discover_result.ttl_ms or 0) if discover_result is not None else 0,
        )

        def upsert() -> None:
            with self._db.session() as s:
                s.merge(row)
                s.commit()

        await self._db.run(upsert)

    async def drop(self, definition: ServerDefinition) -> None:
        """Delete the entry of ``definition`` (era-indicating failure)."""
        cache_key = self.key_for(definition)

        def remove() -> None:
            with self._db.session() as s:
                s.exec(delete(EraCacheRow).where(col(EraCacheRow.cache_key) == cache_key))
                s.commit()

        await self._db.run(remove)

    async def retain(self, definitions: Iterable[ServerDefinition]) -> int:
        """Delete every row whose key belongs to none of ``definitions`` (DM §6); returns
        the number of rows removed."""
        keep = {self.key_for(d) for d in definitions}

        def prune() -> int:
            with self._db.session() as s:
                stale = [
                    row for row in s.exec(select(EraCacheRow)).all() if row.cache_key not in keep
                ]
                for row in stale:
                    s.delete(row)
                s.commit()
                return len(stale)

        return await self._db.run(prune)
