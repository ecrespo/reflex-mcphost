"""``OAuthController``: OAuth 2.1 orchestration around the SDK provider (Flow E, DD-009).

One controller per HTTP Server *without static headers* (REQ-AUTH-009). It owns the SDK
``OAuthClientProvider`` — which does discovery (RFC 9728 → RFC 8414), registration
(configured ``client_id`` → CIMD → DCR), PKCE S256, ``state``, the ``resource`` indicator,
code exchange, issuer binding (SEP-2352) and proactive refresh (Art. 2) — and adds only:

- **Parking.** By default ``redirect_handler`` raises ``AuthorizationDeferredError``: a
  ``401`` ends the attempt and the supervisor sets ``auth_required``; no browser, no
  listener (REQ-AUTH-001).
- **Arming.** ``ConnectionManager.authorize`` binds a ``LoopbackCallbackServer`` and arms
  the controller for one ``AuthorizationFlow``: the redirect URI uses the bound port, the
  authorization URL is published on the flow, and ``callback_handler`` awaits the listener
  within the flow deadline. The listener accepts only an *authentic* callback — our
  ``state`` (constant time) and, unless it is an AS error, a valid ``iss`` (SDK
  ``validate_authorization_response_iss``) and a ``code`` — checked *before* the SDK may
  redeem the code; other callbacks are refused and the wait goes on. An AS error, the
  deadline (reported as ``iss_mismatch`` if a callback with our ``state`` was refused for
  its ``iss``) or cancel raises ``AuthorizationAbortedError`` (REQ-AUTH-006, REQ-AUTH-010).
- **Discovery first** (REQ-AUTH-011): the store withholds stored tokens until the SDK has
  discovered the AS and the issuer binding check passed, so the first attempt after a
  restart is unauthenticated; when it reaches the parked redirect, the tokens the store now
  releases are loaded and the request is replayed once with them (no prompt).
- **Refresh once** (REQ-AUTH-005): a ``401`` on an unexpired, refreshable token (after
  discovery) replays the request once with the in-memory token marked expired so the SDK
  refresh runs at the discovered token endpoint.
- **Attempt outcome.** Each connection attempt gets an ``httpx2.Auth`` (``auth_for``) that
  reports a failed OAuth flow on the ``ObservedTransport`` (``auth_challenge`` +
  ``auth_reason``) and, if the connection was already established (a ``401`` while
  ``ready``), marks the transport closed so the supervisor reconnects.

SDK exception text never leaves this module (only reason codes, Art. 5); the SDK auth
loggers get the redaction filter and an exception-text stripper (``protect_sdk_auth_loggers``,
delta D2). DCR is *deprecated in the pinned spec* and used only as compatibility.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import anyio
import anyio.abc
import httpx2
from mcp.client.auth import (
    OAuthClientProvider,
    OAuthFlowError,
    OAuthRegistrationError,
    OAuthTokenError,
)
from mcp.client.auth.utils import is_valid_client_metadata_url, validate_authorization_response_iss
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from pydantic import AnyUrl

from reflex_mcphost.core.auth.callback_server import (
    LOOPBACK_HOST,
    CallbackResult,
    LoopbackCallbackServer,
)
from reflex_mcphost.core.auth.flow import DEFAULT_FLOW_DEADLINE_S, AuthorizationFlow
from reflex_mcphost.core.auth.token_store import TokenStore
from reflex_mcphost.core.config.models import HttpTransport, ServerDefinition
from reflex_mcphost.core.errors import AuthorizationReason, MCPHostError
from reflex_mcphost.core.logging import ContextAdapter, SecretRedactionFilter, get_logger
from reflex_mcphost.core.secrets import register_secret

if TYPE_CHECKING:
    from reflex_mcphost.core.connection.transports import ObservedTransport

__all__ = [
    "AUTH_REQUIRED",
    "CLIENT_ID_REQUIRED",
    "SDK_AUTH_LOGGERS",
    "AuthorizationAbortedError",
    "AuthorizationDeferredError",
    "OAuthController",
    "protect_sdk_auth_loggers",
]

AUTH_REQUIRED = "auth_required"
CLIENT_ID_REQUIRED = "client_id_required"
SDK_AUTH_LOGGERS = (
    "mcp.client.auth",
    "mcp.client.auth.oauth2",
    "mcp.client.auth.utils",
    "mcp.client.auth.extensions.client_credentials",
    "mcp.client.auth.extensions.identity_assertion",
)
"""Every ``mcp.client.auth*`` module logger (pinned by the SDK auth contract test)."""
_log = get_logger(__name__)


class AuthorizationDeferredError(MCPHostError):
    """Raised by the parked ``redirect_handler``: authorization waits for ``authorize()``."""

    def __init__(self) -> None:
        super().__init__("authorization deferred until authorize() is called")


class AuthorizationAbortedError(MCPHostError):
    """The Host aborted the flow before the code was redeemed (reason code only)."""

    def __init__(self, reason: AuthorizationReason) -> None:
        self.reason: AuthorizationReason = reason
        super().__init__(f"authorization aborted: {reason}")


# ---------------------------------------------------------------------- SDK log hygiene


class _ExceptionTextStripper(logging.Filter):
    """Delta D2: SDK auth records with an exception keep only the exception *type* name
    (``OAuthFlowError`` text may carry ``state`` or a token response). Records about the
    Host's own parking signal are dropped: they are control flow, not errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info:
            return True
        exc = record.exc_info[1]
        if isinstance(exc, AuthorizationDeferredError):
            return False
        record.msg = type(exc).__name__ if exc is not None else "exception"
        record.args = None
        record.exc_info = None
        record.exc_text = None
        return True


