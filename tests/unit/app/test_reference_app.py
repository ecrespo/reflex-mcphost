"""The packaged reference app: ``rxconfig.py`` and the ``mcphost_app`` module (F6-08).

``rxconfig.py`` is executed with the DD-016 environment and its ``rx.Config`` inspected; the
app is built in a fork of Reflex's registration context with the pages compiled nowhere.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

from reflex_mcphost.app import workspace

rx = pytest.importorskip("reflex")

from reflex_mcphost.app.web.mcphost_app import mcphost_app  # noqa: E402 - after importorskip
from reflex_mcphost.ui.install import GuardTransformer  # noqa: E402
from tests.unit.ui.rig import new_app  # noqa: E402

RXCONFIG = workspace.source_root() / "rxconfig.py"


def _config(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    for name in ("REFLEX_MCPHOST_HOST", "REFLEX_MCPHOST_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(str(RXCONFIG))["config"]


def test_rxconfig_loopback_origin_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-NFR-011 (origin wiring): api_url = deploy_url = the single CORS origin
    http://<host>:<port> from REFLEX_MCPHOST_HOST/PORT; telemetry off (DD-016, DD-017)."""
    config = _config(monkeypatch, REFLEX_MCPHOST_HOST="127.0.0.1", REFLEX_MCPHOST_PORT="3123")
    origin = "http://127.0.0.1:3123"
    assert config.app_name == "mcphost_app"
    assert config.api_url == origin
    assert config.deploy_url == origin
    assert list(config.cors_allowed_origins) == [origin]
    assert config.telemetry_enabled is False
    assert config.backend_host == "127.0.0.1"
    assert config.frontend_port == config.backend_port == 3123


def test_rxconfig_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-UI-001: without the CLI environment the app is bound to 127.0.0.1:3000."""
    config = _config(monkeypatch)
    assert config.deploy_url == "http://127.0.0.1:3000"
    assert config.backend_host == "127.0.0.1"


def test_rxconfig_brackets_an_ipv6_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-NFR-011: a concrete IPv6 host (remote mode) yields a valid origin URL."""
    config = _config(monkeypatch, REFLEX_MCPHOST_HOST="2001:db8::5", REFLEX_MCPHOST_PORT="3000")
    assert config.deploy_url == "http://[2001:db8::5]:3000"


def test_config_loaded_once_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DD-016: REFLEX_MCPHOST_CONFIG names the file loaded with HostConfig.load; unset → None."""
    path = tmp_path / "mcphost.toml"
    path.write_text(f'schema_version = 1\n[host]\ndata_dir = "{tmp_path.as_posix()}"\n', "utf-8")
    monkeypatch.setenv("REFLEX_MCPHOST_CONFIG", str(path))
    config = mcphost_app.load_config()
    assert config is not None
    assert config.config_path == path
    assert config.data_dir == tmp_path
    monkeypatch.delenv("REFLEX_MCPHOST_CONFIG")
    assert mcphost_app.load_config() is None


def test_host_factory_builds_a_host_with_that_config(tmp_path: Path) -> None:
    """DD-016: host_factory returns Host(<the loaded config> or None)."""
    host = mcphost_app.host_factory_for(None)()
    assert type(host).__name__ == "Host"
    assert host._config is None


def _pages(app: Any) -> dict[str, Any]:
    return dict(app._unevaluated_pages)


def test_app_has_the_four_pages_with_watch_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-UI-001: left nav Servers | Catalog | Permissions | Audit, each page starts the
    tab's watcher on_load (DD-011) and the Host is installed with its guard (DD-017)."""
    app = new_app()
    built = mcphost_app.build_app(None, app=app, resolve=lambda name: None)
    assert built is app
    pages = _pages(app)
    assert set(pages) == {"index", "catalog", "permissions", "audit"}
    from reflex_mcphost.ui import HostState

    for page in pages.values():
        on_load = page.on_load if isinstance(page.on_load, list) else [page.on_load]
        assert any(getattr(h, "fn", None) is HostState.start_watch.fn for h in on_load)
    transformers = app.api_transformer
    transformers = transformers if isinstance(transformers, list) else [transformers]
    assert any(isinstance(t, GuardTransformer) for t in transformers)
    assert [p.title for p in mcphost_app.PAGES] == ["Servers", "Catalog", "Permissions", "Audit"]


def test_page_uses_the_registered_component_and_consent_center() -> None:
    """REQ-UI-005 (placement): consent_center is on every page next to the view component."""
    calls: list[str] = []

    def resolve(name: str) -> Any:
        def component() -> Any:
            calls.append(name)
            return rx.text(f"view {name}")

        return component

    page = mcphost_app.page_for(mcphost_app.PAGES[1], resolve)
    rendered = str(page())
    assert calls == ["catalog_browser"]
    assert "view catalog_browser" in rendered
    assert "Pending requests" in rendered


def test_missing_component_renders_a_placeholder() -> None:
    """A view whose component has not landed yet renders a notice, not an import error."""
    page = mcphost_app.page_for(mcphost_app.PAGES[3], lambda name: None)
    assert "not available" in str(page())


def test_default_resolver_reads_reflex_mcphost_ui() -> None:
    """The registry resolves names on ``reflex_mcphost.ui``; unknown names give None."""
    assert mcphost_app.resolve_component("consent_center") is not None
    assert mcphost_app.resolve_component("no_such_component") is None


def test_rxconfig_plugins_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """DD-016: Radix Themes enabled explicitly, the default sitemap plugin disabled."""
    config = _config(monkeypatch)
    assert [type(p).__name__ for p in config.plugins] == ["RadixThemesPlugin"]
