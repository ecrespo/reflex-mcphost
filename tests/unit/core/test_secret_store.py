"""Secret store: keyring first, 0600 JSON file fallback (DD-008, DM §2.6, §3)."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail, null
from sqlmodel import select

from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.db_models import SecretIndexRow
from reflex_mcphost.core.secret_store import SERVICE, SecretBackend, SecretStore

pytestmark = pytest.mark.anyio


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.data[(service, username)]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


def _index(db: Database) -> list[SecretIndexRow]:
    with db.session() as s:
        return list(s.exec(select(SecretIndexRow)))


async def test_keyring_backend_round_trip(db: Database, tmp_path: Path) -> None:
    """REQ-NFR-012: with a usable keyring, entries live there, never in a file."""
    kr = MemoryKeyring()
    store = SecretStore(tmp_path / "data", db, keyring_backend=kr)
    assert store.backend is SecretBackend.KEYRING
    await store.set("hmac-key", {"v": 1, "key": "k"}, kind="hmac")
    assert await store.get("hmac-key") == {"v": 1, "key": "k"}
    assert json.loads(kr.data[(SERVICE, "hmac-key")]) == {"v": 1, "key": "k"}
    assert not (tmp_path / "data" / "secrets.json").exists()
    [row] = _index(db)
    assert (row.name, row.kind, row.backend, row.server_id) == ("hmac-key", "hmac", "keyring", "")


@pytest.mark.parametrize("backend", [fail.Keyring(), null.Keyring()])
async def test_unusable_keyring_falls_back_to_0600_file(
    db: Database, tmp_path: Path, backend: KeyringBackend
) -> None:
    """REQ-NFR-012: a fail/null keyring selects `secrets.json`, created with mode 0600."""
    store = SecretStore(tmp_path / "data", db, keyring_backend=backend)
    assert store.backend is SecretBackend.FILE
    await store.set("oauth:gh", {"v": 1, "tokens": {}}, kind="oauth", server_id="gh")
    path = tmp_path / "data" / "secrets.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc == {"version": 1, "entries": {"oauth:gh": {"v": 1, "tokens": {}}}}
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    reopened = SecretStore(tmp_path / "data", db, keyring_backend=backend)
    assert await reopened.get("oauth:gh") == {"v": 1, "tokens": {}}
    [row] = _index(db)
    assert (row.backend, row.server_id) == ("file", "gh")


async def test_delete_removes_entry_and_index(db: Database, tmp_path: Path) -> None:
    """REQ-NFR-012: deleting an entry also deletes its `secret_index` row."""
    for backend in (MemoryKeyring(), fail.Keyring()):
        store = SecretStore(tmp_path / "data", db, keyring_backend=backend)
        await store.set("oauth:x", {"v": 1}, kind="oauth", server_id="x")
        await store.delete("oauth:x")
        await store.delete("oauth:x")  # idempotent
        assert await store.get("oauth:x") is None
        assert _index(db) == []


async def test_missing_entry_is_none(db: Database, tmp_path: Path) -> None:
    """REQ-NFR-012: reading an absent name returns None on both backends."""
    for backend in (MemoryKeyring(), fail.Keyring()):
        store = SecretStore(tmp_path / "data", db, keyring_backend=backend)
        assert await store.get("nope") is None


async def test_rejects_unknown_kind(db: Database, tmp_path: Path) -> None:
    """DM §2.6: `kind` is 'oauth' or 'hmac'."""
    store = SecretStore(tmp_path / "data", db, keyring_backend=MemoryKeyring())
    with pytest.raises(ValueError, match="kind"):
        await store.set("x", {}, kind="other")
