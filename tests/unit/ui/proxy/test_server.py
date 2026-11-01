"""``ui.proxy.server.SandboxProxy`` over real sockets on ``127.0.0.1`` and ``::1`` (F7-03).

Every request is a raw HTTP/1.1 exchange on a loopback socket: the listener's serving rules
of TD DD-012 (``GET /proxy`` only, ``Host == localhost:<port>``, headers, no cookie, no CSP
header, head and deadline limits, ``Connection: close``) and the static shape of
``proxy.html`` (the S-3 sandbox-proxy logic, API §7). The ``::1`` cases skip where the
machine has no IPv6 loopback, except on CI (``CI`` set), where they must run.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import socket
import time
from collections.abc import Callable
from typing import Any

import anyio
import pytest

from reflex_mcphost.ui.proxy import server as server_mod
from reflex_mcphost.ui.proxy.server import (
    MAX_HEAD_BYTES,
    SandboxProxy,
    proxy_page_template,
    view_url,
)

pytestmark = pytest.mark.anyio
UI_ORIGIN = "http://127.0.0.1:8123"


def _has_ipv6_loopback() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


IPV6 = _has_ipv6_loopback()
needs_ipv6 = pytest.mark.skipif(
    not IPV6 and not os.environ.get("CI"),
    reason="no IPv6 loopback on this machine (CI must exercise ::1)",
)
ADDRESSES = [
    pytest.param("127.0.0.1", id="ipv4"),
    pytest.param("::1", id="ipv6", marks=needs_ipv6),
]


class Response:
    """A parsed raw HTTP response (status, lower-cased header pairs, body)."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        head, _, self.body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split(" ")[1]) if raw else 0
        self.headers = [
            (name.strip().lower(), value.strip())
            for name, _, value in (line.partition(":") for line in lines[1:])
        ]

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k == name]
        assert len(values) <= 1, f"repeated {name}"
        return values[0] if values else None


async def _exchange(address: str, port: int, raw: bytes, *, limit_s: float = 5.0) -> bytes:
    """Send ``raw`` and read until the server closes (``Connection: close``)."""
    chunks: list[bytes] = []
    async with await anyio.connect_tcp(address, port) as stream:
        await stream.send(raw)
        with anyio.fail_after(limit_s):
            while True:
                try:
                    chunks.append(await stream.receive())
                except (anyio.EndOfStream, anyio.BrokenResourceError):
                    break
    return b"".join(chunks)


