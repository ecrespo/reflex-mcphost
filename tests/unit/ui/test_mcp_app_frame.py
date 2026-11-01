"""``mcp_app_frame()`` and the ``McpAppRelay`` component (F7-04; API §8, DD-012;
REQ-APP-001, REQ-APP-011, REQ-APP-013, REQ-APP-015)."""

from __future__ import annotations

from pathlib import Path

import pytest

rx = pytest.importorskip("reflex")

from reflex.compiler.compiler import compile_page  # noqa: E402 - after importorskip

from reflex_mcphost.ui.components.catalog_browser import catalog_browser  # noqa: E402
from reflex_mcphost.ui.components.mcp_app_frame import McpAppRelay, mcp_app_frame  # noqa: E402

RELAY_LIBRARY = "$/public/external/reflex_mcphost/ui/assets/mcp_app_relay.js"


@pytest.fixture(autouse=True)
def _app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Reflex app directory: ``rx.asset(shared=True)`` links the relay into
    ``./assets/external``."""
    import reflex_mcphost.ui.assets as assets

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(assets, "_in_app_dir", lambda: True)


def test_relay_library_is_the_shared_asset(tmp_path: Path) -> None:
    """REQ-APP-003 (relay): the component imports ``McpAppRelay`` from the shared asset,
    which Reflex links into the compiling app's ``assets/external`` directory."""
    relay = McpAppRelay.create(view_id="v", proxy_origin="http://localhost:1")
    assert relay.library == RELAY_LIBRARY
    assert relay.tag == "McpAppRelay"
    linked = tmp_path / "assets" / "external" / "reflex_mcphost" / "ui" / "assets"
    assert (linked / "mcp_app_relay.js").read_text(encoding="utf-8").startswith("// reflex-mcphost")


def test_frame_compiles_in_a_page() -> None:
    """REQ-APP-001, REQ-APP-011: the frame compiles into a page module that imports the
    relay, wires its events to ``HostState`` and binds no HTML sink."""
    _, code = compile_page("apps", mcp_app_frame())
    assert f'import {{McpAppRelay}} from "{RELAY_LIBRARY}"' in code
    for needle in ("on_app_message", "on_app_ack", "proxyOrigin", "outbox", "mcp-app-frame"):
        assert needle in code, needle
    assert "dangerouslySetInnerHTML" not in code


def test_frame_test_hooks_and_plain_text() -> None:
    """REQ-APP-013, REQ-APP-015: notices and the link URL are text children (React
    escapes them); the test hooks of F7-06b are present."""
    rendered = str(mcp_app_frame())
    for testid in (
        "mcp-app-frame",
        "mcp-app-notice",
        "mcp-app-link",
        "mcp-app-link-url",
        "mcp-app-link-approve",
        "mcp-app-link-decline",
        "mcp-app-close",
    ):
        assert testid in rendered, testid
    assert "dangerouslySetInnerHTML" not in rendered


def test_catalog_browser_embeds_the_frame() -> None:
    """REQ-APP-001 (API §8): ``catalog_browser`` renders ``mcp_app_frame`` in the result
    panel."""
    assert "mcp-app-frame" in str(catalog_browser())
