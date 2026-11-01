"""``TokenStore``: the SDK ``TokenStorage`` of one Server on the ``SecretStore`` (DD-008, DM §3).

One entry ``oauth:{server_id}`` (kind ``oauth``) holds::

    {"v": 1, "resource": ..., "issuer": ..., "tokens": <OAuthToken>,
     "client_info": <OAuthClientInformationFull>,
     "client_id_source": "configured" | "cimd" | "dcr", "updated_at_us": ...}

Resource binding (RFC 8707): ``resource`` is the canonical URL of the Server the entry was
written for. An entry for another resource (the definition's URL changed under the same
id) reads as absent — neither its tokens nor its client info are used — and the next write
replaces it; an entry without ``resource`` is unreadable.

Issuer binding (REQ-AUTH-011): the SDK drops stored client info bound to another issuer
(SEP-2352), but CIMD and configured client ids are issuer-portable, so the SDK never drops
*their* tokens. This store therefore binds the tokens themselves: the entry's ``issuer`` is
the issuer current when tokens or client info were written (the ``issuer_source`` — the
SDK context's discovered metadata — else ``client_info.issuer``); a known issuer is never
replaced by ``None``. Tokens are discarded when client info of a different issuer is
stored, when an issuer becomes known for tokens stored without one, and on read whenever
the expected issuer is known and differs from the recorded one — for DCR, CIMD and
configured clients alike. Before discovery (no expected issuer yet) stored tokens are
withheld — kept, but not presented — so no bearer token leaves before the issuer binding
check has passed. A configured ``client_id`` is returned before anything stored.
A corrupt or unknown-version entry reads as absent, which
leads to re-authorization. Token, refresh-token and client-secret values are registered for
redaction whenever they are read or written (Art. 5); all I/O goes through ``SecretStore``
(its single-worker thread limiter, Art. 8).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.shared.auth_utils import resource_url_from_server_url
from pydantic import ValidationError

from reflex_mcphost.core.config.models import OAuthSettings
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.secret_store import SecretStore
from reflex_mcphost.core.secrets import register_secret

__all__ = [
    "ENTRY_VERSION",
    "ClientIdSource",
    "SecretStoreReadError",
    "TokenStore",
    "entry_name",
]

ENTRY_VERSION = 1
ClientIdSource = Literal["configured", "cimd", "dcr"]
IssuerSource = Callable[[], str | None]
"""The issuer the SDK currently expects (discovered metadata), or ``None`` before discovery."""
_log = get_logger(__name__)


def entry_name(server_id: str) -> str:
    """The secret-store name of a Server's OAuth entry (DM §3)."""
    return f"oauth:{server_id}"


class SecretStoreReadError(Exception):
    """The secret store (keyring or ``0600`` file) raised while the entry was read.

    Internal: the OAuth controller and the connection manager map it to the
    ``AuthorizationError`` reason ``secret_store_unavailable`` (API §3.4). Its message is
    fixed and the backend exception is not chained — backend text never leaves (Art. 5).
    """

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        super().__init__(
            f"secret store unavailable: could not read the credentials of {server_id!r}"
        )


def _register_tokens(tokens: OAuthToken | None) -> None:
    if tokens is None:
        return
    register_secret(tokens.access_token)
    if tokens.refresh_token:
        register_secret(tokens.refresh_token)


def _register_client(info: OAuthClientInformationFull | None) -> None:
    if info is not None and info.client_secret:
        register_secret(info.client_secret)


