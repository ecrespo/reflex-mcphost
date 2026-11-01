"""The MCP Apps sandbox proxy listener and the origin-pair check (TD DD-012, API §7).

``SandboxProxy`` is a minimal HTTP/1.1 listener (``h11`` over ``anyio``, no ASGI server)
that serves ``proxy.html`` on ``http://localhost:<port>`` — a distinct origin from the Host
UI's ``http://127.0.0.1:<ui_port>`` (different host *and* port, so the proxy's
``allow-same-origin`` reaches neither the UI's storage nor its cookies). It binds one
ephemeral port on ``127.0.0.1`` and on ``::1`` (``IPV6_V6ONLY``) and serves exactly one
thing: ``GET /proxy?view=<id>`` with ``Host: localhost:<port>``. The pinned UI origin is
given by the caller (the guard's resolved origin) and embedded server-side, JSON-encoded;
nothing in the page comes from the request. The page carries no CSP header (the ``srcdoc``
View inherits a page CSP; the View's policy travels in a ``<meta>``, S-3).

``check_origin_pair`` resolves ``localhost`` with ``AF_INET`` and ``AF_INET6`` separately
(``AF_UNSPEC`` alone can hide ``::1``) in a worker thread and requires every address of
every family that resolves to be loopback.

Nothing here logs a request's target, headers or the page (Art. 5); the module imports no
Reflex (import-linter contract).
"""

from __future__ import annotations

import errno
import ipaddress
import json
import re
import socket
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from importlib.resources import files
from types import TracebackType
from typing import Final
from urllib.parse import parse_qs, urlsplit

import anyio
import h11
from anyio.abc import SocketListener, SocketStream, TaskGroup

from reflex_mcphost.core.logging import get_logger

__all__ = [
    "BIND_ATTEMPTS",
    "LOCALHOST_NOT_LOOPBACK",
    "MAX_HEAD_BYTES",
    "PROXY_PATH",
    "READ_TIMEOUT_S",
    "VIEW_ID_PATTERN",
    "Resolver",
    "SandboxProxy",
    "check_origin_pair",
    "proxy_page_template",
    "render_proxy_page",
    "view_url",
]

PROXY_PATH: Final = "/proxy"
"""The only path the proxy serves."""
VIEW_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""A View id (query ``view``): correlation only, not a secret."""
MAX_HEAD_BYTES: Final = 8 * 1024
"""Longest request head accepted (else ``431`` and close)."""
READ_TIMEOUT_S: Final = 10.0
"""Deadline for a client to send its whole request head."""
BIND_ATTEMPTS: Final = 5
"""Attempts to find a port free on both stacks (``EADDRINUSE`` on ``::1``)."""
LOCALHOST_NOT_LOOPBACK: Final = "localhost_not_loopback"
"""``check_origin_pair`` failure reason (``HostRuntime.apps.reason``)."""

_PLACEHOLDER: Final = "__MCPHOST_UI_ORIGIN__"
_LINGER_S: Final = 1.0
_LINGER_BYTES: Final = 64 * 1024
_RECEIVE_BYTES: Final = 16 * 1024
_NO_IPV6: Final = frozenset({errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT})
_REASONS: Final = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    431: "Request Header Fields Too Large",
}
_ORIGIN_RE: Final = re.compile(r"http://(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):[0-9]{1,5}")
_log = get_logger(__name__)

Resolver = Callable[[str, socket.AddressFamily], Sequence[str]]
"""``resolve(host, family) -> addresses`` (blocking; run in a worker thread)."""


# ------------------------------------------------------------------ the page


def proxy_page_template() -> str:
    """The ``proxy.html`` template (package data) with its UI-origin placeholder."""
    return files("reflex_mcphost.ui.proxy").joinpath("proxy.html").read_text(encoding="utf-8")


def _script_json(value: str) -> str:
    """``value`` as a JSON string literal safe inside an inline ``<script>``."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_proxy_page(ui_origin: str, *, template: str | None = None) -> bytes:
    """The proxy page with ``ui_origin`` pinned (JSON-encoded) at its one placeholder."""
    text = proxy_page_template() if template is None else template
    if text.count(_PLACEHOLDER) != 1:
        raise RuntimeError("proxy.html must contain the UI-origin placeholder exactly once")
    return text.replace(_PLACEHOLDER, _script_json(ui_origin)).encode("utf-8")


def view_url(proxy_origin: str, view_id: str) -> str:
    """``<proxy_origin>/proxy?view=<view_id>``; ``ValueError`` for an invalid View id."""
    if not VIEW_ID_PATTERN.fullmatch(view_id):
        raise ValueError("view id must match [A-Za-z0-9_-]{1,64}")
    return f"{proxy_origin}{PROXY_PATH}?view={view_id}"


