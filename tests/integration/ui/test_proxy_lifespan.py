"""The Host lifespan starts the sandbox proxy and enables MCP Apps (F7-03; DD-012, D6).

A fake Host records ``set_mcp_apps`` / ``start`` / ``aclose``; Reflex's lifespan context is
driven without a server and the proxy listens on real loopback sockets. Failures of the
origin-pair check or the bind, and remote mode, disable MCP Apps with a reason and a
WARNING while the Host still starts (API §8 ``HostRuntime.apps``).
"""

from __future__ import annotations

import importlib
import logging
import socket
from collections.abc import Sequence
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.ui.install import AppsStatus, HostRuntime, install
from reflex_mcphost.ui.proxy import server as proxy_server
from tests.unit.ui.rig import lifespan, new_app

pytestmark = pytest.mark.anyio
install_mod = importlib.import_module("reflex_mcphost.ui.install")  # `install` is shadowed
ORIGIN = "http://127.0.0.1:8125"
REMOTE = "http://192.0.2.10:8125"


class FakeHost:
    """Records the order of ``set_mcp_apps(...)``, ``start`` and ``aclose``."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.steps: list[str] = []

    def set_prompter(self, prompter: object) -> None:
        pass

    def set_mcp_apps(self, enabled: bool) -> None:
        self.steps.append(f"set_mcp_apps({enabled})")

    async def __aenter__(self) -> FakeHost:
        self.steps.append("start")
        if self.fail:
            raise MCPHostError("cannot open the Host database")
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.steps.append("aclose")


def _config(monkeypatch: pytest.MonkeyPatch, deploy_url: str, *, remote: bool = False) -> None:
    monkeypatch.setattr(install_mod, "_reflex_settings", lambda: (deploy_url, (deploy_url,)))
    if remote:
        monkeypatch.setenv("REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE", "1")
    else:
        monkeypatch.delenv("REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE", raising=False)


async def _get_proxy(origin: str) -> bytes:
    port = int(origin.rsplit(":", 1)[1])
    chunks: list[bytes] = []
    async with await anyio.connect_tcp("127.0.0.1", port) as stream:
        await stream.send(f"GET /proxy?view=v1 HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode())
        with anyio.fail_after(5):
            while True:
                try:
                    chunks.append(await stream.receive())
                except (anyio.EndOfStream, anyio.BrokenResourceError):
                    break
    return b"".join(chunks)


async def test_enabled_path_starts_the_proxy_before_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-APP-007, REQ-APP-002: on a loopback origin the lifespan checks the origin pair,
    starts the proxy, calls ``set_mcp_apps(True)`` before ``start()`` and publishes
    ``HostRuntime.apps`` (enabled, proxy origin); the page pins the UI origin; the proxy
    port is closed once the lifespan exits (after ``aclose()``)."""
    _config(monkeypatch, ORIGIN)
    host = FakeHost()
    app = new_app()
    install(app, lambda: host)
    async with lifespan(app):
        assert host.steps == ["set_mcp_apps(True)", "start"]
        status = HostRuntime.apps
        assert status.enabled
        assert status.reason is None
        assert status.proxy_origin is not None
        assert status.proxy_origin.startswith("http://localhost:")
        raw = await _get_proxy(status.proxy_origin)
        assert raw.startswith(b"HTTP/1.1 200 ")
        assert b'"http://127.0.0.1:8125"' in raw
        port = int(status.proxy_origin.rsplit(":", 1)[1])
    assert host.steps == ["set_mcp_apps(True)", "start", "aclose"]
    with pytest.raises(OSError, match="connection attempts failed"):
        await anyio.connect_tcp("127.0.0.1", port)
    assert HostRuntime.apps == AppsStatus(enabled=False, reason=None, proxy_origin=None)


async def test_remote_mode_disables_apps_and_the_host_still_starts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-APP-007, DD-012: a remote-mode ``deploy_url`` never enables MCP Apps
    (``set_mcp_apps`` never called, no proxy), reason ``remote_mode``, a WARNING; the Host
    starts normally."""
    _config(monkeypatch, REMOTE, remote=True)
    started: list[object] = []
    monkeypatch.setattr(install_mod, "SandboxProxy", lambda *a, **k: started.append(a))
    host = FakeHost()
    app = new_app()
    install(app, lambda: host)
    caplog.set_level(logging.WARNING)
    async with lifespan(app):
        assert host.steps == ["start"]
        assert HostRuntime.apps == AppsStatus(False, "remote_mode", None)
        assert HostRuntime.host is host
    assert started == []
    assert "MCP Apps are disabled: remote_mode" in caplog.text


async def test_origin_pair_failure_disables_apps(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-APP-007, REQ-APP-002, DD-012: ``localhost`` resolving to a non-loopback address
    disables MCP Apps (``localhost_not_loopback``, WARNING); the Host still starts."""
    _config(monkeypatch, ORIGIN)

    def resolve(host: str, family: socket.AddressFamily) -> Sequence[str]:
        return ["127.0.0.1"] if family == socket.AF_INET else ["2001:db8::1"]

    real = proxy_server.check_origin_pair

    async def check(**kwargs: Any) -> str | None:
        return await real(resolve=resolve)

    monkeypatch.setattr(install_mod, "check_origin_pair", check)
    host = FakeHost()
    app = new_app()
    install(app, lambda: host)
    caplog.set_level(logging.WARNING)
    async with lifespan(app):
        assert host.steps == ["start"]
        assert HostRuntime.apps == AppsStatus(False, "localhost_not_loopback", None)
    assert "MCP Apps are disabled: localhost_not_loopback" in caplog.text


async def test_bind_failure_disables_apps(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-APP-007, DD-012: a proxy that cannot bind disables MCP Apps
    (``proxy_bind_failed``, WARNING); the Host still starts."""
    _config(monkeypatch, ORIGIN)

    def bind(*args: Any) -> socket.socket:
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(proxy_server, "_bind", bind)
    host = FakeHost()
    app = new_app()
    install(app, lambda: host)
    caplog.set_level(logging.WARNING)
    async with lifespan(app):
        assert host.steps == ["start"]
        assert HostRuntime.apps == AppsStatus(False, "proxy_bind_failed", None)
        assert HostRuntime.host is host
    assert "MCP Apps are disabled: proxy_bind_failed" in caplog.text


async def test_host_start_failure_still_closes_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-APP-002, DD-011: when ``Host.start()`` fails, the error leaves the lifespan and
    the proxy listener is closed too."""
    _config(monkeypatch, ORIGIN)
    seen: list[proxy_server.SandboxProxy] = []
    real = proxy_server.SandboxProxy

    def build(*args: Any, **kwargs: Any) -> proxy_server.SandboxProxy:
        proxy = real(*args, **kwargs)
        seen.append(proxy)
        return proxy

    monkeypatch.setattr(install_mod, "SandboxProxy", build)
    app = new_app()
    install(app, lambda: FakeHost(fail=True))
    with pytest.raises(MCPHostError):
        async with lifespan(app):
            pytest.fail("served although Host.start() failed")
    assert len(seen) == 1
    with pytest.raises(RuntimeError, match="not running"):
        _ = seen[0].port
    assert HostRuntime.apps.enabled is False


def test_apps_status_defaults_to_disabled_without_a_reason() -> None:
    """REQ-APP-007: outside a lifespan ``HostRuntime.apps`` is disabled, no reason."""
    assert HostRuntime.apps == AppsStatus(enabled=False, reason=None, proxy_origin=None)
