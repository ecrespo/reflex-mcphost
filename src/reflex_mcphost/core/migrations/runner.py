"""Migration runner (Data Model §5).

1. read ``meta.schema_version`` (no ``meta`` table → 0, fresh DB);
2. if behind: ``VACUUM INTO '<db>.bak-v{old}'`` (mode 0600; skipped for a fresh, empty DB),
   then apply every pending ``up()`` inside one ``BEGIN IMMEDIATE … COMMIT`` and bump the
   version;
3. if ahead of the code: refuse with ``MCPHostError`` naming the backup (fail closed, Art. 3).

The connection must be in autocommit mode (``isolation_level=None``) so the runner controls
the transaction.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pkgutil
import re
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from reflex_mcphost.core.errors import MCPHostError

_MODULE_RE = re.compile(r"^\d{4}_[a-z0-9_]+$")


class Migration(Protocol):
    VERSION: int
    up: Callable[[sqlite3.Connection], None]


def execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute ``script`` statement by statement (``executescript`` would COMMIT first)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            conn.execute(buffer)
            buffer = ""
    if buffer.strip():
        raise ValueError("incomplete SQL statement at end of script")


def discover() -> list[Migration]:
    package = importlib.import_module("reflex_mcphost.core.migrations")
    found: list[Migration] = []
    for info in pkgutil.iter_modules(package.__path__):
        if _MODULE_RE.match(info.name):
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            found.append(module)
    found.sort(key=lambda m: m.VERSION)
    versions = [m.VERSION for m in found]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"migration versions must be 1..n without gaps, got {versions}")
    return found


LATEST_VERSION = len(discover())


def schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0


def backup_path(db_path: Path, version: int) -> Path:
    return db_path.with_name(f"{db_path.name}.bak-v{version}")


def _is_empty(conn: sqlite3.Connection) -> bool:
    count: int = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    return count == 0


def _backup(conn: sqlite3.Connection, target: Path) -> None:
    target.unlink(missing_ok=True)
    old = os.umask(0o077)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        os.umask(old)
    with contextlib.suppress(OSError):  # Windows: the per-user ACL applies
        target.chmod(0o600)


def migrate(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    migrations: Sequence[Migration] | None = None,
) -> int:
    """Bring the DB to the latest version; return the version reached."""
    pending_all = list(migrations) if migrations is not None else discover()
    latest = max((m.VERSION for m in pending_all), default=0)
    current = schema_version(conn)
    if current > latest:
        raise MCPHostError(
            f"database {db_path} has schema version {current}, newer than this reflex-mcphost "
            f"supports ({latest}); upgrade reflex-mcphost or restore the backup "
            f"{backup_path(db_path, latest)} (fail closed)"
        )
    if current == latest:
        return current
    if not _is_empty(conn):
        _backup(conn, backup_path(db_path, current))
    conn.execute("BEGIN IMMEDIATE")
    try:
        for migration in pending_all:
            if current < migration.VERSION:
                migration.up(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (str(latest),),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return latest
