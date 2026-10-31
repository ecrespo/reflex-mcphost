"""reflex-mcphost: an open MCP host built with Reflex (public API re-exports, API Spec §3.6).

The public names are resolved lazily (PEP 562) so ``import reflex_mcphost`` stays cheap and
pulls in neither the MCP SDK nor SQLite until a name is first used.
"""

from __future__ import annotations

import logging
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

# Libraries never configure output (REQ-NFR-040): applications attach their own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

try:
    __version__: str = version("reflex-mcphost")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
    __version__ = "0.0.0"

_EXPORTS: dict[str, str] = {
    # facade and configuration (API §4.1-4.3)
    "Host": "reflex_mcphost.core.host",
    "HostConfig": "reflex_mcphost.core.config.models",
    "ServerDefinition": "reflex_mcphost.core.config.models",
    "StdioTransport": "reflex_mcphost.core.config.models",
    "HttpTransport": "reflex_mcphost.core.config.models",
    "OAuthSettings": "reflex_mcphost.core.config.models",
    "ConfigOrigin": "reflex_mcphost.core.config.models",
    "SourceKind": "reflex_mcphost.core.config.models",
    "ShadowedDefinition": "reflex_mcphost.core.config.models",
    "ConfigProblem": "reflex_mcphost.core.config.models",
    "ConfigProblemCode": "reflex_mcphost.core.config.models",
    "RegistryDiff": "reflex_mcphost.core.config.models",
    "ServerRegistry": "reflex_mcphost.core.registry",
    # connections (API §4.5)
    "ConnectionManager": "reflex_mcphost.core.connection.manager",
    "ServerState": "reflex_mcphost.core.connection.state",
    "ServerStatus": "reflex_mcphost.core.connection.state",
    "LogEntry": "reflex_mcphost.core.connection.stderr",
    # identifiers and consumers (API §3.3, §4.4)
    "Consumer": "reflex_mcphost.core.types",
    "ConsumerKind": "reflex_mcphost.core.types",
    "Fqn": "reflex_mcphost.core.types",
    # human-in-the-loop seam (API §4.10; Phase 2 subset)
    "Prompter": "reflex_mcphost.core.prompter",
    "TrustRequest": "reflex_mcphost.core.prompter",
    # events (API §4.11)
    "EventBus": "reflex_mcphost.core.events",
    "HostEvent": "reflex_mcphost.core.events",
    "ServerStateChanged": "reflex_mcphost.core.events",
    "RegistryChanged": "reflex_mcphost.core.events",
    "ServerLog": "reflex_mcphost.core.events",
    "EventsDropped": "reflex_mcphost.core.events",
    # errors (API §3.4)
    "MCPHostError": "reflex_mcphost.core.errors",
    "ConfigError": "reflex_mcphost.core.errors",
    "UnknownServerError": "reflex_mcphost.core.errors",
    "UnknownEntryError": "reflex_mcphost.core.errors",
    "ServerUnavailableError": "reflex_mcphost.core.errors",
    "CapabilityNotSupportedError": "reflex_mcphost.core.errors",
    "AuthorizationError": "reflex_mcphost.core.errors",
    "ServerRequestError": "reflex_mcphost.core.errors",
    "HostClosedError": "reflex_mcphost.core.errors",
    "InputRequestError": "reflex_mcphost.core.errors",
}

__all__ = [
    "AuthorizationError",
    "CapabilityNotSupportedError",
    "ConfigError",
    "ConfigOrigin",
    "ConfigProblem",
    "ConfigProblemCode",
    "ConnectionManager",
    "Consumer",
    "ConsumerKind",
    "EventBus",
    "EventsDropped",
    "Fqn",
    "Host",
    "HostClosedError",
    "HostConfig",
    "HostEvent",
    "HttpTransport",
    "InputRequestError",
    "LogEntry",
    "MCPHostError",
    "OAuthSettings",
    "Prompter",
    "RegistryChanged",
    "RegistryDiff",
    "ServerDefinition",
    "ServerLog",
    "ServerRegistry",
    "ServerRequestError",
    "ServerState",
    "ServerStateChanged",
    "ServerStatus",
    "ServerUnavailableError",
    "ShadowedDefinition",
    "SourceKind",
    "StdioTransport",
    "TrustRequest",
    "UnknownEntryError",
    "UnknownServerError",
    "__version__",
]
"""Kept literal for static tools; equal to ``_EXPORTS`` plus ``__version__`` (tested)."""

if TYPE_CHECKING:  # pragma: no cover - static re-exports for type checkers and IDEs
    from reflex_mcphost.core.config.models import (
        ConfigOrigin,
        ConfigProblem,
        ConfigProblemCode,
        HostConfig,
        HttpTransport,
        OAuthSettings,
        RegistryDiff,
        ServerDefinition,
        ShadowedDefinition,
        SourceKind,
        StdioTransport,
    )
    from reflex_mcphost.core.connection.manager import ConnectionManager
    from reflex_mcphost.core.connection.state import ServerState, ServerStatus
    from reflex_mcphost.core.connection.stderr import LogEntry
    from reflex_mcphost.core.errors import (
        AuthorizationError,
        CapabilityNotSupportedError,
        ConfigError,
        HostClosedError,
        InputRequestError,
        MCPHostError,
        ServerRequestError,
        ServerUnavailableError,
        UnknownEntryError,
        UnknownServerError,
    )
    from reflex_mcphost.core.events import (
        EventBus,
        EventsDropped,
        HostEvent,
        RegistryChanged,
        ServerLog,
        ServerStateChanged,
    )
    from reflex_mcphost.core.host import Host
    from reflex_mcphost.core.prompter import Prompter, TrustRequest
    from reflex_mcphost.core.registry import ServerRegistry
    from reflex_mcphost.core.types import Consumer, ConsumerKind, Fqn


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value  # cache: later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
