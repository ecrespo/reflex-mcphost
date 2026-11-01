"""Forward-only migrations and schema drift (DM §5, §9)."""

from __future__ import annotations

import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from reflex_mcphost.core import db_models  # noqa: F401 - registers the tables
from reflex_mcphost.core.db import DB_FILENAME, open_database
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.migrations.runner import LATEST_VERSION, migrate, schema_version

EXPECTED_TABLES = {
    "meta",
    "consent_rule",
    "audit_record",
    "trusted_server",
    "era_cache",
    "secret_index",
}
EXPECTED_INDEXES = {
    "uq_consent_rule_key",
    "ix_audit_time",
    "ix_audit_server_time",
    "ix_audit_fqn_time",
    "ix_audit_consumer_time",
    "ix_audit_in_progress",
    "ix_secret_server",
}


Connect = Callable[[Path], sqlite3.Connection]


@pytest.fixture
def connect() -> Iterator[Connect]:
    """``sqlite3.connect`` whose connections are closed at teardown (F8-04: no
    ``ResourceWarning`` from a connection left to the garbage collector)."""
    opened: list[sqlite3.Connection] = []

    def _connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, isolation_level=None)
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()


def test_fresh_db_reaches_latest_version(tmp_path: Path, connect: Connect) -> None:
    """DM §5: an empty DB is migrated to the latest version (1)."""
    conn = connect(tmp_path / "x.db")
    assert schema_version(conn) == 0
    migrate(conn, tmp_path / "x.db")
    assert schema_version(conn) == LATEST_VERSION == 1
    names = {
        r[0]: r[1]
        for r in conn.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    assert set(names) >= EXPECTED_TABLES
    assert set(names) >= EXPECTED_INDEXES
    assert {"audit_record_immutable", "audit_record_no_delete"} <= set(names)


def test_rerun_is_idempotent(tmp_path: Path, connect: Connect) -> None:
    """DM §5: re-running at the latest version changes nothing and makes no backup."""
    path = tmp_path / "x.db"
    conn = connect(path)
    migrate(conn, path)
    install_id = conn.execute("SELECT value FROM meta WHERE key = 'install_id'").fetchone()
    migrate(conn, path)
    assert conn.execute("SELECT value FROM meta WHERE key = 'install_id'").fetchone() == install_id
    assert not list(tmp_path.glob("*.bak-*"))


def test_db_ahead_refuses_naming_backup(tmp_path: Path, connect: Connect) -> None:
    """DM §5: a DB newer than the code fails closed and names the backup to restore."""
    path = tmp_path / "x.db"
    conn = connect(path)
    migrate(conn, path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(MCPHostError, match=r"x\.db\.bak-v1"):
        migrate(conn, path)


def test_upgrade_takes_backup_first(tmp_path: Path, connect: Connect) -> None:
    """DM §5: a DB behind the code is backed up (0600) before migrating."""
    path = tmp_path / "x.db"
    conn = connect(path)
    migrate(conn, path)
    conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    # pretend v1 is pending again on a non-empty DB: up() must not run twice, so use a
    # custom migration list with a no-op v1
    from types import SimpleNamespace

    noop = SimpleNamespace(VERSION=1, up=lambda c: None)
    migrate(conn, path, migrations=[noop])
    backup = tmp_path / "x.db.bak-v0"
    assert backup.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert schema_version(conn) == 1


def test_failed_migration_rolls_back(tmp_path: Path, connect: Connect) -> None:
    """DM §5: migrations apply inside one transaction; a failure leaves the DB untouched."""
    from types import SimpleNamespace

    path = tmp_path / "x.db"
    conn = connect(path)

    def boom(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE t (x INTEGER) STRICT")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        migrate(conn, path, migrations=[SimpleNamespace(VERSION=1, up=boom)])
    assert schema_version(conn) == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 't'").fetchone() is None


def _sql_type(col: object) -> str:
    python_type = col.type.python_type  # type: ignore[attr-defined]
    return "INTEGER" if python_type in (int, bool) else "TEXT"


def test_schema_matches_sqlmodel_metadata(tmp_path: Path, connect: Connect) -> None:
    """DM §9: the migrated schema and the SQLModel row classes do not drift."""
    database = open_database(tmp_path / "data")
    database.close()
    conn = connect(tmp_path / "data" / DB_FILENAME)
    meta_tables = {
        t.name: t for t in SQLModel.metadata.tables.values() if t.name in EXPECTED_TABLES
    }
    assert set(meta_tables) == EXPECTED_TABLES
    for name, table in meta_tables.items():
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        db_cols = {r[1]: {"type": r[2], "notnull": bool(r[3]), "pk": r[5] > 0} for r in info}
        model_cols = {
            c.name: {"type": _sql_type(c), "notnull": not c.nullable, "pk": c.primary_key}
            for c in table.columns
        }
        assert set(db_cols) == set(model_cols), name
        for col, spec in model_cols.items():
            got = db_cols[col]
            assert got["type"] == spec["type"], f"{name}.{col} type"
            assert got["pk"] == spec["pk"], f"{name}.{col} pk"
            if not spec["pk"]:
                assert got["notnull"] == spec["notnull"], f"{name}.{col} nullability"