def _get(target: str, host: str | None, *, version: str = "1.1", extra: str = "") -> bytes:
    lines = [f"GET {target} HTTP/{version}"]
    if host is not None:
        lines.append(f"Host: {host}")
    if extra:
        lines.append(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def _fetch(proxy: SandboxProxy, raw: bytes, address: str = "127.0.0.1") -> Response:
    return Response(await _exchange(address, proxy.port, raw))


# ------------------------------------------------------------------ the page (200)


@pytest.mark.parametrize("address", ADDRESSES)
async def test_get_proxy_serves_the_page_with_strict_headers(address: str) -> None:
    """REQ-APP-002, DD-012: ``GET /proxy?view=<id>`` with ``Host: localhost:<port>`` on
    either loopback stack gets ``200 text/html; charset=utf-8`` with ``Cache-Control:
    no-store``, ``X-Content-Type-Options: nosniff``, ``Referrer-Policy: no-referrer`` and
    ``Connection: close``; no ``Set-Cookie`` and no CSP header (the ``srcdoc`` View inherits
    a page CSP, S-3)."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        response = await _fetch(
            proxy, _get("/proxy?view=view_7-A", f"localhost:{proxy.port}"), address
        )
    assert response.status == 200
    assert response.header("content-type") == "text/html; charset=utf-8"
    assert response.header("cache-control") == "no-store"
    assert response.header("x-content-type-options") == "nosniff"
    assert response.header("referrer-policy") == "no-referrer"
    assert response.header("connection") == "close"
    assert response.header("set-cookie") is None
    assert response.header("content-security-policy") is None
    assert response.header("content-security-policy-report-only") is None
    assert int(response.header("content-length") or -1) == len(response.body)


async def test_page_pins_the_ui_origin_and_never_echoes_the_query() -> None:
    """REQ-APP-002, DD-012: the page embeds the pinned UI origin JSON-encoded server-side
    and never the request's query (``view`` is correlation only)."""
    marker = "zQx9correlation"
    async with SandboxProxy(UI_ORIGIN) as proxy:
        response = await _fetch(proxy, _get(f"/proxy?view={marker}", f"localhost:{proxy.port}"))
    body = response.body.decode("utf-8")
    assert f"const UI_ORIGIN = {json.dumps(UI_ORIGIN)};" in body
    assert marker not in body
    assert "__MCPHOST_UI_ORIGIN__" not in body


async def test_ui_origin_is_escaped_for_the_script_context() -> None:
    """REQ-APP-002: the pinned origin cannot close the inline script (``<``, ``>``, ``&``
    are JSON-escaped) even for an unusual origin string."""
    page = server_mod.render_proxy_page("http://127.0.0.1:1", template="X __MCPHOST_UI_ORIGIN__")
    assert page == b'X "http://127.0.0.1:1"'
    escaped = server_mod._script_json("</script><b>&")
    assert not {"<", ">", "&"} & set(escaped)
    assert json.loads(escaped) == "</script><b>&"
    separators = server_mod._script_json("a\u2028b\u2029c")
    assert "\u2028" not in separators
    assert "\u2029" not in separators
    assert json.loads(separators) == "a\u2028b\u2029c"


@pytest.mark.parametrize(
    "origin",
    ["https://127.0.0.1:1", "http://127.0.0.1", "http://127.0.0.1:1/x", "", "127.0.0.1:1"],
)
def test_ui_origin_must_be_a_bare_http_origin(origin: str) -> None:
    """REQ-APP-002, DD-012: the pinned UI origin is ``http://<host>:<port>`` with no path."""
    with pytest.raises(ValueError, match="UI origin"):
        SandboxProxy(origin)


# ------------------------------------------------------------------ refusals


@pytest.mark.parametrize("address", ADDRESSES)
@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:{port}",
        "[::1]:{port}",
        "evil.example:{port}",
        "localhost:{other}",
        "localhost",
        "localhost.:{port}",
    ],
)
async def test_wrong_host_header_is_forbidden(address: str, host: str) -> None:
    """REQ-APP-002, DD-012: ``Host`` must be exactly ``localhost:<proxy_port>`` (DNS
    rebinding / origin confusion), else ``403``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        value = host.format(port=proxy.port, other=proxy.port + 1)
        response = await _fetch(proxy, _get("/proxy?view=a", value), address)
    assert response.status == 403
    assert response.header("connection") == "close"


async def test_missing_host_is_forbidden_and_protocol_errors_are_bad_requests() -> None:
    """REQ-APP-002, DD-012: an HTTP/1.0 request without ``Host`` gets ``403``; an HTTP/1.1
    one without ``Host`` or with two ``Host`` headers is a protocol error (``400``)."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        own = f"localhost:{proxy.port}"
        v10 = await _fetch(proxy, _get("/proxy?view=a", None, version="1.0"))
        v11 = await _fetch(proxy, _get("/proxy?view=a", None))
        twice = await _fetch(proxy, _get("/proxy?view=a", own, extra=f"Host: {own}"))
        garbage = await _fetch(proxy, b"\x00\x01 nonsense\r\n\r\n")
        after = await _fetch(proxy, _get("/proxy?view=a", own))
    assert v10.status == 403
    assert v11.status == 400
    assert twice.status == 400
    assert garbage.status == 400
    assert after.status == 200  # a bad client never stops the listener


