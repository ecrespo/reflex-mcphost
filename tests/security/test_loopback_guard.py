"""Host/Origin guard of the Reflex backend (F6-01; REQ-NFR-011, DD-017, spike S-5 §5).

The S-5 §5 matrix is reproduced at ASGI level: on ``LoopbackGuard`` over a recording inner
app, and end to end through an ``rx.App`` whose ``api_transformer`` the guard was appended
to by ``install()`` (lifespan driven without a server, pages not compiled).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from reflex_mcphost.ui.install import AllowedOrigin, LoopbackGuard, allowed_origin, install
from tests.unit.ui.rig import asgi_of, lifespan, new_app, ws_call

pytestmark = pytest.mark.anyio
install_mod = importlib.import_module("reflex_mcphost.ui.install")  # `install` is shadowed
P = 8765
LOOPBACK = AllowedOrigin("127.0.0.1", P)
REMOTE_ENV = "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE"


class Recorder:
    """Inner ASGI app: answers HTTP 200 and accepts websockets; records what reached it."""

    def __init__(self) -> None:
        self.reached: list[str] = []

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        self.reached.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        else:
            await receive()
            await send({"type": "websocket.accept"})


def _guarded(origin: AllowedOrigin | None = LOOPBACK) -> tuple[LoopbackGuard, Recorder]:
    inner = Recorder()
    return LoopbackGuard(inner, lambda: origin), inner


async def _get(app: Any, headers: dict[str, str], path: str = "/") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{P}") as c:
        return await c.get(path, headers=headers)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"host": f"127.0.0.1:{P}"}, 200),
        ({"host": f"127.0.0.1:{P}", "origin": f"http://127.0.0.1:{P}"}, 200),
        ({"host": f"localhost:{P}"}, 403),
        ({"host": f"evil.example:{P}"}, 403),  # DNS rebinding
        ({"host": "127.0.0.1"}, 403),  # wrong port (none)
        ({"host": f"127.0.0.1:{P + 1}"}, 403),
        ({"host": f"127.0.0.1:{P}", "origin": "http://evil.example"}, 403),
        ({"host": f"127.0.0.1:{P}", "origin": f"http://localhost:{P}"}, 403),
        ({"host": f"127.0.0.1:{P}", "origin": "null"}, 403),
        ({"host": f"127.0.0.1:{P}", "origin": f"https://127.0.0.1:{P}"}, 403),
    ],
)
async def test_http_matrix(headers: dict[str, str], status: int) -> None:
    """REQ-NFR-011: only ``Host: 127.0.0.1:P`` with no Origin or ``Origin:
    http://127.0.0.1:P`` reaches the app; every 403 body tells the user to open
    ``http://127.0.0.1:P/`` (as HTML)."""
    guard, inner = _guarded()
    response = await _get(guard, headers)
    assert response.status_code == status
    if status == 403:
        assert inner.reached == []
        assert response.headers["content-type"].startswith("text/html")
        assert f"Open http://127.0.0.1:{P}/" in response.text
    else:
        assert inner.reached == ["http"]


async def test_duplicate_host_headers_are_refused() -> None:
    """REQ-NFR-011: an ambiguous request (two Host headers) is refused."""
    guard, inner = _guarded()
    transport = httpx.ASGITransport(app=guard)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{P}") as c:
        request = c.build_request("GET", "/")
        request.headers = httpx.Headers([("host", f"127.0.0.1:{P}"), ("host", "evil.example")])
        response = await c.send(request)
    assert response.status_code == 403
    assert inner.reached == []


