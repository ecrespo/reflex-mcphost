"""``TokenStore``: the SDK ``TokenStorage`` on the secret store (F4-06, DD-008, DM §3)."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import SecretStr
from sqlmodel import select

from reflex_mcphost.core.auth.token_store import TokenStore
from reflex_mcphost.core.config.models import (
    DEFAULT_OAUTH_CLIENT_METADATA_URL,
    OAuthSettings,
)
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.db_models import SecretIndexRow
from reflex_mcphost.core.secret_store import SERVICE, SecretStore
from reflex_mcphost.core.secrets import redact
from tests.unit.core.test_secret_store import MemoryKeyring

pytestmark = pytest.mark.anyio

ACCESS = "access-secret-token-store-0001"
REFRESH = "refresh-secret-token-store-0002"
CLIENT_SECRET = "client-secret-token-store-0003"  # noqa: S105 - test sentinel
ISSUER_A = "http://127.0.0.1:9001/"
ISSUER_B = "http://127.0.0.1:9001/tenant2"
NAME = "oauth:remote"
RESOURCE = "http://127.0.0.1:9100/mcp"
OTHER_RESOURCE = "http://127.0.0.1:9200/mcp"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


@pytest.fixture(params=["keyring", "file"])
def secrets(request: pytest.FixtureRequest, db: Database, tmp_path: Path) -> SecretStore:
    backend: KeyringBackend = MemoryKeyring() if request.param == "keyring" else fail.Keyring()
    return SecretStore(tmp_path / "data", db, keyring_backend=backend)


class Expected:
    """A settable expected issuer (stands in for the SDK context's discovered metadata)."""

    def __init__(self, issuer: str | None = None) -> None:
        self.issuer = issuer

    def __call__(self) -> str | None:
        return self.issuer


def _store(
    secrets: SecretStore,
    oauth: OAuthSettings | None = None,
    *,
    resource: str = RESOURCE,
    expected: Expected | None = None,
) -> TokenStore:
    """A store whose SDK context has discovered ``ISSUER_A`` (unless ``expected`` says)."""
    return TokenStore(
        secrets,
        "remote",
        resource=resource,
        oauth=oauth,
        cimd_url=DEFAULT_OAUTH_CLIENT_METADATA_URL,
        issuer_source=expected if expected is not None else Expected(ISSUER_A),
    )


def _tokens() -> OAuthToken:
    return OAuthToken(access_token=ACCESS, refresh_token=REFRESH, expires_in=3600, scope="read")


def _dcr(issuer: str = ISSUER_A, client_id: str = "dcr-client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://127.0.0.1:50123/callback"],  # type: ignore[list-item]
        token_endpoint_auth_method="none",  # noqa: S106 - a method name, not a secret
        issuer=issuer,
    )


def _index(db: Database) -> list[SecretIndexRow]:
    with db.session() as s:
        return list(s.exec(select(SecretIndexRow)))


async def test_implements_sdk_token_storage(secrets: SecretStore) -> None:
    """REQ-AUTH-004 (storage): ``TokenStore`` satisfies the SDK ``TokenStorage`` protocol and
    round-trips ``OAuthToken`` and ``OAuthClientInformationFull`` on both backends."""
    store: TokenStorage = _store(secrets)
    assert await store.get_tokens() is None
    assert await store.get_client_info() is None
    await store.set_client_info(_dcr())
    await store.set_tokens(_tokens())
    again = _store(secrets)
    assert await again.get_tokens() == _tokens()
    info = await again.get_client_info()
    assert info is not None
    assert info.client_id == "dcr-client-1"
    assert info.issuer == ISSUER_A
    assert again.issuer == ISSUER_A
    assert again.client_id_source == "dcr"


async def test_entry_follows_the_data_model_layout(secrets: SecretStore) -> None:
    """DM §3: one entry ``oauth:{server_id}`` with v, resource, issuer, tokens, client_info,
    client_id_source and updated_at_us; indexed as kind ``oauth`` for the Server."""
    store = _store(secrets)
    await store.set_client_info(_dcr())
    await store.set_tokens(_tokens())
    entry = await secrets.get(NAME)
    assert entry is not None
    keys = {"v", "resource", "issuer", "tokens", "client_info", "client_id_source"}
    assert set(entry) == {*keys, "updated_at_us"}
    assert (entry["v"], entry["issuer"], entry["client_id_source"]) == (1, ISSUER_A, "dcr")
    assert entry["resource"] == RESOURCE
    assert entry["tokens"]["access_token"] == ACCESS
    assert isinstance(entry["updated_at_us"], int)
    [row] = _index(secrets._db)
    assert (row.name, row.kind, row.server_id) == (NAME, "oauth", "remote")