@pytest.mark.parametrize(
    "target", ["/", "/proxy/", "/proxy.html", "/favicon.ico", "http://localhost/proxy", "*"]
)
async def test_other_paths_are_not_found(target: str) -> None:
    """REQ-APP-002, DD-012: the listener serves only ``/proxy``; anything else is ``404``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        response = await _fetch(proxy, _get(target, f"localhost:{proxy.port}"))
    assert response.status == 404
    assert response.header("content-security-policy") is None


@pytest.mark.parametrize("method", ["POST", "HEAD", "PUT", "OPTIONS", "DELETE"])
async def test_other_methods_are_not_allowed(method: str) -> None:
    """REQ-APP-002, DD-012: only ``GET`` is served; other methods get ``405`` with
    ``Allow: GET``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        raw = (
            f"{method} /proxy?view=a HTTP/1.1\r\nHost: localhost:{proxy.port}\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode()
        response = await _fetch(proxy, raw)
    assert response.status == 405
    assert response.header("allow") == "GET"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?",
        "?view=",
        "?view=" + "a" * 65,
        "?view=a%20b",
        "?view=a.b",
        "?view=a/b",
        "?view=a&view=b",
        "?view=a&x=1",
        "?other=a",
        "?view=%C3%A9",
    ],
)
async def test_bad_view_id_is_a_bad_request(query: str) -> None:
    """REQ-APP-002, DD-012: ``view`` must be one ``[A-Za-z0-9_-]{1,64}`` value (and the only
    query parameter), else ``400``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        response = await _fetch(proxy, _get(f"/proxy{query}", f"localhost:{proxy.port}"))
    assert response.status == 400


async def test_longest_view_id_is_accepted() -> None:
    """REQ-APP-002, DD-012: a 64-character View id is in range."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        response = await _fetch(proxy, _get("/proxy?view=" + "Z" * 64, f"localhost:{proxy.port}"))
    assert response.status == 200


async def test_request_with_a_body_is_a_bad_request() -> None:
    """REQ-APP-002, DD-012: the proxy request has no body; one with a body gets ``400``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        port = proxy.port
        sized = await _fetch(
            proxy, _get("/proxy?view=a", f"localhost:{port}", extra="Content-Length: 4") + b"abcd"
        )
        chunked = await _fetch(
            proxy,
            _get("/proxy?view=a", f"localhost:{port}", extra="Transfer-Encoding: chunked")
            + b"0\r\n\r\n",
        )
    assert sized.status == 400
    assert chunked.status == 400


async def test_oversized_head_is_refused_and_closed() -> None:
    """REQ-APP-002, DD-012: a request head over 8 KiB is refused (``431``) and the
    connection closed."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        filler = "X-Fill: " + "a" * (MAX_HEAD_BYTES + 16)
        raw = _get("/proxy?view=a", f"localhost:{proxy.port}", extra=filler)
        response = await _fetch(proxy, raw)
    assert response.status == 431
    assert response.header("connection") == "close"


async def test_idle_client_is_closed_at_the_read_deadline() -> None:
    """REQ-APP-002, DD-012: a client that does not finish its request head within the read
    deadline (10 s; shortened here) is disconnected without a response."""
    async with SandboxProxy(UI_ORIGIN, read_timeout=0.3) as proxy:
        started = time.monotonic()
        raw = await _exchange("127.0.0.1", proxy.port, b"GET /proxy?view=a HTTP/1.1\r\n")
        elapsed = time.monotonic() - started
    assert raw == b""
    assert 0.25 <= elapsed < 4.0


async def test_requests_are_never_logged_with_their_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-APP-002, Art. 5: the listener logs no request target, header or query value."""
    marker = "Secretish42"
    caplog.set_level(logging.DEBUG)
    async with SandboxProxy(UI_ORIGIN) as proxy:
        await _fetch(proxy, _get(f"/proxy?view={marker}", f"localhost:{proxy.port}"))
        await _fetch(proxy, _get(f"/{marker}", f"{marker}:{proxy.port}"))
    assert marker not in caplog.text


# ------------------------------------------------------------------ binding


async def test_binds_one_port_on_both_loopback_stacks_and_names_localhost() -> None:
    """REQ-APP-002, DD-012: one ephemeral port, bound on ``127.0.0.1`` and (when the
    machine has it) ``::1``; the proxy origin is ``http://localhost:<port>``."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        assert proxy.port > 0
        assert proxy.origin == f"http://localhost:{proxy.port}"
        expected = ("127.0.0.1", "::1") if IPV6 else ("127.0.0.1",)
        assert proxy.addresses == expected
        assert proxy.url_for("v1") == f"http://localhost:{proxy.port}/proxy?view=v1"


