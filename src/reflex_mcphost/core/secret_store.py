"""Secret store: OS keyring first, ``data_dir/secrets.json`` (0600) fallback (DD-008, DM §3).

Values are JSON objects. Keyring backends cannot enumerate entries, so every name written is
recorded in the ``secret_index`` table (DM §2.6) — names and backend only, never values.
All keyring and secret-file access runs in a dedicated single-worker thread (Art. 8).
"""

from __future__ import annotations

import json
import os
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import anyio
import keyring
from keyring.backend import KeyringBackend
from keyring.backends import chainer, fail, null

from reflex_mcphost.core.db import Database
from reflex_mcphost.core.db_models import SecretIndexRow
from reflex_mcphost.core.logging import get_logger

SERVICE = "reflex-mcphost"
SECRETS_FILENAME = "secrets.json"
_KINDS = frozenset({"oauth", "hmac"})

log = get_logger("core.secret_store")


class SecretBackend(StrEnum):
    KEYRING = "keyring"
    FILE = "file"


def _usable(backend: KeyringBackend) -> bool:
    if isinstance(backend, fail.Keyring | null.Keyring):
        return False
    if isinstance(backend, chainer.ChainerBackend):
        return bool(backend.backends)
    return True


class SecretStore:
    def __init__(
        self,
        data_dir: Path,
        db: Database,
        *,
        keyring_backend: KeyringBackend | None = None,
    ) -> None:
        self._db = db
        self._file = data_dir / SECRETS_FILENAME
        self._keyring = keyring_backend if keyring_backend is not None else keyring.get_keyring()
        self.backend = SecretBackend.KEYRING if _usable(self._keyring) else SecretBackend.FILE
        if self.backend is SecretBackend.FILE and os.name != "posix":
            log.warning("no usable keyring: secrets fall back to %s (per-user ACL)", self._file)
        self._limiter: anyio.CapacityLimiter | None = None

    @property
    def limiter(self) -> anyio.CapacityLimiter:
        if self._limiter is None:  # needs a running event loop
            self._limiter = anyio.CapacityLimiter(1)
        return self._limiter

    # ------------------------------------------------------------------ public API

    async def get(self, name: str) -> dict[str, Any] | None:
        return await anyio.to_thread.run_sync(self._get, name, limiter=self.limiter)

    async def set(
        self, name: str, value: dict[str, Any], *, kind: str, server_id: str = ""
    ) -> None:
        if kind not in _KINDS:
            raise ValueError(f"invalid secret kind {kind!r}: expected one of {sorted(_KINDS)}")
        await anyio.to_thread.run_sync(self._set, name, value, limiter=self.limiter)
        await self._db.run(self._index_upsert, name, kind, server_id)

    async def delete(self, name: str) -> None:
        await anyio.to_thread.run_sync(self._delete, name, limiter=self.limiter)
        await self._db.run(self._index_delete, name)

    # ------------------------------------------------------------------ blocking helpers

    def _get(self, name: str) -> dict[str, Any] | None:
        if self.backend is SecretBackend.KEYRING:
            raw = self._keyring.get_password(SERVICE, name)
            return None if raw is None else _as_object(json.loads(raw))
        entry = self._read_file().get(name)
        return None if entry is None else _as_object(entry)

    def _set(self, name: str, value: dict[str, Any]) -> None:
        if self.backend is SecretBackend.KEYRING:
            self._keyring.set_password(SERVICE, name, json.dumps(value))
            return
        entries = self._read_file()
        entries[name] = value
        self._write_file(entries)

    def _delete(self, name: str) -> None:
        if self.backend is SecretBackend.KEYRING:
            if self._keyring.get_password(SERVICE, name) is not None:
                self._keyring.delete_password(SERVICE, name)
            return
        entries = self._read_file()
        if entries.pop(name, None) is not None:
            self._write_file(entries)

    def _read_file(self) -> dict[str, Any]:
        try:
            doc = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        entries = _as_object(doc).get("entries", {})
        return dict(_as_object(entries))

    def _write_file(self, entries: dict[str, Any]) -> None:
        tmp = self._file.with_name(self._file.name + ".tmp")
        payload = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._file)

    def _index_upsert(self, name: str, kind: str, server_id: str) -> None:
        with self._db.session() as s:
            row = s.get(SecretIndexRow, name) or SecretIndexRow(
                name=name, kind=kind, backend=self.backend.value, updated_at_us=0
            )
            row.kind, row.server_id, row.backend = kind, server_id, self.backend.value
            row.updated_at_us = time.time_ns() // 1000
            s.add(row)
            s.commit()

    def _index_delete(self, name: str) -> None:
        with self._db.session() as s:
            row = s.get(SecretIndexRow, name)
            if row is not None:
                s.delete(row)
                s.commit()


def _as_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("secret store entry is not a JSON object")
    return value
