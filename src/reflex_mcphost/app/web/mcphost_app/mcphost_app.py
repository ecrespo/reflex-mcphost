"""The reference app (REQ-UI-001; API §8, DD-016): composed only from ``reflex_mcphost.ui``.

Run by ``reflex-mcphost run`` as the module ``mcphost_app.mcphost_app`` of the materialized
workspace. Reflex reads its ``app`` attribute; that attribute is built on first access
(PEP 562), which Reflex does while loading the app, before the backend serves: console
logging is configured (JSON with ``REFLEX_MCPHOST_LOG_JSON=1``), the config named by
``REFLEX_MCPHOST_CONFIG`` is loaded once with ``HostConfig.load`` (no blocking I/O on the
event loop later) and ``install(app, host_factory)`` makes the app own one
``Host(<that config> or None)``. Importing the module for tests has no side effect.

Pages (left nav ``Servers | Catalog | Permissions | Audit``; no Settings page in v0.1) come
from the ``PAGES`` registry: each names its view component on ``reflex_mcphost.ui`` and is
resolved when the app is built, so a view whose component has not landed yet renders a
notice instead of failing. Every page has ``consent_center`` and starts the tab's watcher
with ``on_load=HostState.start_watch`` (DD-011).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import reflex as rx

import reflex_mcphost.ui as ui
from reflex_mcphost import Host, HostConfig
from reflex_mcphost.app.cli import ENV_CONFIG, ENV_LOG_JSON, configure_logging

__all__ = [
    "PAGES",
    "PageSpec",
    "build_app",
    "host_factory_for",
    "load_config",
    "page_for",
    "resolve_component",
]

Component = Callable[[], rx.Component]
Resolver = Callable[[str], Component | None]


@dataclass(frozen=True)
class PageSpec:
    """One page of the left nav: its route, title and view component name on ``ui``."""

    route: str
    title: str
    component: str


PAGES: tuple[PageSpec, ...] = (
    PageSpec("/", "Servers", "servers_panel"),
    PageSpec("/catalog", "Catalog", "catalog_browser"),
    PageSpec("/permissions", "Permissions", "permissions_table"),
    PageSpec("/audit", "Audit", "audit_table"),
)
"""The reference app's pages; the components are API §8 names (batch R lands them)."""


def resolve_component(name: str) -> Component | None:
    """The component ``name`` of ``reflex_mcphost.ui``, or ``None`` if it is not there yet."""
    try:
        value = getattr(ui, name)
    except AttributeError:
        return None
    return value if callable(value) else None


def load_config() -> HostConfig | None:
    """``HostConfig.load(REFLEX_MCPHOST_CONFIG)`` when the variable is set, else ``None``."""
    path = os.environ.get(ENV_CONFIG)
    return HostConfig.load(Path(path)) if path else None


def host_factory_for(config: HostConfig | None) -> Callable[[], Host]:
    """The ``host_factory`` handed to ``install``: a new ``Host(config)`` per lifespan."""

    def host_factory() -> Host:
        return Host(config)

    return host_factory


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _nav() -> rx.Component:
    return _component(
        rx.vstack(
            rx.heading("reflex-mcphost", size="4"),
            *(
                rx.link(spec.title, href=spec.route, data_testid=f"nav-{spec.title}")
                for spec in PAGES
            ),
            spacing="3",
            padding="4",
            min_width="180px",
            border_right="1px solid var(--gray-a5)",
            height="100vh",
        )
    )


def _placeholder(spec: PageSpec) -> rx.Component:
    return _component(
        rx.callout(f"The {spec.title} view is not available in this build.", color_scheme="gray")
    )


def page_for(spec: PageSpec, resolve: Resolver = resolve_component) -> Component:
    """The page function of ``spec``: nav, the Host banner, the view and the consent center."""
    view = resolve(spec.component)

    def page() -> rx.Component:
        state: Any = ui.HostState
        return _component(
            rx.hstack(
                _nav(),
                rx.box(
                    rx.cond(state.banner != "", rx.callout(state.banner, color_scheme="amber")),
                    view() if view is not None else _placeholder(spec),
                    flex="1",
                    min_width="0",
                    padding="4",
                ),
                ui.consent_center(),
                align="start",
                width="100%",
                spacing="0",
            )
        )

    page.__name__ = f"{spec.component}_page"
    return page


def build_app(
    config: HostConfig | None,
    *,
    app: rx.App | None = None,
    resolve: Resolver = resolve_component,
) -> rx.App:
    """Install the Host on ``app`` (a new ``rx.App`` by default) and add every page."""
    app = app if app is not None else rx.App()
    ui.install(app, host_factory_for(config))
    for spec in PAGES:
        app.add_page(
            page_for(spec, resolve),
            route=spec.route,
            title=f"{spec.title} · reflex-mcphost",
            on_load=ui.HostState.start_watch,
        )
    return app


def __getattr__(name: str) -> Any:
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    configure_logging(json_lines=os.environ.get(ENV_LOG_JSON) == "1")
    built = build_app(load_config())
    globals()["app"] = built
    return built
