"""Fixture OAuth 2.1 authorization server (Tasks F4-09a; test-only code, Constitution Art. 1).

A small Starlette app run by uvicorn in a background thread on ``127.0.0.1:<ephemeral>``.
It serves two issuers on one port — ``http://127.0.0.1:<port>`` and
``http://127.0.0.1:<port>/tenant2`` — so a test can switch the *current* issuer (the one the
protected fixtures advertise) and watch re-registration (REQ-AUTH-011). Per issuer:

- RFC 8414 metadata at ``/.well-known/oauth-authorization-server[/tenant2]``;
- ``/authorize`` auto-approves: ``302`` to the redirect URI with ``code``, ``state`` and
  (``iss_supported``) ``iss``; an unknown client or unregistered redirect gets ``400`` and no
  redirect; ``deny`` answers ``error=access_denied``;
- ``/token``: ``authorization_code`` (PKCE S256 verified, ``resource`` and ``redirect_uri``
  checked, client secret checked for confidential clients) and ``refresh_token``;
- ``/register`` (RFC 7591, only while ``dcr``);
- ``/introspect`` for the protected fixtures (``active`` only for live tokens of the current
  issuer); ``/_fixture/issuer`` returns the current issuer.

CIMD: the default ``oauth_client_metadata_url`` is resolved from the repository's
``docs/oauth/client.json`` (no network); its portless loopback redirect matches any port
only while ``port_variance`` is on (RFC 8252 §7.3). Toggles are plain attributes of
``AuthServerState``. ``journal`` records each endpoint call with the client id and the
*names* of the parameters present; ``seen_secrets`` keeps every code, verifier, token and
client secret handed out or received, for redaction tests. ``introspect`` entries record
whether the token was active and the issuer it was issued by; ``prm`` entries mark a
protected fixture serving its resource metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

CIMD_DOCUMENT = Path(__file__).resolve().parents[3] / "docs" / "oauth" / "client.json"
TENANT2 = "/tenant2"
START_TIMEOUT_S = 10.0


def _token(kind: str) -> str:
    return f"{kind}-secret-{secrets.token_hex(16)}"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _loopback_match(requested: str, registered: str, *, port_variance: bool) -> bool:
    """RFC 8252 §7.3: a loopback redirect may vary only in its port (when allowed)."""
    if requested == registered:
        return True
    a, b = urlsplit(requested), urlsplit(registered)
    if not port_variance or a.hostname != "127.0.0.1" or b.hostname != "127.0.0.1":
        return False
    return (a.scheme, a.path, a.query) == (b.scheme, b.path, b.query)


@dataclass
class Client:
    client_id: str
    issuer: str | None  # None: accepted by every issuer (CIMD, pre-registered)
    redirect_uris: list[str]
    secret: str | None = None
    application_type: str | None = None


@dataclass
class Grant:
    """An issued authorization code or token."""

    issuer: str
    client_id: str
    redirect_uri: str = ""
    challenge: str = ""
    resource: str | None = None
    expires_at: float = 0.0
    revoked: bool = False


@dataclass
class AuthServerState:
    """Toggles, registrations, issued grants and the request journal."""

    base: str = ""
    cimd: bool = True
    dcr: bool = True
    iss_supported: bool = True
    iss_override: str | None = None  # sent as `iss` instead of the issuer (mix-up)
    omit_iss: bool = False  # drop `iss` even when advertised
    deny: bool = False  # answer /authorize with error=access_denied
    current: str = ""  # path of the current issuer: "" or TENANT2
    access_ttl_s: int = 3600
    reject_access_tokens: bool = False  # introspection rejects every access token
    port_variance: bool = True
    clients: dict[str, Client] = field(default_factory=dict)
    codes: dict[str, Grant] = field(default_factory=dict)
    access: dict[str, Grant] = field(default_factory=dict)
    refresh: dict[str, Grant] = field(default_factory=dict)
    journal: list[dict[str, Any]] = field(default_factory=list)
    seen_secrets: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------ test API

    @property
    def issuer(self) -> str:
        """The current issuer (advertised by the protected fixtures)."""
        return self.base + self.current

    def issuer_of(self, path: str) -> str:
        return self.base + path

    def switch_issuer(self, path: str = TENANT2) -> None:
        self.current = path

    def preregister(self, client_id: str, secret: str | None = None) -> None:
        """A client known to every issuer, any loopback ``/callback`` port (configured id)."""
        self.clients[client_id] = Client(
            client_id, None, ["http://127.0.0.1/callback"], secret=secret
        )
        if secret:
            self.seen_secrets.append(secret)

    def revoke_access_tokens(self) -> None:
        """Reject every access token issued so far (refreshed ones are accepted)."""
        for grant in self.access.values():
            grant.revoked = True

    def expire_access_tokens(self) -> None:
        """Make every access token issued so far expired (as if its lifetime had passed)."""
        for grant in self.access.values():
            grant.expires_at = 0.0

    def revoke_refresh_tokens(self) -> None:
        for grant in self.refresh.values():
            grant.revoked = True

    def requests(self, endpoint: str, issuer: str | None = None) -> list[dict[str, Any]]:
        return [
            r
            for r in self.journal
            if r["endpoint"] == endpoint and (issuer is None or r["issuer"] == issuer)
        ]

    def active(self, token: str) -> bool:
        grant = self.access.get(token)
        return (
            grant is not None
            and not grant.revoked
            and not self.reject_access_tokens
            and grant.issuer == self.issuer
            and time.time() < grant.expires_at
        )

    # ------------------------------------------------------------------ internals

    def record(self, endpoint: str, issuer: str, params: dict[str, Any], **extra: Any) -> None:
        with self.lock:
            self.journal.append(
                {
                    "endpoint": endpoint,
                    "issuer": issuer,
                    "client_id": params.get("client_id"),
                    "params": sorted(params),
                    **extra,
                }
            )

    def client(self, client_id: str, issuer: str) -> Client | None:
        if self.cimd and client_id == _cimd_client_id():
            doc = json.loads(CIMD_DOCUMENT.read_text(encoding="utf-8"))
            return Client(client_id, None, list(doc["redirect_uris"]))
        client = self.clients.get(client_id)
        if client is None or client.issuer not in (None, issuer):
            return None
        return client

    def issue(self, issuer: str, client_id: str, resource: str | None) -> dict[str, Any]:
        access, refresh = _token("access"), _token("refresh")
        expires = time.time() + self.access_ttl_s
        self.access[access] = Grant(issuer, client_id, resource=resource, expires_at=expires)
        self.refresh[refresh] = Grant(issuer, client_id, resource=resource)
        self.seen_secrets += [access, refresh]
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.access_ttl_s,
            "refresh_token": refresh,
        }


def _cimd_client_id() -> str:
    return str(json.loads(CIMD_DOCUMENT.read_text(encoding="utf-8"))["client_id"])


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})


def build_app(state: AuthServerState) -> Starlette:
    def metadata(path: str) -> Any:
        async def endpoint(request: Request) -> Response:
            issuer = state.issuer_of(path)
            state.record("metadata", issuer, {})
            body: dict[str, Any] = {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                "client_id_metadata_document_supported": state.cimd,
                "authorization_response_iss_parameter_supported": state.iss_supported,
            }
            if state.dcr:
                body["registration_endpoint"] = f"{issuer}/register"
            return JSONResponse(body)

        return endpoint

    def authorize(path: str) -> Any:
        async def endpoint(request: Request) -> Response:
            issuer = state.issuer_of(path)
            params = dict(request.query_params)
            state.record("authorize", issuer, params, redirect_uri=params.get("redirect_uri"))
            client = state.client(params.get("client_id", ""), issuer)
            redirect = params.get("redirect_uri", "")
            if client is None or not any(
                _loopback_match(redirect, r, port_variance=state.port_variance)
                for r in client.redirect_uris
            ):
                return PlainTextResponse("invalid client or redirect_uri", status_code=400)
            query: dict[str, str] = {}
            if state.deny:
                query["error"] = "access_denied"
            elif params.get("response_type") != "code" or (
                params.get("code_challenge_method") != "S256" or not params.get("code_challenge")
            ):
                query["error"] = "invalid_request"
            else:
                code = _token("code")
                state.seen_secrets.append(code)
                state.codes[code] = Grant(
                    issuer,
                    client.client_id,
                    redirect_uri=redirect,
                    challenge=params["code_challenge"],
                    resource=params.get("resource"),
                    expires_at=time.time() + 300,
                )
                query["code"] = code
            if "state" in params:
                query["state"] = params["state"]
            if state.iss_override is not None:
                query["iss"] = state.iss_override
            elif state.iss_supported and not state.omit_iss:
                query["iss"] = issuer
            return RedirectResponse(f"{redirect}?{urlencode(query)}", status_code=302)

        return endpoint

    def token(path: str) -> Any:
        async def endpoint(request: Request) -> Response:
            issuer = state.issuer_of(path)
            form = {k: str(v) for k, v in (await request.form()).items()}
            state.record("token", issuer, form, grant_type=form.get("grant_type"))
            for name in ("code", "code_verifier", "refresh_token", "client_secret"):
                if name in form:
                    state.seen_secrets.append(form[name])
            client = state.client(form.get("client_id", ""), issuer)
            if client is None:
                return _error(401, "invalid_client")
            if client.secret is not None and form.get("client_secret") != client.secret:
                return _error(401, "invalid_client")
            grant_type = form.get("grant_type")
            if grant_type == "authorization_code":
                grant = state.codes.pop(form.get("code", ""), None)
                if (
                    grant is None
                    or grant.issuer != issuer
                    or grant.client_id != client.client_id
                    or grant.redirect_uri != form.get("redirect_uri")
                    or time.time() > grant.expires_at
                    or _s256(form.get("code_verifier", "")) != grant.challenge
                    or (grant.resource is not None and form.get("resource") != grant.resource)
                ):
                    return _error(400, "invalid_grant")
                return JSONResponse(
                    state.issue(issuer, client.client_id, grant.resource),
                    headers={"Cache-Control": "no-store"},
                )
            if grant_type == "refresh_token":
                grant = state.refresh.get(form.get("refresh_token", ""))
                if (
                    grant is None
                    or grant.revoked
                    or grant.issuer != issuer
                    or grant.client_id != client.client_id
                ):
                    return _error(400, "invalid_grant")
                access = _token("access")
                state.seen_secrets.append(access)
                state.access[access] = Grant(
                    issuer,
                    client.client_id,
                    resource=grant.resource,
                    expires_at=time.time() + state.access_ttl_s,
                )
                return JSONResponse(
                    {
                        "access_token": access,
                        "token_type": "Bearer",
                        "expires_in": state.access_ttl_s,
                    },
                    headers={"Cache-Control": "no-store"},
                )
            return _error(400, "unsupported_grant_type")

        return endpoint

    def register(path: str) -> Any:
        async def endpoint(request: Request) -> Response:
            issuer = state.issuer_of(path)
            body = await request.json()
            state.record(
                "register",
                issuer,
                body,
                application_type=body.get("application_type"),
                redirect_uris=body.get("redirect_uris"),
            )
            if not state.dcr:
                return PlainTextResponse("not found", status_code=404)
            client_id = f"dcr-{secrets.token_hex(8)}"
            uris = [str(u) for u in body.get("redirect_uris") or []]
            state.clients[client_id] = Client(
                client_id, issuer, uris, application_type=body.get("application_type")
            )
            return JSONResponse(
                {
                    "client_id": client_id,
                    "client_id_issued_at": int(time.time()),
                    "redirect_uris": uris,
                    "token_endpoint_auth_method": "none",
                    "grant_types": body.get("grant_types", ["authorization_code"]),
                    "response_types": ["code"],
                    "application_type": body.get("application_type"),
                },
                status_code=201,
            )

        return endpoint

    async def introspect(request: Request) -> Response:
        form = await request.form()
        token = str(form.get("token", ""))
        active = state.active(token)
        grant = state.access.get(token)
        state.record(
            "introspect",
            state.issuer,
            {},
            active=active,
            token_issuer=grant.issuer if grant is not None else None,
            host=str(form.get("host", "")),  # the Host header the protected fixture saw
        )
        return JSONResponse({"active": active})

    async def current_issuer(request: Request) -> Response:
        state.record("prm", state.issuer, {})  # a protected fixture is serving its PRM
        return JSONResponse({"issuer": state.issuer})

    routes = [
        Route("/introspect", introspect, methods=["POST"]),
        Route("/_fixture/issuer", current_issuer),
    ]
    for path in ("", TENANT2):
        routes += [
            Route(f"/.well-known/oauth-authorization-server{path}", metadata(path)),
            Route(f"{path}/authorize", authorize(path)),
            Route(f"{path}/token", token(path), methods=["POST"]),
            Route(f"{path}/register", register(path), methods=["POST"]),
        ]
    return Starlette(routes=routes)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@contextmanager
def running_auth_server(**toggles: Any) -> Iterator[AuthServerState]:
    """Run the fixture AS in a background thread; yields its state (toggles writable)."""
    port = _free_port()
    state = AuthServerState(base=f"http://127.0.0.1:{port}", **toggles)
    config = uvicorn.Config(build_app(state), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="fixture-auth-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("fixture authorization server did not start")
        time.sleep(0.01)
    try:
        yield state
    finally:
        server.should_exit = True
        thread.join(timeout=5)