async def test_file_fallback_is_0600(db: Database, tmp_path: Path) -> None:
    """REQ-NFR-012, REQ-AUTH-004: without a keyring the token entry lands in the 0600 file."""
    secrets = SecretStore(tmp_path / "data", db, keyring_backend=fail.Keyring())
    await _store(secrets).set_tokens(_tokens())
    path = tmp_path / "data" / "secrets.json"
    assert NAME in json.loads(path.read_text(encoding="utf-8"))["entries"]
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_keyring_backend_never_writes_a_file(db: Database, tmp_path: Path) -> None:
    """REQ-AUTH-004: with a usable keyring the tokens live there only."""
    keyring = MemoryKeyring()
    secrets = SecretStore(tmp_path / "data", db, keyring_backend=keyring)
    await _store(secrets).set_tokens(_tokens())
    assert json.loads(keyring.data[(SERVICE, NAME)])["tokens"]["refresh_token"] == REFRESH
    assert not (tmp_path / "data" / "secrets.json").exists()


async def test_issuer_change_drops_tokens_and_client_info(secrets: SecretStore) -> None:
    """REQ-AUTH-011: client info registered with another issuer replaces the old one and
    discards the stored tokens (never reused across issuers)."""
    store = _store(secrets)
    await store.set_client_info(_dcr(ISSUER_A, "old-client"))
    await store.set_tokens(_tokens())
    await store.set_client_info(_dcr(ISSUER_B, "new-client"))
    assert await store.get_tokens() is None
    info = await store.get_client_info()
    assert info is not None
    assert (info.client_id, info.issuer) == ("new-client", ISSUER_B)
    assert store.issuer == ISSUER_B


async def test_same_issuer_keeps_tokens(secrets: SecretStore) -> None:
    """REQ-AUTH-011: re-storing client info of the same issuer keeps the tokens."""
    store = _store(secrets)
    await store.set_client_info(_dcr())
    await store.set_tokens(_tokens())
    await store.set_client_info(_dcr())
    assert await store.get_tokens() == _tokens()


async def test_cimd_client_id_source(secrets: SecretStore) -> None:
    """REQ-AUTH-003 (diagnostics): a client id equal to the CIMD URL records source cimd."""
    store = _store(secrets)
    await store.set_client_info(
        OAuthClientInformationFull(
            client_id=DEFAULT_OAUTH_CLIENT_METADATA_URL,
            token_endpoint_auth_method="none",  # noqa: S106 - method name
        )
    )
    assert store.client_id_source == "cimd"
    entry = await secrets.get(NAME)
    assert entry is not None
    assert entry["client_id_source"] == "cimd"


async def test_configured_client_wins_over_stored(secrets: SecretStore) -> None:
    """REQ-AUTH-003: a configured client_id (with secret → client_secret_post) is returned
    before anything stored; without a secret the method is ``none``."""
    plain = _store(secrets)
    await plain.set_client_info(_dcr())
    configured = _store(
        secrets, OAuthSettings(client_id="configured-1", client_secret=SecretStr(CLIENT_SECRET))
    )
    info = await configured.get_client_info()
    assert info is not None
    assert (info.client_id, info.client_secret) == ("configured-1", CLIENT_SECRET)
    assert info.token_endpoint_auth_method == "client_secret_post"  # noqa: S105 - method name
    assert info.issuer is None
    assert configured.client_id_source == "configured"
    public = await _store(secrets, OAuthSettings(client_id="public-1")).get_client_info()
    assert public is not None
    assert (public.client_secret, public.token_endpoint_auth_method) == (None, "none")


