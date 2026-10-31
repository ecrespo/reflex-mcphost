"""Per-install HMAC key and keyed hashing (DD-007, DM §2.4, §3).

``keyed_hash(obj) = "hmac-sha256:" + HMAC-SHA256(key, canonical_json(obj))``. Used for audit
argument hashes and project-Server trust fingerprints; comparable only within one install.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from reflex_mcphost.core.secret_store import SecretStore
from reflex_mcphost.core.secrets import register_secret

HMAC_KEY_NAME = "hmac-key"
HASH_PREFIX = "hmac-sha256:"


def _normalize(obj: Any) -> Any:
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_normalize(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace, UTF-8 kept, integral floats as ints; NaN/inf rejected."""
    return json.dumps(
        _normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class InstallKey:
    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("install key must be 32 bytes")
        self._key = key

    def keyed_hash(self, obj: Any) -> str:
        digest = hmac.new(self._key, canonical_json(obj).encode("utf-8"), hashlib.sha256)
        return HASH_PREFIX + digest.hexdigest()

    def __repr__(self) -> str:
        return "InstallKey(**********)"


async def load_or_create_install_key(store: SecretStore) -> InstallKey:
    entry = await store.get(HMAC_KEY_NAME)
    if entry is None:
        key = secrets.token_bytes(32)
        b64 = base64.b64encode(key).decode("ascii")
        await store.set(
            HMAC_KEY_NAME,
            {"v": 1, "key": b64, "created_at_us": time.time_ns() // 1000},
            kind="hmac",
        )
    else:
        b64 = str(entry["key"])
        key = base64.b64decode(b64)
    register_secret(b64)
    return InstallKey(key)
