"""Embedding ``reflex_mcphost.ui`` in a foreign Reflex app (F6-09a; REQ-UI-007, API §8).

A foreign ``rx.App`` with a page of its own calls ``install(app, factory)`` and adds pages
built from every API §8 component (``mcp_app_frame`` included since F7-05). Reflex's lifespan
is driven without a server (pages are not compiled to ``.web/``); the backend ASGI app is
called through ``httpx.ASGITransport``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.test_host import MODERN_TABLE, TIMEOUT, Setup, setup
from tests.unit.ui.rig import asgi_of, lifespan, new_app

rx = pytest.importorskip("reflex")

import reflex_mcphost.ui as ui  # noqa: E402 - after importorskip

pytestmark = pytest.mark.anyio
install_mod = importlib.import_module("reflex_mcphost.ui.install")  # `install` is shadowed
__all__ = ["setup"]  # re-exported fixture
PORT = 8131
ORIGIN = f"http://127.0.0.1:{PORT}"
COMPONENTS = (
    "servers_panel",
    "catalog_browser",
    "consent_center",
    "permissions_table",
    "audit_table",
    "mcp_app_frame",
)
"""API §8 components (``mcp_app_frame`` since F7-05, REQ-UI-007)."""


def _foreign_index() -> Any:
    return rx.vstack(rx.heading("Foreign app"), rx.text("not ours"))


def _embedding_page(name: str) -> Any:
    component = getattr(ui, name)

    def page() -> Any:
        return rx.hstack(rx.text("Embedded"), component())

    page.__name__ = f"embedded_{name}"
    return page


async def _get(asgi: Any, host: str, path: str = "/ping") -> httpx.Response:
    transport = httpx.ASGITransport(app=asgi)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.get(path, headers={"host": host})


def test_every_api_component_composes_into_a_foreign_page() -> None:
    """REQ-UI-007: each API §8 component renders inside a page of a foreign app (no
    reference-app module involved)."""
    for name in COMPONENTS:
        rendered = str(_embedding_page(name)())
        assert "Embedded" in rendered, name


async def test_foreign_app_installs_the_host_and_keeps_its_own_pages(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REQ-UI-007, REQ-NFR-011: a foreign ``rx.App`` with its own page calls
    ``install(app, factory)`` and adds pages using every API §8 component (with the
    ``HostState.start_watch`` on_load); while Reflex serves, the factory's Host runs with
    its own Server ready, the guard protects the foreign backend (``/ping`` via
    ``127.0.0.1`` 200, via ``localhost`` 403) and the foreign page is still there."""
    monkeypatch.setattr(install_mod, "_reflex_settings", lambda: (ORIGIN, (ORIGIN,)))
    monkeypatch.delenv("REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE", raising=False)
    config = setup.config({"own": MODERN_TABLE})
    built: list[Any] = []

    def factory() -> Any:
        host = setup.host(config)
        built.append(host)
        return host

    app = new_app()
    app.add_page(_foreign_index, route="/", title="Foreign")
    ui.install(app, factory)
    for name in COMPONENTS:
        app.add_page(_embedding_page(name), route=f"/mcp/{name}", on_load=ui.HostState.start_watch)
    routes = set(app._unevaluated_pages)
    assert routes == {"index", *(f"mcp/{name}" for name in COMPONENTS)}
    asgi = asgi_of(app, monkeypatch, tmp_path)
    async with lifespan(app):
        host = ui.HostRuntime.host
        assert built == [host]
        await host.connections.wait_ready("own", timeout=TIMEOUT)
        assert (await _get(asgi, f"127.0.0.1:{PORT}")).status_code == 200
        refused = await _get(asgi, f"localhost:{PORT}")
        assert refused.status_code == 403
        assert ORIGIN in refused.text
    with pytest.raises(RuntimeError):
        _ = ui.HostRuntime.host
