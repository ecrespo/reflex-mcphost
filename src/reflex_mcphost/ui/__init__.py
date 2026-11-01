"""Embeddable Reflex components and state (RF-UI, API §8; REQ-UI-007).

Reflex-free names are bound eagerly (``install`` shadows its own submodule's name, so it
is imported here first); Reflex components and state are resolved lazily (PEP 562), so
importing ``reflex_mcphost.ui`` or a Reflex-free submodule such as ``reflex_mcphost.ui.models``
does not import Reflex. Each name of API §8 lands with its task.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from reflex_mcphost.ui.install import HostRuntime, install
from reflex_mcphost.ui.prompter import ReflexPrompter

_EXPORTS: dict[str, str] = {
    "audit_table": "reflex_mcphost.ui.components.audit_table",
    "catalog_browser": "reflex_mcphost.ui.components.catalog_browser",
    "consent_center": "reflex_mcphost.ui.components.consent_center",
    "mcp_app_frame": "reflex_mcphost.ui.components.mcp_app_frame",
    "permissions_table": "reflex_mcphost.ui.components.permissions_table",
    "servers_panel": "reflex_mcphost.ui.components.servers_panel",
    "HostState": "reflex_mcphost.ui.state",
}

__all__ = [
    "HostRuntime",
    "HostState",
    "ReflexPrompter",
    "audit_table",
    "catalog_browser",
    "consent_center",
    "install",
    "mcp_app_frame",
    "permissions_table",
    "servers_panel",
]

if TYPE_CHECKING:  # pragma: no cover - static re-exports for type checkers and IDEs
    from reflex_mcphost.ui.components.audit_table import audit_table
    from reflex_mcphost.ui.components.catalog_browser import catalog_browser
    from reflex_mcphost.ui.components.consent_center import consent_center
    from reflex_mcphost.ui.components.mcp_app_frame import mcp_app_frame
    from reflex_mcphost.ui.components.permissions_table import permissions_table
    from reflex_mcphost.ui.components.servers_panel import servers_panel
    from reflex_mcphost.ui.state import HostState


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