@pytest.mark.parametrize(
    "entry",
    [
        {"v": 99, "tokens": {"access_token": "x"}},
        {"v": 1, "tokens": {"no_access_token": True}},
        {"v": 1, "client_info": {"client_secret": "only"}},
        {"v": "1"},
        {"v": 1, "tokens": {"access_token": "x", "token_type": "Bearer"}},  # no resource
    ],
)
async def test_corrupt_entry_reads_as_absent(
    secrets: SecretStore, entry: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """DM §3: a corrupt or unknown-version entry reads as absent (→ re-authorization) and
    the warning carries no entry content."""
    await secrets.set(NAME, entry, kind="oauth", server_id="remote")
    caplog.set_level(logging.WARNING)
    store = _store(secrets)
    assert await store.get_tokens() is None
    assert await store.get_client_info() is None
    assert "unreadable" in caplog.text
    assert "only" not in caplog.text
    await store.set_tokens(_tokens())  # a write replaces the bad entry
    assert await store.get_tokens() == _tokens()


async def test_undecodable_keyring_value_reads_as_absent(db: Database, tmp_path: Path) -> None:
    """DM §3: a keyring value that is not JSON reads as absent."""
    keyring = MemoryKeyring()
    keyring.data[(SERVICE, NAME)] = "{not json"
    store = _store(SecretStore(tmp_path / "data", db, keyring_backend=keyring))
    assert await store.get_tokens() is None


async def test_clear_removes_entry_and_index(secrets: SecretStore) -> None:
    """REQ-AUTH-007: ``clear()`` deletes the entry and its ``secret_index`` row; idempotent."""
    store = _store(secrets)
    await store.set_client_info(_dcr())
    await store.set_tokens(_tokens())
    await store.clear()
    await store.clear()
    assert await secrets.get(NAME) is None
    assert _index(secrets._db) == []
    assert await store.get_tokens() is None
    assert store.issuer is None


async def test_secrets_are_registered_and_never_in_repr_logs_or_db(
    db: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-AUTH-008, REQ-NFR-010, REQ-NFR-012: token, refresh-token and client-secret values
    are registered for redaction on write and read, appear in no ``repr`` or log record, and
    never reach the SQLite file."""
    caplog.set_level(logging.DEBUG)
    secrets = SecretStore(tmp_path / "data", db, keyring_backend=MemoryKeyring())
    store = _store(
        secrets, OAuthSettings(client_id="configured-2", client_secret=SecretStr(CLIENT_SECRET))
    )
    await store.set_tokens(_tokens())
    await store.get_client_info()
    for value in (ACCESS, REFRESH, CLIENT_SECRET):
        assert redact(f"x {value} y") == "x ********** y"
        assert value not in repr(store)
        assert value not in caplog.text
    db.close()
    raw = b"".join(p.read_bytes() for p in (tmp_path / "data").iterdir() if p.is_file())
    for value in (ACCESS, REFRESH, CLIENT_SECRET):
        assert value.encode() not in raw


def _bound(
    secrets: SecretStore, expected: Expected, oauth: OAuthSettings | None = None
) -> TokenStore:
    return _store(secrets, oauth, expected=expected)


def _cimd(issuer: str | None = None) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=DEFAULT_OAUTH_CLIENT_METADATA_URL,
        token_endpoint_auth_method="none",  # noqa: S106 - method name
        issuer=issuer,
    )


CONFIGURED = OAuthSettings(client_id="configured-3")


@pytest.mark.parametrize("kind", ["dcr", "cimd", "configured"])
async def test_tokens_are_bound_to_the_issuer_current_at_write(
    secrets: SecretStore, kind: str
) -> None:
    """REQ-AUTH-011, DM §3: ``set_tokens`` records the issuer the SDK discovered for every
    client kind (DCR, CIMD, configured), so tokens are always issuer-bound."""
    expected = Expected(ISSUER_A)
    store = _bound(secrets, expected, CONFIGURED if kind == "configured" else None)
    if kind == "dcr":
        await store.set_client_info(_dcr(ISSUER_A))
    elif kind == "cimd":
        await store.set_client_info(_cimd())
    await store.set_tokens(_tokens())
    entry = await secrets.get(NAME)
    assert entry is not None
    assert entry["issuer"] == ISSUER_A
    assert await store.get_tokens() == _tokens()


@pytest.mark.parametrize("kind", ["dcr", "cimd", "configured"])
async def test_tokens_of_another_issuer_are_dropped_on_read(
    secrets: SecretStore, kind: str
) -> None:
    """REQ-AUTH-011 (mix-up): once the expected issuer is known and differs from the one
    the tokens are bound to, ``get_tokens`` returns nothing and deletes them — DCR, CIMD
    and configured clients alike."""
    expected = Expected(ISSUER_A)
    store = _bound(secrets, expected, CONFIGURED if kind == "configured" else None)
    if kind == "dcr":
        await store.set_client_info(_dcr(ISSUER_A))
    elif kind == "cimd":
        await store.set_client_info(_cimd(ISSUER_A))
    await store.set_tokens(_tokens())
    expected.issuer = ISSUER_B
    assert await store.get_tokens() is None
    expected.issuer = ISSUER_A  # not a cache: the tokens are gone from storage
    assert await store.get_tokens() is None
    entry = await secrets.get(NAME)
    assert entry is not None
    assert entry.get("tokens") is None


async def test_tokens_are_withheld_before_discovery(secrets: SecretStore) -> None:
    """REQ-AUTH-011, REQ-AUTH-004: before discovery (expected issuer unknown) stored tokens
    are *not presented* (no bearer before the issuer binding check) but are kept; once the
    discovered issuer matches they are returned. A known stored issuer is never replaced
    by None."""
    expected = Expected(ISSUER_A)
    store = _bound(secrets, expected)
    await store.set_tokens(_tokens())
    expected.issuer = None
    assert await store.get_tokens() is None
    await store.set_tokens(_tokens())
    await store.set_client_info(_cimd())
    entry = await secrets.get(NAME)
    assert entry is not None
    assert entry["issuer"] == ISSUER_A
    expected.issuer = ISSUER_A
    assert await store.get_tokens() == _tokens()


async def test_unbound_tokens_then_issuer_is_an_issuer_change(secrets: SecretStore) -> None:
    """REQ-AUTH-011: tokens stored without an issuer are dropped as soon as an issuer
    becomes known — on ``set_client_info`` with an issuer and on a read with a known
    expected issuer."""
    expected = Expected(None)
    store = _bound(secrets, expected)
    await store.set_tokens(_tokens())
    await store.set_client_info(_dcr(ISSUER_B))
    assert await store.get_tokens() is None

    other = _bound(secrets, Expected(None))
    await other.clear()
    await other.set_tokens(_tokens())
    reader = _bound(secrets, Expected(ISSUER_A))
    assert await reader.get_tokens() is None


async def test_entry_is_bound_to_the_resource(
    secrets: SecretStore, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-AUTH-011, RFC 8707, DM §3: the entry records the canonical resource (the
    Server URL); a store for the same id but another URL sees neither tokens nor client
    info, and its first write replaces the foreign entry."""
    caplog.set_level(logging.INFO, logger="reflex_mcphost.core.auth.token_store")
    await _store(secrets).set_client_info(_dcr())
    await _store(secrets).set_tokens(_tokens())
    moved = _store(secrets, resource=OTHER_RESOURCE)
    assert await moved.get_tokens() is None
    assert await moved.get_client_info() is None
    assert "another resource" in caplog.text
    await moved.set_tokens(_tokens())
    entry = await secrets.get(NAME)
    assert entry is not None
    assert entry["resource"] == OTHER_RESOURCE
    assert entry["client_info"] is None
    assert await _store(secrets).get_tokens() is None  # and the old URL no longer matches


async def test_resource_is_canonicalised() -> None:
    """RFC 8707 §2: scheme and host are compared case-insensitively, without fragment."""
    assert TokenStore.canonical_resource("HTTP://127.0.0.1:9100/mcp#x") == RESOURCE


async def test_drop_tokens_keeps_the_client(secrets: SecretStore) -> None:
    """REQ-AUTH-005: after a rejected refresh the dead tokens are deleted (never reloaded)
    while the client registration and the issuer binding stay."""
    store = _store(secrets)
    await store.set_client_info(_dcr())
    await store.set_tokens(_tokens())
    await store.drop_tokens()
    await store.drop_tokens()  # idempotent
    assert await store.get_tokens() is None
    info = await store.get_client_info()
    assert info is not None
    assert info.client_id == "dcr-client-1"
    assert store.issuer == ISSUER_A