@pytest.mark.parametrize(
    ("headers", "accepted"),
    [
        ({"host": f"127.0.0.1:{P}", "origin": f"http://127.0.0.1:{P}"}, True),
        ({"host": f"127.0.0.1:{P}"}, True),  # no Origin
        ({"host": f"127.0.0.1:{P}", "origin": "http://evil.example"}, False),
        ({"host": f"127.0.0.1:{P}", "origin": f"http://localhost:{P}"}, False),
        ({"host": f"evil.example:{P}", "origin": f"http://127.0.0.1:{P}"}, False),
    ],
)
async def test_websocket_matrix(headers: dict[str, str], accepted: bool) -> None:
    """REQ-NFR-011: a websocket with a foreign Host (even with a valid Origin) or a foreign
    Origin is closed before accept; the inner app never sees it."""
    guard, inner = _guarded()
    sent = await ws_call(guard, headers)
    if accepted:
        assert inner.reached == ["websocket"]
        assert sent[0]["type"] == "websocket.accept"
    else:
        assert inner.reached == []
        assert [m["type"] for m in sent] == ["websocket.close"]


async def test_requests_before_start_up_fail_closed() -> None:
    """REQ-NFR-011: before the lifespan resolved the origin, nothing passes."""
    guard, inner = _guarded(None)
    assert (await _get(guard, {"host": f"127.0.0.1:{P}"})).status_code == 503
    sent = await ws_call(guard, {"host": f"127.0.0.1:{P}"})
    assert [m["type"] for m in sent] == ["websocket.close"]
    assert inner.reached == []


async def test_other_scopes_pass_through() -> None:
    """The guard only judges HTTP and websocket scopes (lifespan is Reflex's own)."""
    _, inner = _guarded(None)

    async def receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    async def send(message: Any) -> None:
        pass

    inner_calls: list[str] = []

    async def lifespan_app(scope: Any, receive: Any, send: Any) -> None:
        inner_calls.append(scope["type"])

    await LoopbackGuard(lifespan_app, lambda: None)({"type": "lifespan"}, receive, send)
    assert inner_calls == ["lifespan"]
    assert inner.reached == []


# ------------------------------------------------------------------ origin from deploy_url


def test_origin_is_parsed_from_deploy_url() -> None:
    """REQ-NFR-011, DD-017: the single origin is ``deploy_url``'s scheme, host and port."""
    origin = allowed_origin(f"http://127.0.0.1:{P}/some/path", allow_remote=False)
    assert origin == LOOPBACK
    assert origin.host_header == f"127.0.0.1:{P}"
    assert origin.origin == f"http://127.0.0.1:{P}"
    assert origin.url == f"http://127.0.0.1:{P}/"


@pytest.mark.parametrize(
    ("deploy_url", "allow_remote"),
    [
        (f"http://localhost:{P}", False),
        (f"http://localhost:{P}", True),  # reserved for the MCP Apps sandbox proxy
        (f"http://192.0.2.10:{P}", False),  # non-loopback without the opt-in
        (f"http://0.0.0.0:{P}", True),  # wildcard: the guard needs one concrete origin
        (f"http://[::]:{P}", True),
        (f"https://127.0.0.1:{P}", False),
        ("http://127.0.0.1", False),  # no explicit port
        (None, False),
        ("", False),
    ],
)
def test_unacceptable_deploy_urls_refuse_to_start(
    deploy_url: str | None, allow_remote: bool
) -> None:
    """REQ-NFR-011, DD-017: ``localhost`` always, a non-loopback host without the opt-in, a
    wildcard host, a non-``http`` scheme or a missing port → start-up ``RuntimeError``."""
    with pytest.raises(RuntimeError):
        allowed_origin(deploy_url, allow_remote=allow_remote)


async def test_remote_mode_accepts_only_the_concrete_origin() -> None:
    """REQ-NFR-011 (remote clause), REQ-UI-002: with the opt-in, ``deploy_url =
    http://192.0.2.10:P`` makes ``192.0.2.10:P`` the only accepted Host/Origin."""
    remote = allowed_origin(f"http://192.0.2.10:{P}", allow_remote=True)
    guard, inner = _guarded(remote)
    ok = {"host": f"192.0.2.10:{P}", "origin": f"http://192.0.2.10:{P}"}
    assert (await _get(guard, ok)).status_code == 200
    refused = await _get(guard, {"host": f"127.0.0.1:{P}"})
    assert refused.status_code == 403
    assert f"Open http://192.0.2.10:{P}/" in refused.text
    assert (await _get(guard, {**ok, "origin": f"http://127.0.0.1:{P}"})).status_code == 403
    assert inner.reached == ["http"]