# ------------------------------------------------------------------ binding


def _bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
    """A listening, non-blocking TCP socket on ``(address, port)`` (``IPV6_V6ONLY`` for v6)."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if sys.platform == "win32":  # pragma: no cover - exercised on the Windows runner
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(64)
    except BaseException:
        sock.close()
        raise
    return sock


def _bind_pair() -> list[socket.socket]:
    """IPv4 on port 0, then IPv6 on that port; retried on ``EADDRINUSE`` (≤ 5 attempts).

    A machine without an IPv6 loopback (``EADDRNOTAVAIL`` / ``EAFNOSUPPORT``) gets the
    IPv4 socket only: no browser there can reach ``::1`` either (deviation 3).
    """
    for attempt in range(1, BIND_ATTEMPTS + 1):
        v4 = _bind(socket.AF_INET, "127.0.0.1", 0)
        port = int(v4.getsockname()[1])
        try:
            v6 = _bind(socket.AF_INET6, "::1", port)
        except OSError as exc:
            if exc.errno in _NO_IPV6:
                _log.info("no IPv6 loopback; the sandbox proxy listens on 127.0.0.1 only")
                return [v4]
            v4.close()
            if exc.errno != errno.EADDRINUSE or attempt == BIND_ATTEMPTS:
                raise
            continue
        return [v4, v6]
    raise AssertionError("unreachable")  # pragma: no cover


# ------------------------------------------------------------------ the listener


def _response(status: int, body: bytes, *, content_type: str, extra: str = "") -> bytes:
    head = (
        f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Referrer-Policy: no-referrer\r\n"
        f"{extra}"
        "Connection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def _error(status: int, extra: str = "") -> bytes:
    return _response(
        status,
        _REASONS[status].encode("ascii"),
        content_type="text/plain; charset=utf-8",
        extra=extra,
    )


def _valid_view_query(query: str) -> bool:
    try:
        params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    values = params.get("view")
    return (
        set(params) == {"view"}
        and values is not None
        and len(values) == 1
        and VIEW_ID_PATTERN.fullmatch(values[0]) is not None
    )


class SandboxProxy:
    """The sandbox proxy listener (async context manager; DD-012).

    ``async with SandboxProxy(ui_origin) as proxy`` binds the port and serves until exit;
    ``proxy.origin`` is ``http://localhost:<port>``. ``ui_origin`` is the pinned Host UI
    origin ``http://<host>:<port>`` (the guard's resolved origin), never taken from a
    request. Bind failures raise ``OSError`` from ``__aenter__``.
    """

    def __init__(self, ui_origin: str, *, read_timeout: float = READ_TIMEOUT_S) -> None:
        if not _ORIGIN_RE.fullmatch(ui_origin):
            raise ValueError(f"UI origin must be http://<host>:<port> (got {ui_origin!r})")
        self._ui_origin = ui_origin
        self._read_timeout = read_timeout
        self._page: bytes = b""
        self._port: int | None = None
        self._addresses: tuple[str, ...] = ()
        self._stack: AsyncExitStack | None = None

    # -------------------------------------------------------------- public surface

    @property
    def ui_origin(self) -> str:
        """The pinned Host UI origin embedded in the page."""
        return self._ui_origin

    @property
    def port(self) -> int:
        """The proxy port; ``RuntimeError`` unless the listener runs."""
        if self._port is None:
            raise RuntimeError("the sandbox proxy is not running")
        return self._port

    @property
    def origin(self) -> str:
        """``http://localhost:<port>`` — the origin the UI frames and checks."""
        return f"http://localhost:{self.port}"

    @property
    def addresses(self) -> tuple[str, ...]:
        """The bound loopback addresses (``127.0.0.1`` and, where available, ``::1``)."""
        return self._addresses

    def url_for(self, view_id: str) -> str:
        """The proxy URL of one View (``view_url(self.origin, view_id)``)."""
        return view_url(self.origin, view_id)

    async def __aenter__(self) -> SandboxProxy:
        template = await anyio.to_thread.run_sync(proxy_page_template)
        self._page = render_proxy_page(self._ui_origin, template=template)
        sockets = _bind_pair()
        self._port = int(sockets[0].getsockname()[1])
        self._addresses = tuple(str(sock.getsockname()[0]) for sock in sockets)
        stack = AsyncExitStack()
        try:
            listeners: list[SocketListener] = []
            for sock in sockets:
                listener = await SocketListener.from_socket(sock)
                listeners.append(listener)
                stack.push_async_callback(listener.aclose)
            task_group = await stack.enter_async_context(anyio.create_task_group())
            stack.callback(task_group.cancel_scope.cancel)  # LIFO: cancel, then join
            for listener in listeners:
                task_group.start_soon(self._accept_loop, listener, task_group)
        except BaseException:
            await stack.aclose()
            for sock in sockets:
                sock.close()
            self._port, self._addresses = None, ()
            raise
        self._stack = stack
        _log.info("sandbox proxy listening on %s", self.origin)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        self._port, self._addresses = None, ()
        if stack is not None:
            await stack.aclose()

    # -------------------------------------------------------------- serving

    async def _accept_loop(self, listener: SocketListener, task_group: TaskGroup) -> None:
        while True:
            try:
                stream = await listener.accept()
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                return
            except OSError as exc:  # e.g. EMFILE: keep serving
                _log.warning("sandbox proxy accept failed: %s", type(exc).__name__)
                await anyio.sleep(0.1)
                continue
            task_group.start_soon(self._handle, stream)

    async def _handle(self, stream: SocketStream) -> None:
        async with stream:
            try:
                reply = await self._read_and_route(stream)
                if reply is None:
                    return  # deadline or client gone: close without a response
                await stream.send(reply)
                await stream.send_eof()
                await self._linger(stream)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
                return
            except Exception as exc:  # never let one client stop the listener
                _log.debug("sandbox proxy connection error: %s", type(exc).__name__)

    async def _read_and_route(self, stream: SocketStream) -> bytes | None:
        conn = h11.Connection(h11.SERVER, max_incomplete_event_size=MAX_HEAD_BYTES)
        received = 0
        with anyio.move_on_after(self._read_timeout):
            while True:
                try:
                    event = conn.next_event()
                except h11.RemoteProtocolError as exc:
                    return _error(431 if exc.error_status_hint == 431 else 400)
                if event is h11.NEED_DATA:
                    if received > MAX_HEAD_BYTES:  # the head did not end within the limit
                        return _error(431)
                    try:
                        # Never buffer more than the head limit (+1 byte to detect overflow).
                        data = await stream.receive(
                            min(_RECEIVE_BYTES, MAX_HEAD_BYTES + 1 - received)
                        )
                        received += len(data)
                    except anyio.EndOfStream:
                        data = b""
                    try:
                        conn.receive_data(data)
                    except h11.RemoteProtocolError as exc:
                        return _error(431 if exc.error_status_hint == 431 else 400)
                    continue
                if isinstance(event, h11.Request):
                    return self._route(event)
                return None  # ConnectionClosed / PAUSED: nothing to answer
        return None

    def _route(self, request: h11.Request) -> bytes:
        hosts = [value for name, value in request.headers if name == b"host"]
        if hosts != [f"localhost:{self._port}".encode("ascii")]:
            return _error(403)
        target = request.target.decode("ascii", "replace")
        if not target.startswith("/"):
            return _error(404)
        parts = urlsplit(target)
        if parts.path != PROXY_PATH:
            return _error(404)
        if request.method != b"GET":
            return _error(405, "Allow: GET\r\n")
        for name, value in request.headers:
            if name == b"transfer-encoding" or (name == b"content-length" and value != b"0"):
                return _error(400)
        if not _valid_view_query(parts.query):
            return _error(400)
        return _response(200, self._page, content_type="text/html; charset=utf-8")

    @staticmethod
    async def _linger(stream: SocketStream) -> None:
        """Drain what the client still sends so closing does not reset the response."""
        drained = 0
        with anyio.move_on_after(_LINGER_S):
            while drained < _LINGER_BYTES:
                try:
                    drained += len(await stream.receive(_RECEIVE_BYTES))
                except anyio.EndOfStream:
                    return


# ------------------------------------------------------------------ origin pair


def _system_resolve(host: str, family: socket.AddressFamily) -> Sequence[str]:
    infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


async def check_origin_pair(*, resolve: Resolver | None = None) -> str | None:
    """``None`` when ``localhost`` resolves only to loopback, else ``localhost_not_loopback``.

    ``localhost`` is resolved with ``AF_INET`` and with ``AF_INET6`` (``resolve`` defaults to
    ``getaddrinfo``; it runs in a worker thread). A family that fails or returns nothing is
    skipped; every address of the others must be loopback, and at least one family must
    resolve (DD-012 consequences).
    """
    lookup = _system_resolve if resolve is None else resolve
    resolved: list[str] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            addresses = await anyio.to_thread.run_sync(lookup, "localhost", family)
        except Exception as exc:  # this family does not resolve (gaierror, broken resolver)
            _log.debug("localhost does not resolve for %s: %s", family.name, type(exc).__name__)
            continue
        resolved.extend(addresses)
    if not resolved or not all(_is_loopback(address) for address in resolved):
        return LOCALHOST_NOT_LOOPBACK
    return None