class TokenStore:
    """Implements the SDK ``TokenStorage`` protocol for one Server (plus ``clear``)."""

    def __init__(
        self,
        secrets: SecretStore,
        server_id: str,
        *,
        resource: str,
        oauth: OAuthSettings | None,
        cimd_url: str,
        issuer_source: IssuerSource | None = None,
    ) -> None:
        self._issuer_source = issuer_source
        self._secrets = secrets
        self._server_id = server_id
        self._name = entry_name(server_id)
        self._resource = self.canonical_resource(resource)
        self._cimd_url = cimd_url
        self._configured: OAuthClientInformationFull | None = None
        if oauth is not None and oauth.client_id:
            secret = oauth.client_secret.get_secret_value() if oauth.client_secret else None
            self._configured = OAuthClientInformationFull(
                client_id=oauth.client_id,
                client_secret=secret,
                token_endpoint_auth_method="client_secret_post" if secret else "none",
            )
            _register_client(self._configured)
        self._issuer: str | None = None
        self._source: ClientIdSource | None = "configured" if self._configured else None
        self._log = ContextAdapter(_log, server_id=server_id)

    def __repr__(self) -> str:
        return f"TokenStore(server_id={self._server_id!r})"

    @staticmethod
    def canonical_resource(url: str) -> str:
        """RFC 8707 canonical form of a Server URL (lowercase scheme/host, no fragment)."""
        return resource_url_from_server_url(url)

    @property
    def resource(self) -> str:
        """The canonical resource (Server URL) this store's entry is bound to."""
        return self._resource

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def issuer(self) -> str | None:
        """The issuer the stored credentials are bound to (last read or written)."""
        return self._issuer

    @property
    def client_id_source(self) -> ClientIdSource | None:
        """Which registration path produced the client id in use (diagnostics, DM §3)."""
        return self._source

    @property
    def configured(self) -> bool:
        """``True`` when the Server's definition configures a ``client_id``."""
        return self._configured is not None

    def bind_issuer_source(self, source: IssuerSource) -> None:
        """Where the currently expected issuer is read from (the controller's SDK context)."""
        self._issuer_source = source

    def _expected(self) -> str | None:
        if self._issuer_source is None:
            return None
        try:
            return self._issuer_source()
        except Exception:  # never let a binding probe break the SDK flow
            return None

    # ------------------------------------------------------------------ TokenStorage

    async def get_tokens(self) -> OAuthToken | None:
        entry = await self._read()
        raw = entry.get("tokens")
        if raw is None:
            return None
        expected = self._expected()
        if expected is None:  # before discovery: no bearer before the issuer check passed
            return None
        if entry.get("issuer") != expected:
            # REQ-AUTH-011: tokens bound to another issuer (or to none) are never presented
            entry["tokens"] = None
            await self._write(entry)
            self._log.info("stored tokens belong to another issuer; discarded")
            return None
        tokens = OAuthToken.model_validate(raw)  # validated in `_read`
        _register_tokens(tokens)
        return tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        _register_tokens(tokens)
        entry = await self._read()
        issuer = self._expected()
        if issuer is not None and entry.get("issuer") not in (None, issuer):
            self._drop_foreign_client(entry, issuer)
        if issuer is not None:
            entry["issuer"] = issuer
        entry["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        await self._write(entry)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if self._configured is not None:
            return self._configured
        entry = await self._read()
        raw = entry.get("client_info")
        if raw is None:
            return None
        info = OAuthClientInformationFull.model_validate(raw)
        _register_client(info)
        return info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        _register_client(client_info)
        entry = await self._read()
        stored = entry.get("issuer")
        issuer = client_info.issuer or self._expected()
        changed = issuer is not None and stored != issuer and entry.get("tokens") is not None
        if changed:  # REQ-AUTH-011: never reuse tokens across issuers (None → X included)
            entry["tokens"] = None
            self._log.info("authorization server issuer changed; stored tokens discarded")
        if self._configured is not None and client_info.client_id == self._configured.client_id:
            source: ClientIdSource = "configured"
        elif client_info.client_id == self._cimd_url:
            source = "cimd"
        else:
            source = "dcr"
            self._log.info(
                "client registered with Dynamic Client Registration (deprecated in the "
                "pinned spec, used as a compatibility fallback)"
            )
        if issuer is not None:  # a known issuer is never replaced by None
            entry["issuer"] = issuer
        entry["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        entry["client_id_source"] = source
        await self._write(entry)

    # ------------------------------------------------------------------ extras

    async def drop_tokens(self) -> None:
        """Delete the stored tokens only (a refresh was rejected: they are dead)."""
        entry = await self._read()
        if entry.get("tokens") is not None:
            entry["tokens"] = None
            await self._write(entry)

    async def clear(self) -> None:
        """Delete the entry and its ``secret_index`` row (sign-out, REQ-AUTH-007)."""
        await self._secrets.delete(self._name)
        self._issuer = None
        self._source = "configured" if self._configured else None

    # ------------------------------------------------------------------ helpers

    def _drop_foreign_client(self, entry: dict[str, Any], issuer: str) -> None:
        """Tokens of a new issuer arrive: client info registered with the old one goes."""
        info = entry.get("client_info")
        bound = info.get("issuer") if isinstance(info, dict) else None
        if bound is not None and bound != issuer:
            entry["client_info"] = None

    async def _read(self) -> dict[str, Any]:
        """The current entry (validated) or a fresh one; never raises for bad content.

        A secret store that raises (a locked keyring, an unreadable file) is logged at
        WARNING with the exception type only and raised as ``SecretStoreReadError`` (fixed
        message, backend exception not chained — Art. 5)."""
        try:
            entry = await self._secrets.get(self._name)
        except ValueError:  # not JSON / not an object
            entry = None
            self._warn_unreadable()
        except Exception as exc:
            self._log.warning("could not read the stored credentials (%s)", type(exc).__name__)
            raise SecretStoreReadError(self._server_id) from None
        if entry is None:
            self._issuer = None
            return {"v": ENTRY_VERSION}
        if not self._valid(entry):
            self._warn_unreadable()
            self._issuer = None
            return {"v": ENTRY_VERSION}
        if entry["resource"] != self._resource:
            self._log.info("stored OAuth entry belongs to another resource; discarded")
            self._issuer = None
            return {"v": ENTRY_VERSION}
        issuer = entry.get("issuer")
        self._issuer = issuer if isinstance(issuer, str) else None
        source = entry.get("client_id_source")
        if self._configured is None and source in ("cimd", "dcr"):
            self._source = source
        return entry

    @staticmethod
    def _valid(entry: dict[str, Any]) -> bool:
        if entry.get("v") != ENTRY_VERSION or not isinstance(entry.get("resource"), str):
            return False
        issuer = entry.get("issuer")
        if issuer is not None and not isinstance(issuer, str):
            return False
        try:
            if entry.get("tokens") is not None:
                OAuthToken.model_validate(entry["tokens"])
            if entry.get("client_info") is not None:
                OAuthClientInformationFull.model_validate(entry["client_info"])
        except ValidationError:
            return False
        return True

    def _warn_unreadable(self) -> None:
        self._log.warning("stored OAuth entry is unreadable; the Server must be re-authorized")

    async def _write(self, entry: dict[str, Any]) -> None:
        entry["v"] = ENTRY_VERSION
        entry["resource"] = self._resource
        entry.setdefault("issuer", None)
        entry.setdefault("tokens", None)
        entry.setdefault("client_info", None)
        entry.setdefault("client_id_source", self._source)
        entry["updated_at_us"] = time.time_ns() // 1000
        await self._secrets.set(self._name, entry, kind="oauth", server_id=self._server_id)
        self._issuer = entry["issuer"]
        if entry["client_id_source"] is not None:
            self._source = entry["client_id_source"]