def test_listener_is_not_available_before_entering() -> None:
    """REQ-APP-002: ``port`` / ``origin`` exist only while the listener runs."""
    proxy = SandboxProxy(UI_ORIGIN)
    with pytest.raises(RuntimeError, match="not running"):
        _ = proxy.port


async def test_port_is_closed_after_exit() -> None:
    """REQ-APP-002, DD-012: leaving the context closes every listening socket."""
    async with SandboxProxy(UI_ORIGIN) as proxy:
        port = proxy.port
    for address in ("127.0.0.1", "::1") if IPV6 else ("127.0.0.1",):
        with pytest.raises(OSError, match="connection attempts failed"):
            await anyio.connect_tcp(address, port)


@needs_ipv6
async def test_a_taken_ipv6_port_triggers_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-APP-002, DD-012: when ``::1:<port>`` is taken after IPv4 got ``<port>``, the
    proxy closes that IPv4 socket and retries with a new port."""
    real: Callable[..., socket.socket] = server_mod._bind
    blockers: list[socket.socket] = []
    taken: list[int] = []

    def bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
        if family == socket.AF_INET6 and not blockers:
            blocker = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            blocker.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            blocker.bind(("::1", port))
            blocker.listen(1)
            blockers.append(blocker)
            taken.append(port)
        return real(family, address, port)

    monkeypatch.setattr(server_mod, "_bind", bind)
    try:
        async with SandboxProxy(UI_ORIGIN) as proxy:
            assert proxy.port != taken[0]
            response = await _fetch(proxy, _get("/proxy?view=a", f"localhost:{proxy.port}"), "::1")
            assert response.status == 200
    finally:
        for blocker in blockers:
            blocker.close()


async def test_bind_gives_up_after_five_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-APP-002, DD-012: at most 5 attempts on ``EADDRINUSE``; then the bind error
    propagates (the lifespan maps it to ``proxy_bind_failed``)."""
    real: Callable[..., socket.socket] = server_mod._bind
    calls: list[socket.AddressFamily] = []

    def bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
        calls.append(family)
        if family == socket.AF_INET6:
            raise OSError(errno.EADDRINUSE, "taken")
        return real(family, address, port)

    monkeypatch.setattr(server_mod, "_bind", bind)
    with pytest.raises(OSError, match="taken") as caught:
        async with SandboxProxy(UI_ORIGIN):
            pytest.fail("bound although every attempt failed")
    assert caught.value.errno == errno.EADDRINUSE
    assert calls.count(socket.AF_INET6) == 5


async def test_other_ipv6_bind_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-APP-002, DD-012: an IPv6 bind error other than ``EADDRINUSE`` or "no IPv6
    loopback" fails at once and closes the IPv4 socket."""
    real: Callable[..., socket.socket] = server_mod._bind
    opened: list[socket.socket] = []

    def bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError(errno.EACCES, "denied")
        sock = real(family, address, port)
        opened.append(sock)
        return sock

    monkeypatch.setattr(server_mod, "_bind", bind)
    with pytest.raises(OSError, match="denied"):
        async with SandboxProxy(UI_ORIGIN):
            pass
    assert len(opened) == 1
    assert opened[0].fileno() == -1


