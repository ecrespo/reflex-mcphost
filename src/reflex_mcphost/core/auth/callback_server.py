"""``LoopbackCallbackServer``: the one network listener core opens itself (DD-009, API §2.2).

An ``anyio`` TCP listener on ``127.0.0.1`` (never ``0.0.0.0``, ``::`` or ``localhost``)
speaking HTTP/1.1 through ``h11`` (Art. 8: no ``http.server``, no threads). It serves only
``GET /callback`` (other paths ``404``, other methods ``405``), caps a request head at
8 KiB (``431``) and a connection's reads at ``read_timeout_s``, and accepts exactly one
*authentic* callback (a ``code`` or AS ``error`` the ``validator`` accepts): it answers a
static page that echoes nothing (``Cache-Control: no-store``, ``Referrer-Policy:
no-referrer``) and then closes. Any other callback (foreign ``state``, bad ``iss``, no
``code``) gets ``400`` + the failure page and the listener keeps waiting, so a local page or
process cannot end or hijack the flow (login CSRF); the flow deadline bounds the wait.
The request target and query are never logged (REQ-AUTH-008). Use as
``async with LoopbackCallbackServer() as server: ... await server.result()``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlsplit

import anyio
import h11
from anyio.abc import SocketAttribute, SocketListener, SocketStream, TaskGroup
from anyio.streams.stapled import MultiListener

from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.secrets import register_secret

__all__ = [
    "FAILURE_PAGE",
    "LOOPBACK_HOST",
    "MAX_HEAD_BYTES",
    "SUCCESS_PAGE",
    "CallbackResult",
    "LoopbackCallbackServer",
]

LOOPBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
MAX_HEAD_BYTES = 8 * 1024
READ_TIMEOUT_S = 10.0
_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>reflex-mcphost</title>'
    "</head><body><p>{}</p></body></html>"
)
SUCCESS_PAGE = _PAGE.format("You can close this tab.")
FAILURE_PAGE = _PAGE.format("Authorization failed. You can close this tab.")
_log = get_logger(__name__)


@dataclass(frozen=True)
class CallbackResult:
    """The redirect parameters (``code``/``state``/``iss`` or the AS ``error``)."""

    code: str | None
    state: str | None
    iss: str | None
    error: str | None

    def __repr__(self) -> str:  # never show the code or state (Art. 5)
        return f"CallbackResult(error={self.error!r})"


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


def _response(status: int, body: str = "") -> tuple[h11.Response, bytes]:
    data = body.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(data))),
        ("Cache-Control", "no-store"),
        ("Referrer-Policy", "no-referrer"),
        ("Connection", "close"),
    ]
    return h11.Response(status_code=status, headers=headers), data


class LoopbackCallbackServer:
    """One-shot OAuth redirect listener on ``127.0.0.1`` (async context manager).

    ``port`` is a preferred port (a DCR client's registered one); if it is taken an
    ephemeral port is used. ``validator`` (optional) decides whether a callback is
    authentic (ends the flow) or is rejected (``400``, keep listening).
    """

    def __init__(self, port: int = 0, *, read_timeout_s: float = READ_TIMEOUT_S) -> None:
        self._preferred = port
        self._read_timeout_s = read_timeout_s
        self._listener: SocketListener | None = None
        self._tg: TaskGroup | None = None
        self._result: CallbackResult | None = None
        self._got = anyio.Event()
        self.closed = anyio.Event()
        """Set once the listener stopped accepting connections."""
        self.validator: Callable[[CallbackResult], bool] | None = None
        self.port = 0

    @property
    def host(self) -> str:
        return LOOPBACK_HOST

    @property
    def bound_address(self) -> tuple[str, int]:
        if self._listener is None:
            raise RuntimeError("listener not bound")
        address = self._listener.extra(SocketAttribute.local_address)  # noqa: S610 - anyio
        return str(address[0]), int(address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.port}{CALLBACK_PATH}"

    async def result(self) -> CallbackResult:
        """Wait for the callback."""
        await self._got.wait()
        assert self._result is not None  # noqa: S101 - set before the event
        return self._result

    # ------------------------------------------------------------------ lifecycle

    async def _bind(self) -> MultiListener[SocketStream]:
        if self._preferred:
            try:
                return await anyio.create_tcp_listener(
                    local_host=LOOPBACK_HOST, local_port=self._preferred
                )
            except OSError:
                _log.info("preferred loopback port is taken; using an ephemeral port")
        return await anyio.create_tcp_listener(local_host=LOOPBACK_HOST, local_port=0)

    async def __aenter__(self) -> Self:
        multi = await self._bind()
        listener = multi.listeners[0]
        assert isinstance(listener, SocketListener)  # noqa: S101 - one TCP listener
        self._listener = listener
        self.port = int(listener.extra(SocketAttribute.local_port))  # noqa: S610 - anyio
        tg = anyio.create_task_group()
        await tg.__aenter__()
        self._tg = tg
        tg.start_soon(self._serve, listener)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        assert self._tg is not None  # noqa: S101 - entered
        self._tg.cancel_scope.cancel()
        await self._tg.__aexit__(exc_type, exc, tb)
        await self._close()
        return None

    async def _close(self) -> None:
        if self._listener is not None:
            with anyio.CancelScope(shield=True):
                await self._listener.aclose()
        self.closed.set()

    async def _serve(self, listener: SocketListener) -> None:
        try:
            async with anyio.create_task_group() as tg:
                while not self._got.is_set():
                    stream = await listener.accept()
                    tg.start_soon(self._handle, stream, tg)
        finally:
            await self._close()

    # ------------------------------------------------------------------ HTTP

    async def _handle(self, stream: SocketStream, tg: TaskGroup) -> None:
        async with stream:
            conn = h11.Connection(h11.SERVER, max_incomplete_event_size=MAX_HEAD_BYTES)
            request: h11.Request | None = None
            try:
                with anyio.move_on_after(self._read_timeout_s):
                    request = await self._read_request(stream, conn)
            except h11.RemoteProtocolError as exc:
                await self._send(stream, conn, exc.error_status_hint)
                return
            if request is None:
                return  # idle or closed before a full head: drop it silently
            if request.method != b"GET":
                await self._send(stream, conn, 405)
                return
            parts = urlsplit(request.target.decode("ascii", errors="replace"))
            if parts.path != CALLBACK_PATH or self._got.is_set():
                await self._send(stream, conn, 404)
                return
            result = self._parse(parts.query)
            if not self._authentic(result):
                # (its values are not registered: a forged request must not poison redaction)
                # a forged / foreign callback must not end (or CSRF) the flow: keep waiting
                await self._send(stream, conn, 400, FAILURE_PAGE)
                _log.debug("authorization callback rejected; still waiting")
                return
            for value in (result.code, result.state, parts.query):
                if value:
                    register_secret(value)
            page = SUCCESS_PAGE if result.error is None else FAILURE_PAGE
            await self._send(stream, conn, 200, page)
            _log.debug("authorization callback received")
            self._result = result
            self._got.set()
            tg.cancel_scope.cancel()  # one authentic callback only: stop accepting

    def _authentic(self, result: CallbackResult) -> bool:
        """Whether ``result`` ends the flow: it carries a ``code`` or an AS ``error`` and the
        ``validator`` (``state`` / ``iss`` checks, set by the controller) accepts it."""
        if result.code is None and result.error is None:
            return False
        if self.validator is None:
            return True
        try:
            return self.validator(result)
        except Exception:
            return False

    @staticmethod
    def _parse(query_text: str) -> CallbackResult:
        query = parse_qs(query_text, keep_blank_values=True)
        result = CallbackResult(
            code=_first(query, "code"),
            state=_first(query, "state"),
            iss=_first(query, "iss"),
            error=_first(query, "error"),
        )
        return result

    @staticmethod
    async def _read_request(stream: SocketStream, conn: h11.Connection) -> h11.Request | None:
        received = 0
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                try:
                    data = await stream.receive()
                except (anyio.EndOfStream, anyio.BrokenResourceError):
                    data = b""
                received += len(data)
                if received > MAX_HEAD_BYTES and b"\r\n\r\n" in data:
                    raise h11.RemoteProtocolError("request head too large", 431)
                conn.receive_data(data)
                if not data:
                    event = conn.next_event()
                    return event if isinstance(event, h11.Request) else None
                continue
            if isinstance(event, h11.Request):
                head = len(event.target) + sum(len(k) + len(v) + 4 for k, v in event.headers)
                if head > MAX_HEAD_BYTES:
                    raise h11.RemoteProtocolError("request head too large", 431)
                return event
            return None

    @staticmethod
    async def _send(
        stream: SocketStream, conn: h11.Connection, status: int, body: str = ""
    ) -> None:
        response, data = _response(status, body)
        try:
            if conn.our_state is not h11.SEND_RESPONSE:
                conn = h11.Connection(h11.SERVER)  # protocol error: answer on a clean state
                conn.receive_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
                conn.next_event()
            out = (
                conn.send(response) + conn.send(h11.Data(data=data)) + conn.send(h11.EndOfMessage())
            )
            await stream.send(out)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, h11.LocalProtocolError):
            pass
