"""Per-install HMAC key and keyed hashing (DD-007, DM §3)."""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from keyring.backends import fail

from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.hmac_key import HMAC_KEY_NAME, canonical_json, load_or_create_install_key
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.secret_store import SecretStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SecretStore]:
    db: Database = open_database(tmp_path / "data")
    yield SecretStore(tmp_path / "data", db, keyring_backend=fail.Keyring())
    db.close()


async def test_key_is_created_once_and_stable(store: SecretStore) -> None:
    """REQ-NFR-012: the 32-byte key is created on first run and reused afterwards."""
    first = await load_or_create_install_key(store)
    second = await load_or_create_install_key(store)
    assert first.keyed_hash({"a": 1}) == second.keyed_hash({"a": 1})
    entry = await store.get(HMAC_KEY_NAME)
    assert entry is not None
    assert entry["v"] == 1
    assert len(base64.b64decode(entry["key"])) == 32


async def test_keyed_hash_format_and_canonical_json(store: SecretStore) -> None:
    """DD-007: `hmac-sha256:<hex>` over canonical JSON; integral floats equal ints."""
    key = await load_or_create_install_key(store)
    h = key.keyed_hash({"b": 1.0, "a": [2, {"c": 3.0}]})
    assert h.startswith("hmac-sha256:")
    assert len(h.removeprefix("hmac-sha256:")) == 64
    assert h == key.keyed_hash({"a": [2, {"c": 3}], "b": 1})
    assert h != key.keyed_hash({"a": [2, {"c": 3}], "b": 1.5})
    assert canonical_json({"b": 1.0, "é": "ü", "a": True}) == '{"a":true,"b":1,"é":"ü"}'


async def test_key_never_appears_in_repr_or_logs(
    store: SecretStore, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-NFR-012: the key material is registered for redaction and hidden in repr."""
    key = await load_or_create_install_key(store)
    entry = await store.get(HMAC_KEY_NAME)
    assert entry is not None
    b64 = entry["key"]
    assert b64 not in repr(key)
    with caplog.at_level(logging.INFO, logger="reflex_mcphost"):
        get_logger("test").info("leaking %s", b64)
    assert b64 not in caplog.text
