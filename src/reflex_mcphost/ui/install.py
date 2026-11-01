"""``install(app, host_factory)``: one Host per backend process, guarded by origin (DD-011, DD-017).

``install`` registers, with ``app.register_lifespan_task``, an async context manager that
resolves the single allowed origin from the Reflex config's ``deploy_url``, builds the Host
with ``host_factory()``, binds the process-wide ``ReflexPrompter`` (replacing any prompter
the factory set), enters ``async with host`` before Reflex serves, publishes it in
``HostRuntime`` and exits it after Reflex stops serving. Reflex enters and exits such tasks
on its ``AsyncExitStack`` in one task and without the 5 s cancel cap it applies to
coroutine tasks, so ``Host.start()`` and ``Host.aclose()`` share a task (API §4.1) and
``aclose()`` keeps its 15 s budget (REQ-CON-009).

``LoopbackGuard`` (REQ-NFR-011) is a pure-ASGI middleware appended to ``app.api_transformer``
(after any transformer the embedding app set, so it is outermost): an HTTP request whose
``Host`` is not ``<host>:<port>`` or whose ``Origin`` is present and not
``http://<host>:<port>`` gets a ``403`` whose HTML body names the origin to open; such a
websocket is closed before accept. Until the lifespan resolved the origin nothing passes.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import AsyncIterator, Callable, MutableMapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.ui.prompter import PROMPTER

if TYPE_CHECKING:
    import reflex as rx

    from reflex_mcphost.core.host import Host

__all__ = [
    "UNSAFE_ALLOW_REMOTE_ENV",
    "AllowedOrigin",
    "GuardTransformer",
    "HostRuntime",
    "LoopbackGuard",
    "allowed_origin",
    "install",
]

UNSAFE_ALLOW_REMOTE_ENV = "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE"
"""Set to ``1`` (by ``reflex-mcphost run --unsafe-allow-remote``) to accept a non-loopback
``deploy_url`` host (REQ-UI-002)."""

_LOOPBACK = "127.0.0.1"
_INSTALLED_ATTR = "_reflex_mcphost_host_factory"
_log = get_logger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Any]
Send = Callable[[MutableMapping[str, Any]], Any]
ASGIApp = Callable[[Scope, Receive, Send], Any]
Phase = Literal["stopped", "starting", "running"]


# ------------------------------------------------------------------ origin (DD-017)


@dataclass(frozen=True)
class AllowedOrigin:
    """The single ``Host`` / ``Origin`` the backend accepts (REQ-NFR-011)."""

    host: str
    port: int

    @property
    def host_header(self) -> str:
        """The accepted ``Host`` header value, ``<host>:<port>``."""
        name = f"[{self.host}]" if ":" in self.host else self.host
        return f"{name}:{self.port}"

    @property
    def origin(self) -> str:
        """The accepted ``Origin`` header value, ``http://<host>:<port>``."""
        return f"http://{self.host_header}"

    @property
    def url(self) -> str:
        """The address the user should open."""
        return f"{self.origin}/"


def allowed_origin(deploy_url: str | None, *, allow_remote: bool) -> AllowedOrigin:
    """Parse ``deploy_url`` into the allowed origin, or raise ``RuntimeError`` (start-up).

    Refused: a missing URL, a scheme other than ``http``, a missing port, ``localhost``
    (always: reserved for the MCP Apps sandbox proxy), a wildcard host and — unless
    ``allow_remote`` — any host other than ``127.0.0.1`` (DD-017).
    """
    if not deploy_url:
        raise RuntimeError("reflex_mcphost.ui: the Reflex config has no deploy_url")
    parts = urlsplit(deploy_url)
    try:
        port = parts.port
    except ValueError:
        port = None
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or port is None or not host:
        raise RuntimeError(
            f"reflex_mcphost.ui: deploy_url must be http://127.0.0.1:<port> (got {deploy_url!r})"
        )
    if host == "localhost" or host.endswith(".localhost"):
        raise RuntimeError(
            "reflex_mcphost.ui: deploy_url must not use localhost (reserved for the MCP Apps "
            "sandbox proxy); use http://127.0.0.1:<port>"
        )
    if _is_wildcard(host):
        raise RuntimeError(
            f"reflex_mcphost.ui: deploy_url host {host!r} is a wildcard; the guard needs one "
            "concrete origin"
        )
    if host != _LOOPBACK and not allow_remote:
        raise RuntimeError(
            f"reflex_mcphost.ui: deploy_url host {host!r} is not 127.0.0.1; set "
            f"{UNSAFE_ALLOW_REMOTE_ENV}=1 (--unsafe-allow-remote) to serve it remotely"
        )
    return AllowedOrigin(host, port)