async def test_missing_ipv6_loopback_falls_back_to_ipv4_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-APP-002, DD-012 (deviation 3): on a machine without an IPv6 loopback the proxy
    serves on ``127.0.0.1`` only (no browser can reach ``::1`` there either)."""
    real: Callable[..., socket.socket] = server_mod._bind

    def bind(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError(errno.EADDRNOTAVAIL, "no ::1")
        return real(family, address, port)

    monkeypatch.setattr(server_mod, "_bind", bind)
    async with SandboxProxy(UI_ORIGIN) as proxy:
        assert proxy.addresses == ("127.0.0.1",)
        response = await _fetch(proxy, _get("/proxy?view=a", f"localhost:{proxy.port}"))
    assert response.status == 200


def test_ipv6_socket_is_v6_only() -> None:
    """REQ-APP-002, DD-012: the ``::1`` socket sets ``IPV6_V6ONLY`` (the IPv4 port is a
    separate socket)."""
    if not IPV6:
        pytest.skip("no IPv6 loopback")
    sock = server_mod._bind(socket.AF_INET6, "::1", 0)
    try:
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
    finally:
        sock.close()


# ------------------------------------------------------------------ helpers


def test_view_url_validates_the_view_id() -> None:
    """REQ-APP-002, DD-012: ``view_url`` builds ``<proxy origin>/proxy?view=<id>`` and
    refuses an id outside ``[A-Za-z0-9_-]{1,64}``."""
    assert view_url("http://localhost:5", "a-b_C9") == "http://localhost:5/proxy?view=a-b_C9"
    for bad in ("", "a b", "a" * 65, "é", "a&x=1"):
        with pytest.raises(ValueError, match="view id"):
            view_url("http://localhost:5", bad)


# ------------------------------------------------------------------ proxy.html (static)


def _template() -> str:
    return proxy_page_template()


def test_page_template_has_one_origin_placeholder() -> None:
    """REQ-APP-002: the UI origin is injected server-side at exactly one place."""
    assert _template().count("__MCPHOST_UI_ORIGIN__") == 1
    assert "const UI_ORIGIN = __MCPHOST_UI_ORIGIN__;" in _template()


def test_page_filters_parent_messages_by_source_and_pinned_origin() -> None:
    """REQ-APP-002, S-3: parent messages are accepted only from ``window.parent`` with the
    pinned UI origin, and posts to the parent target that origin (never ``"*"``)."""
    page = _template()
    assert "event.source !== window.parent || event.origin !== UI_ORIGIN" in page
    assert "window.parent.postMessage(message, UI_ORIGIN)" in page
    assert not re.search(r"parent\.postMessage\([^)]*\"\*\"", page)


def test_page_filters_view_messages_by_window_identity() -> None:
    """REQ-APP-002, S-3: View messages are accepted only from ``inner.contentWindow`` and
    posts to the View use target ``"*"`` (its origin is opaque)."""
    page = _template()
    assert "event.source === inner.contentWindow" in page
    assert 'inner.contentWindow.postMessage(message, "*")' in page


def test_page_builds_the_view_frame_per_the_contract() -> None:
    """REQ-APP-002, REQ-APP-011: the inner frame is ``sandbox="allow-scripts"`` with an
    empty ``allow`` (no device permissions), uses ``srcdoc``, and the CSP ``<meta>`` is the
    first child of ``<head>``; one ``sandbox-resource-ready`` is accepted."""
    page = _template()
    assert 'inner.setAttribute("sandbox", "allow-scripts")' in page
    assert 'inner.setAttribute("allow", "")' in page
    assert "inner.srcdoc = " in page
    assert 'meta.setAttribute("http-equiv", "Content-Security-Policy")' in page
    assert "doc.head.insertBefore(meta, doc.head.firstChild)" in page
    assert '"ui/notifications/sandbox-resource-ready"' in page
    assert '"ui/notifications/sandbox-proxy-ready"' in page
    assert "if (inner !== null) return;" in page  # a second resource-ready is ignored


def test_page_never_builds_script_from_message_data() -> None:
    """REQ-APP-002, Art. 5: no ``eval``, ``Function``, ``document.write``, ``innerHTML``,
    timers from strings, ``document.referrer`` or query parsing in the proxy page."""
    page = _template()
    for banned in (
        "eval(",
        "new Function",
        "document.write",
        "innerHTML",
        "outerHTML =",
        "insertAdjacentHTML",
        "setTimeout(",
        "setInterval(",
        "document.referrer",
        "location.search",
        "allow-same-origin",
    ):
        assert banned not in page, banned


def test_template_is_package_data() -> None:
    """REQ-APP-002: ``proxy.html`` ships inside the package (``importlib.resources``)."""
    from importlib.resources import files

    resource: Any = files("reflex_mcphost.ui.proxy").joinpath("proxy.html")
    assert resource.is_file()
    assert resource.read_text(encoding="utf-8") == _template()
