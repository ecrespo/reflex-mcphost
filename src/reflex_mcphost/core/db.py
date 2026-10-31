"""SQLite database access (DD-006, Data Model §1, §5, §7).

``open_database`` creates ``data_dir`` (0700), opens ``mcphost.db`` under ``umask(0o077)``
so the DB, ``-wal`` and ``-shm`` files are 0600 (REQ-NFR-012), runs ``PRAGMA quick_check``
(corruption → fail closed, DM §7), applies pending migrations and returns a ``Database``
wrapping a sync SQLModel engine. All DB work from async code goes through
``Database.run``, a single-worker ``anyio.to_thread`` limiter (serialized writes, Art. 8).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import anyio
import anyio.to_thread
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, create_engine

from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.migrations.runner import migrate

DB_FILENAME = "mcphost.db"
PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA trusted_schema = OFF",
)
T = TypeVar("T")
log = get_logger("core.db")


def apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in PRAGMAS:
        conn.execute(pragma)


def _corrupt(path: Path, detail: str) -> MCPHostError:
    return MCPHostError(
        f"database {path} failed its integrity check ({detail}); reflex-mcphost will not "
        f"start (fail closed). Stop the Host, move {path.name} aside and restore a backup "
        f"copy of the data dir, or a pre-migration backup {path.name}.bak-v<N> if present."
    )


def _prepare(path: Path) -> None:
    """Open with restrictive umask, check integrity, migrate. Raises on corruption."""
    old_umask = os.umask(0o077)
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            try:
                apply_pragmas(conn)
                rows = conn.execute("PRAGMA quick_check").fetchall()
            except sqlite3.DatabaseError as exc:
                raise _corrupt(path, str(exc)) from exc
            if rows != [("ok",)]:
                raise _corrupt(path, "; ".join(str(r[0]) for r in rows[:5]))
            migrate(conn, path)
        finally:
            conn.close()
    finally:
        os.umask(old_umask)


class Database:
    def __init__(self, path: Path, engine: Engine) -> None:
        self.path = path
        self.engine = engine
        self._limiter: anyio.CapacityLimiter | None = None

    @property
    def limiter(self) -> anyio.CapacityLimiter:
        # created lazily: a CapacityLimiter needs a running event loop
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(1)
        return self._limiter

    def session(self) -> Session:
        return Session(self.engine)

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        """Run blocking DB work in the single DB worker thread."""
        return await anyio.to_thread.run_sync(fn, *args, limiter=self.limiter)

    def close(self) -> None:
        self.engine.dispose()


def open_database(data_dir: Path) -> Database:
    created = not data_dir.exists()
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if created and os.name == "posix":
        data_dir.chmod(0o700)  # mkdir's mode is masked by the current umask
    if os.name != "posix":
        log.warning("file modes not enforced on %s; relying on the per-user data dir ACL", os.name)
    path = data_dir / DB_FILENAME
    _prepare(path)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        # Pooled: closing a WAL connection checkpoints (~3 ms), which per audit write would
        # dominate the Host overhead budget (REQ-NFR-002). Access is serialized by `run`.
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=8,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: sqlite3.Connection, _record: object) -> None:
        apply_pragmas(dbapi_conn)

    return Database(path, engine)