_STRIPPER = _ExceptionTextStripper()
_REDACT = SecretRedactionFilter()


def protect_sdk_auth_loggers() -> None:
    """Attach the stripper and the redaction filter to every SDK auth logger (idempotent;
    installs no handler)."""
    for name in SDK_AUTH_LOGGERS:
        logger = logging.getLogger(name)
        if _STRIPPER not in logger.filters:
            logger.addFilter(_STRIPPER)
        if _REDACT not in logger.filters:
            logger.addFilter(_REDACT)


# ---------------------------------------------------------------------- controller


def _loopback_port(uri: object) -> int:
    parts = urlsplit(str(uri))
    if parts.hostname == LOOPBACK_HOST and parts.port:
        return int(parts.port)
    return 0


async def _first(*waits: Callable[[], Awaitable[object]]) -> int:
    """Index of the first of ``waits`` to return (the others are cancelled)."""
    winner = -1
    async with anyio.create_task_group() as tg:

        async def run(index: int, fn: Callable[[], Awaitable[object]]) -> None:
            nonlocal winner
            await fn()
            if winner < 0:
                winner = index
            tg.cancel_scope.cancel()

        for index, fn in enumerate(waits):
            tg.start_soon(run, index, fn)
    return winner


class OAuthController:
    """OAuth orchestration for one HTTP Server (Flow E). Not thread-safe (API §3.1)."""

    def __init__(
        self,
        definition: ServerDefinition,
        store: TokenStore,
        *,
        cimd_url: str | None,
        deadline_s: float = DEFAULT_FLOW_DEADLINE_S,
    ) -> None:
        transport = definition.transport
        if not isinstance(transport, HttpTransport) or transport.headers:
            raise ValueError("OAuth applies only to HTTP Servers without static headers")
        self.server_id = definition.id
        self.server_url = str(transport.url)
        self.store = store
        self.deadline_s = deadline_s
        """Whole-flow deadline (REQ-AUTH-006); tests inject a short one."""
        settings = transport.oauth
        self._scope = (
            " ".join(settings.scopes) if settings is not None and settings.scopes else None
        )
        self._cimd_url = cimd_url if is_valid_client_metadata_url(cimd_url) else None
        self._flow: AuthorizationFlow | None = None
        self._listener: LoopbackCallbackServer | None = None
        self._expected_state: str | None = None
        self._rejection: AuthorizationReason | None = None
        self._redirect_port = 0
        self.status_reason: str | None = None
        """``client_id_required`` when registration is impossible (REQ-AUTH-003)."""
        self._log = ContextAdapter(_log, server_id=definition.id)
        self.provider = self._new_provider()
        store.bind_issuer_source(self._metadata_issuer)

    def __repr__(self) -> str:
        return f"OAuthController(server_id={self.server_id!r})"

    def _new_provider(self) -> OAuthClientProvider:
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(self._redirect_uri(self._redirect_port))],
            scope=self._scope,
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="none",  # noqa: S106 - a method name, not a secret
            application_type="native",
            client_name="reflex-mcphost",
        )
        return OAuthClientProvider(
            self.server_url,
            metadata,
            storage=self.store,
            redirect_handler=self._on_redirect,
            callback_handler=self._on_callback,
            client_metadata_url=self._cimd_url,
        )

    @staticmethod
    def _redirect_uri(port: int) -> str:
        netloc = f"{LOOPBACK_HOST}:{port}" if port else LOOPBACK_HOST
        return f"http://{netloc}/callback"

    def _set_redirect(self, port: int) -> None:
        self._redirect_port = port
        self.provider.context.client_metadata.redirect_uris = [AnyUrl(self._redirect_uri(port))]

    def _metadata_issuer(self) -> str | None:
        metadata = self.provider.context.oauth_metadata
        return str(metadata.issuer) if metadata is not None else None

    # ------------------------------------------------------------------ manager API

    @property
    def flow(self) -> AuthorizationFlow | None:
        return self._flow

    def new_flow(self) -> AuthorizationFlow:
        return AuthorizationFlow(self.server_id, deadline_s=self.deadline_s)

    async def preferred_port(self) -> int:
        """A DCR client re-binds its registered loopback port when free (Flow E step 2)."""
        info = await self.store.get_client_info()
        if info is not None and self.store.client_id_source == "dcr":
            for uri in info.redirect_uris or ():
                port = _loopback_port(uri)
                if port:
                    return port
        return self._redirect_port

    def arm(self, flow: AuthorizationFlow, listener: LoopbackCallbackServer) -> None:
        """Use ``listener`` for ``flow``: the next ``401`` runs the browser flow."""
        self._flow = flow
        self._listener = listener
        self._expected_state = None
        self._rejection = None
        self._set_redirect(listener.port)
        listener.validator = self._authentic

    def disarm(self, flow: AuthorizationFlow) -> None:
        """Back to parked (the flow ended); the verifier dies with the SDK frame."""
        if self._flow is flow:
            self._flow = None
            self._listener = None
            self._expected_state = None

    async def sign_out(self) -> None:
        """Delete stored credentials and forget the in-memory ones (REQ-AUTH-007)."""
        await self.store.clear()
        self.status_reason = None
        self.provider = self._new_provider()

    def auth_for(self, observed: ObservedTransport) -> httpx2.Auth:
        """The ``httpx2`` auth of one connection attempt (reports onto ``observed``)."""
        return _AttemptAuth(self, observed)

    # ------------------------------------------------------------------ SDK handlers

    async def _on_redirect(self, authorization_url: str) -> None:
        flow = self._flow
        if flow is None or flow.done:
            raise AuthorizationDeferredError()
        if flow.cancelled.is_set():
            raise AuthorizationAbortedError("cancelled")
        state = parse_qs(urlsplit(authorization_url).query).get("state", [None])[0]
        if state:
            register_secret(state)
        self._expected_state = state
        self._log.info("authorization URL ready; waiting for the browser")
        flow.set_url(authorization_url)

    async def _on_callback(self) -> AuthorizationCodeResult:
        flow, listener = self._flow, self._listener
        if flow is None or listener is None:
            raise AuthorizationAbortedError("flow_error")
        result: CallbackResult | None = None

        async def receive() -> None:
            nonlocal result
            result = await listener.result()

        with anyio.move_on_after(max(0.0, flow.deadline - anyio.current_time())):
            winner = await _first(receive, flow.cancelled.wait, flow.finished.wait)
            if winner != 0:
                raise AuthorizationAbortedError("cancelled")
        if result is None:  # deadline: report why a callback carrying our state was refused
            raise AuthorizationAbortedError(self._rejection or "timeout")
        reason = self._check(result)
        if reason is not None:
            raise AuthorizationAbortedError(reason)
        assert result.code is not None  # noqa: S101 - `_check` guarantees it
        return AuthorizationCodeResult(code=result.code, state=result.state, iss=result.iss)

    def _state_matches(self, state: str | None) -> bool:
        expected = self._expected_state
        return (
            expected is not None
            and state is not None
            and secrets.compare_digest(state.encode(), expected.encode())
        )

    def _authentic(self, result: CallbackResult) -> bool:
        """Listener validator: only a callback carrying our ``state`` that is an AS error or
        passes every pre-redemption check ends the flow; anything else is refused and the
        listener keeps waiting (a forged callback can neither end nor hijack the flow). A
        refusal with our ``state`` (``iss`` mismatch) is remembered for the deadline."""
        if not self._state_matches(result.state):
            return False
        if result.error is not None:
            return True
        reason = self._check(result)
        if reason is not None:
            self._rejection = reason
            return False
        return True

    def _check(self, result: CallbackResult) -> AuthorizationReason | None:
        """Pre-redemption checks (Flow E step 4), in order: AS error, ``state``, ``iss``."""
        if result.error is not None:
            return "access_denied"
        if not self._state_matches(result.state):
            return "state_mismatch"
        try:
            validate_authorization_response_iss(result.iss, self.provider.context.oauth_metadata)
        except OAuthFlowError:
            return "iss_mismatch"
        if not result.code:
            return "flow_error"
        return None

    # ------------------------------------------------------------------ attempt outcome

    def _classify(self, exc: BaseException) -> AuthorizationReason:
        context = self.provider.context
        if isinstance(exc, AuthorizationAbortedError):
            return exc.reason
        if isinstance(exc, OAuthRegistrationError):
            metadata = context.oauth_metadata
            if metadata is None or metadata.registration_endpoint is None:
                return "client_id_required"
            return "registration_failed"
        if isinstance(exc, OAuthTokenError):
            return "token_error"
        if context.oauth_metadata is None:
            return "discovery_failed"
        return "flow_error"

    def _failed(self, exc: BaseException) -> None:
        """An attempt's OAuth flow raised: record the reason (never the SDK text)."""
        if isinstance(exc, AuthorizationDeferredError):
            self._log.info("server requires authorization (parked until authorize())")
            return
        reason = self._classify(exc)
        self.status_reason = CLIENT_ID_REQUIRED if reason == "client_id_required" else None
        if reason == "client_id_required":
            self._log.warning(
                "no client id: the authorization server supports neither CIMD nor DCR; "
                "set [servers.%s.oauth] client_id in mcphost.toml",
                self.server_id,
            )
        else:
            self._log.warning("authorization failed: %s (%s)", reason, type(exc).__name__)
        flow = self._flow
        if flow is not None and not flow.done:
            flow.failure = reason

    def _succeeded(self) -> None:
        self.status_reason = None

    async def _prepare(self) -> None:
        """Parked with no concrete redirect yet: reserve a free loopback port so a DCR
        registration (if the SDK needs one) records a port ``authorize`` can re-bind."""
        if self._redirect_port or self._flow is not None:
            return
        listener = await anyio.create_tcp_listener(local_host=LOOPBACK_HOST, local_port=0)
        try:
            local_port = anyio.abc.SocketAttribute.local_port
            port = int(listener.listeners[0].extra(local_port))  # noqa: S610 - anyio
        finally:
            await listener.aclose()
        self._set_redirect(port)

    def _refreshable_unexpired(self) -> bool:
        context = self.provider.context
        return (
            context.oauth_metadata is not None  # never before the issuer binding check
            and context.is_token_valid()
            and context.can_refresh_token()
        )

    async def _reload_bound_tokens(self) -> bool:
        """Discovery just bound the issuer (the SDK reached the parked redirect): load the
        stored tokens the store releases for it. ``True`` when there are any — the attempt
        is then replayed with them instead of asking for authorization."""
        context = self.provider.context
        if context.oauth_metadata is None or context.current_tokens is not None:
            return False
        tokens = await self.store.get_tokens()
        if tokens is None:
            return False
        context.current_tokens = tokens
        context.token_expiry_time = None  # unknown after a restart: a 401 triggers refresh
        return True

    def _mark_expired(self) -> None:
        """Make the SDK see the in-memory token as expired so its refresh runs."""
        self.provider.context.token_expiry_time = time.time() - 1.0

    def _drop_foreign_tokens(self) -> None:
        """Belt and braces for REQ-AUTH-011: in-memory tokens bound (by the store) to an
        issuer other than the discovered one are never presented or refreshed."""
        issuer, bound = self._metadata_issuer(), self.store.issuer
        if issuer is not None and bound is not None and issuer != bound:
            self.provider.context.clear_tokens()