def _is_wildcard(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _reflex_settings() -> tuple[str | None, tuple[str, ...]]:
    """``(deploy_url, cors_allowed_origins)`` of the current Reflex config."""
    from reflex.config import get_config

    config = get_config()
    return config.deploy_url, tuple(config.cors_allowed_origins)


def _resolve_origin() -> AllowedOrigin:
    deploy_url, cors = _reflex_settings()
    origin = allowed_origin(deploy_url, allow_remote=os.environ.get(UNSAFE_ALLOW_REMOTE_ENV) == "1")
    if origin.host != _LOOPBACK:
        _log.warning("serving the Host UI remotely at %s (unsafe-allow-remote)", origin.url)
    broader = [value for value in cors if value != origin.origin]
    if broader:
        _log.warning(
            "cors_allowed_origins allows %s besides %s; the guard still accepts only %s",
            ", ".join(sorted(broader)),
            origin.origin,
            origin.origin,
        )
    return origin


# ------------------------------------------------------------------ the guard (REQ-NFR-011)


def _single(scope: Scope, name: bytes) -> str | Literal[False] | None:
    """The header's value, ``None`` when absent, ``False`` when repeated (ambiguous)."""
    values = [v for k, v in scope.get("headers", ()) if k.lower() == name]
    if len(values) > 1:
        return False
    return values[0].decode("latin-1") if values else None


class LoopbackGuard:
    """Pure-ASGI Host/Origin guard; ``origin_of()`` is ``None`` until start-up resolved it."""

    def __init__(self, app: ASGIApp, origin_of: Callable[[], AllowedOrigin | None]) -> None:
        self.app = app
        self._origin_of = origin_of

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        origin = self._origin_of()
        if origin is not None and self._allowed(scope, origin):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        await self._refuse(send, origin)

    @staticmethod
    def _allowed(scope: Scope, origin: AllowedOrigin) -> bool:
        host = _single(scope, b"host")
        if not isinstance(host, str) or host.lower() != origin.host_header:
            return False
        sent_origin = _single(scope, b"origin")
        if sent_origin is False:
            return False
        return sent_origin is None or sent_origin.lower() == origin.origin

    @staticmethod
    async def _refuse(send: Send, origin: AllowedOrigin | None) -> None:
        if origin is None:
            status, title, text = 503, "Starting", "The Host UI is starting; reload shortly."
        else:
            url = escape(origin.url)
            status, title = 403, "Wrong address"
            text = f'Open {url}</p><p><a href="{url}">{url}</a>'
        body = (
            f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body><p>{text}</p></body></html>"
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class GuardTransformer:
    """The ``api_transformer`` entry ``install()`` appends: wraps the backend in the guard."""

    def __init__(self) -> None:
        self.origin: AllowedOrigin | None = None

    def __call__(self, app: ASGIApp) -> ASGIApp:
        return LoopbackGuard(app, lambda: self.origin)


# ------------------------------------------------------------------ the Host (DD-011)


class _HostRuntime:
    """Process-wide holder of the running Host (one per backend process)."""

    def __init__(self) -> None:
        self._host: Host | None = None
        self._phase: Phase = "stopped"

    @property
    def phase(self) -> Phase:
        """``starting`` (lifespan entered), ``running`` (Host published) or ``stopped``."""
        return self._phase

    @property
    def host(self) -> Host:
        """The running Host; ``RuntimeError`` before start-up and after shutdown."""
        if self._host is None:
            raise RuntimeError(f"the Host is not running ({self._phase})")
        return self._host

    def _begin(self) -> None:
        if self._phase != "stopped":
            raise RuntimeError("a Host lifespan is already running in this process")
        self._phase = "starting"

    def _publish(self, host: Host) -> None:
        self._host, self._phase = host, "running"

    def _end(self) -> None:
        self._host, self._phase = None, "stopped"


HostRuntime = _HostRuntime()
"""The running Host of this backend process (``HostRuntime.host``, ``HostRuntime.phase``)."""


def install(app: rx.App, host_factory: Callable[[], Host]) -> None:
    """Make ``app`` own one Host for its lifespan and guard its backend (API §8).

    Idempotent per ``rx.App``; a second call with a different ``host_factory`` raises
    ``RuntimeError``. Registering after the lifespan started raises (Reflex's rule).
    """
    current = getattr(app, _INSTALLED_ATTR, None)
    if current is not None:
        if current is not host_factory:
            raise RuntimeError(
                "reflex_mcphost.ui.install(): app already has a different host_factory"
            )
        return
    guard = GuardTransformer()

    @asynccontextmanager
    async def reflex_mcphost_host() -> AsyncIterator[None]:
        HostRuntime._begin()  # raises while another Host lifespan runs
        try:
            guard.origin = _resolve_origin()
            host = host_factory()
            host.set_prompter(PROMPTER)
            async with host:
                HostRuntime._publish(host)
                try:
                    yield
                finally:
                    HostRuntime._end()
        finally:
            guard.origin = None
            HostRuntime._end()

    app.register_lifespan_task(reflex_mcphost_host)
    app.api_transformer = _append(app.api_transformer, guard)
    setattr(app, _INSTALLED_ATTR, host_factory)


def _append(existing: Any, guard: GuardTransformer) -> Any:
    if existing is None:
        return guard
    if isinstance(existing, Sequence):
        return [*existing, guard]
    return [existing, guard]
