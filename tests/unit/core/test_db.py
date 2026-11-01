"""SQLite database (DM §1, §7, DD-006; REQ-NFR-012)."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest
from sqlmodel import select

from reflex_mcphost.core.db import DB_FILENAME, Database, open_database
from reflex_mcphost.core.db_models import MetaRow
from reflex_mcphost.core.errors import MCPHostError

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes; Windows relies on the per-user ACL"
)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture
def db(tmp_path: Path) -> object:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


def test_pragmas_applied(db: Database) -> None:
    """DM §1: WAL, synchronous NORMAL, foreign keys, busy timeout, trusted_schema off."""
    with db.engine.connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert raw.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert raw.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert raw.execute("PRAGMA trusted_schema").fetchone()[0] == 0


@posix_only
def test_file_modes(tmp_path: Path) -> None:
    """REQ-NFR-012: data_dir is 0700 and the DB, WAL and SHM files are 0600."""
    data = tmp_path / "data"
    database = open_database(data)
    try:
        with database.session() as s:
            s.exec(select(MetaRow)).all()
        assert _mode(data) == 0o700
        for name in (DB_FILENAME, f"{DB_FILENAME}-wal", f"{DB_FILENAME}-shm"):
            path = data / name
            if path.exists():
                assert _mode(path) == 0o600, name
        assert (data / DB_FILENAME).exists()
    finally:
        database.close()


@posix_only
def test_umask_restored(tmp_path: Path) -> None:
    """REQ-NFR-012: the restrictive umask is only applied around the open."""
    before = os.umask(0o022)
    os.umask(before)
    open_database(tmp_path / "d").close()
    after = os.umask(0o022)
    os.umask(after)
    assert after == before


def test_session_reads_meta(db: Database) -> None:
    """DM §2.1: meta holds schema_version, install_id and created_at_us."""
    with db.session() as s:
        rows = {r.key: r.value for r in s.exec(select(MetaRow)).all()}
    assert rows["schema_version"] == "1"
    assert rows["install_id"]
    assert int(rows["created_at_us"]) > 0


@pytest.mark.anyio
async def test_run_db_serializes_in_worker_thread(db: Database) -> None:
    """DD-006: blocking DB calls run behind a single-worker to_thread limiter."""
    import threading

    main = threading.get_ident()

    def work() -> int:
        assert threading.get_ident() != main
        with db.session() as s:
            return len(s.exec(select(MetaRow)).all())

    assert await db.run(work) == 3
    assert db.limiter.total_tokens == 1


def test_corrupted_db_refuses_to_open(tmp_path: Path) -> None:
    """DM §7: a corrupted file fails closed, naming the path and backup instructions."""
    data = tmp_path / "data"
    data.mkdir()
    (data / DB_FILENAME).write_bytes(b"this is not a sqlite database" * 200)
    with pytest.raises(MCPHostError, match=r"mcphost\.db") as info:
        open_database(data)
    assert "backup" in str(info.value)


def test_audit_delete_blocked_by_trigger(db: Database) -> None:
    """DM §2.3: audit rows cannot be deleted; finished rows are immutable."""
    with db.engine.connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None
        raw.execute(
            "INSERT INTO audit_record (invocation_id, status, operation, started_at_us,"
            " finished_at_us, consumer_kind, consumer_name, server_id, fqn, args_hash,"
            " decision_source, outcome, duration_ms) VALUES (?, 'finished', 'tool_call', 1, 2,"
            " 'human', 'ui', 's', 's::t', 'hmac-sha256:00', 'rule', 'success', 1)",
            ("inv_" + "0" * 26,),
        )
        raw.commit()
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            raw.execute("DELETE FROM audit_record")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            raw.execute("UPDATE audit_record SET outcome = 'denied'")