def _is_refresh(request: httpx2.Request) -> bool:
    return request.method == "POST" and b"grant_type=refresh_token" in request.content


class _AttemptAuth(httpx2.Auth):
    """Drives the controller's SDK provider for one attempt and reports its outcome."""

    def __init__(self, controller: OAuthController, observed: ObservedTransport) -> None:
        self._controller = controller
        self._observed = observed

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        controller = self._controller
        final: httpx2.Response | None = None
        replayed = reloaded = False
        try:
            await controller._prepare()
            while True:
                controller._drop_foreign_tokens()
                flow = controller.provider.async_auth_flow(request)
                restart = False
                try:
                    outgoing = await flow.__anext__()
                    while True:
                        main = outgoing is request
                        eligible = main and not replayed and controller._refreshable_unexpired()
                        response = yield outgoing
                        if not main and _is_refresh(outgoing) and response.status_code != 200:
                            await controller.store.drop_tokens()  # dead: never reload them
                        if main:
                            final = response
                            if response.status_code == 401 and eligible:
                                replayed = restart = True  # REQ-AUTH-005: refresh once
                                controller._mark_expired()
                                break
                        outgoing = await flow.asend(response)
                except StopAsyncIteration:
                    pass
                except AuthorizationDeferredError:
                    # Discovery-first: stored tokens are withheld until discovery bound the
                    # issuer (REQ-AUTH-011); now replay once with the ones it releases.
                    if reloaded or not await controller._reload_bound_tokens():
                        raise
                    reloaded = restart = True
                finally:
                    await flow.aclose()
                if not restart:
                    break
        except Exception as exc:
            controller._failed(exc)
            self._unauthorized()
            raise
        if final is not None and final.status_code == 401:
            self._unauthorized()
        elif final is not None and final.is_success:
            controller._succeeded()
            self._observed.auth_challenge = False

    def _unauthorized(self) -> None:
        observed = self._observed
        observed.auth_challenge = True
        observed.auth_reason = self._controller.status_reason
        if observed.established:  # a 401 while ready: reconnect, land in auth_required
            observed.mark_closed()