# ------------------------------------------------------------------ end to end via install()


class _Host:
    def set_prompter(self, prompter: object) -> None:
        pass

    def set_mcp_apps(self, enabled: bool) -> None:  # F7-03: the lifespan may enable Apps
        pass

    async def __aenter__(self) -> _Host:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


def _settings(monkeypatch: pytest.MonkeyPatch, deploy_url: str, cors: tuple[str, ...]) -> None:
    monkeypatch.setattr(install_mod, "_reflex_settings", lambda: (deploy_url, cors))


async def test_installed_guard_covers_the_reflex_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REQ-NFR-011, DD-017: through ``install()`` the guard wraps Reflex's backend: ``/ping``
    via ``127.0.0.1:P`` answers, via ``localhost:P`` or a foreign Origin it is a 403 naming
    the origin, and the ``/_event`` websocket with a foreign Host is closed before accept."""
    monkeypatch.delenv(REMOTE_ENV, raising=False)
    _settings(monkeypatch, f"http://127.0.0.1:{P}", (f"http://127.0.0.1:{P}",))
    app = new_app()
    install(app, _Host)
    asgi = asgi_of(app, monkeypatch, tmp_path)
    async with lifespan(app):
        assert (await _get(asgi, {"host": f"127.0.0.1:{P}"}, "/ping")).status_code == 200
        refused = await _get(asgi, {"host": f"localhost:{P}"}, "/ping")
        assert refused.status_code == 403
        assert f"http://127.0.0.1:{P}" in refused.text
        foreign = {"host": f"127.0.0.1:{P}", "origin": "http://evil.example"}
        assert (await _get(asgi, foreign, "/ping")).status_code == 403
        sent = await ws_call(asgi, {"host": f"evil.example:{P}", "origin": LOOPBACK.origin})
        assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize(
    ("deploy_url", "remote_env"),
    [(f"http://localhost:{P}", "1"), (f"http://192.0.2.10:{P}", None)],
)
async def test_lifespan_refuses_an_unacceptable_origin(
    monkeypatch: pytest.MonkeyPatch, deploy_url: str, remote_env: str | None
) -> None:
    """REQ-NFR-011, DD-017: ``install()`` refuses to start (start-up ``RuntimeError``, no Host
    built) for a ``localhost`` deploy_url, or a non-loopback one without
    ``REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1``."""
    if remote_env is None:
        monkeypatch.delenv(REMOTE_ENV, raising=False)
    else:
        monkeypatch.setenv(REMOTE_ENV, remote_env)
    _settings(monkeypatch, deploy_url, ())
    built: list[object] = []

    def factory() -> _Host:
        built.append(1)
        return _Host()

    app = new_app()
    install(app, factory)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pytest.fail("started with an unacceptable origin")
    assert built == []


async def test_remote_opt_in_starts_and_broad_cors_is_warned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-NFR-011, REQ-UI-002, DD-017: the opt-in lets a concrete non-loopback origin
    start; a ``cors_allowed_origins`` broader than the origin is logged as a warning."""
    monkeypatch.setenv(REMOTE_ENV, "1")
    _settings(monkeypatch, f"http://192.0.2.10:{P}", ("*",))
    app = new_app()
    install(app, _Host)
    with caplog.at_level(logging.WARNING, logger="reflex_mcphost.ui.install"):
        async with lifespan(app):
            pass
    messages = [r.getMessage() for r in caplog.records]
    assert any("cors_allowed_origins" in m for m in messages)
    assert any("remote" in m for m in messages)
