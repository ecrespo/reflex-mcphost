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
    # catalog (API §4.6)
    "Catalog": "reflex_mcphost.core.catalog",
    "ToolEntry": "reflex_mcphost.core.catalog",
    "ResourceEntry": "reflex_mcphost.core.catalog",
    "ResourceTemplateEntry": "reflex_mcphost.core.catalog",
    "PromptEntry": "reflex_mcphost.core.catalog",
    "ResourceContents": "reflex_mcphost.core.catalog",
    "PromptResult": "reflex_mcphost.core.catalog",
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
    "CatalogChanged": "reflex_mcphost.core.events",
    "ResourceUpdated": "reflex_mcphost.core.events",
    "ServerLog": "reflex_mcphost.core.events",
    "EventsDropped": "reflex_mcphost.core.events",
    # consent, invoker, audit (API §4.7-§4.9) and their events (§4.11)
    "AuditLog": "reflex_mcphost.core.audit",
    "AuditPage": "reflex_mcphost.core.audit",
    "AuditQuery": "reflex_mcphost.core.audit",
    "AuditRecord": "reflex_mcphost.core.audit",
    "ConsentAnswer": "reflex_mcphost.core.consent.models",
    "ConsentDecision": "reflex_mcphost.core.consent.models",
    "ConsentOutcome": "reflex_mcphost.core.consent.outcome",
    "ConsentPolicy": "reflex_mcphost.core.consent.policy",
    "ConsentRequest": "reflex_mcphost.core.consent.models",
    "ConsentRequested": "reflex_mcphost.core.events",
    "ConsentResolved": "reflex_mcphost.core.events",
    "ConsentRule": "reflex_mcphost.core.consent.models",
    "ConsentTarget": "reflex_mcphost.core.consent.models",
    "InvocationError": "reflex_mcphost.core.invoker",
    "InvocationFinished": "reflex_mcphost.core.events",
    "InvocationHandle": "reflex_mcphost.core.invoker",
    "InvocationOutcome": "reflex_mcphost.core.outcomes",
    "InvocationProgress": "reflex_mcphost.core.events",
    "InvocationResult": "reflex_mcphost.core.invoker",
    "InvocationStarted": "reflex_mcphost.core.events",
    "Invoker": "reflex_mcphost.core.invoker",
    "OperationKind": "reflex_mcphost.core.consent.outcome",
    "Progress": "reflex_mcphost.core.outcomes",
    # OAuth flow and sampling seam (API §4.5, §4.14)
    "AuthorizationFlow": "reflex_mcphost.core.auth.flow",
    "SamplingProvider": "reflex_mcphost.core.handlers.sampling",
    # errors (API §3.4)
    "AuditUnavailableError": "reflex_mcphost.core.audit",
    "AuditRecordNotFoundError": "reflex_mcphost.core.audit",
    "ConsentStoreError": "reflex_mcphost.core.consent.store",
    "UnknownRuleError": "reflex_mcphost.core.consent.store",
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
    "AuditLog",
    "AuditPage",
    "AuditQuery",
    "AuditRecord",
    "AuditRecordNotFoundError",
    "AuditUnavailableError",
    "AuthorizationError",
    "AuthorizationFlow",
    "CapabilityNotSupportedError",
    "Catalog",
    "CatalogChanged",
    "ConfigError",
    "ConfigOrigin",
    "ConfigProblem",
    "ConfigProblemCode",
    "ConnectionManager",
    "ConsentAnswer",
    "ConsentDecision",
    "ConsentOutcome",
    "ConsentPolicy",
    "ConsentRequest",
    "ConsentRequested",
    "ConsentResolved",
    "ConsentRule",
    "ConsentStoreError",
    "ConsentTarget",
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
    "InvocationError",
    "InvocationFinished",
    "InvocationHandle",
    "InvocationOutcome",
    "InvocationProgress",
    "InvocationResult",
    "InvocationStarted",
    "Invoker",
    "LogEntry",
    "MCPHostError",
    "OAuthSettings",
    "OperationKind",
    "Progress",
    "PromptEntry",
    "PromptResult",
    "Prompter",
    "RegistryChanged",
    "RegistryDiff",
    "ResourceContents",
    "ResourceEntry",
    "ResourceTemplateEntry",
    "ResourceUpdated",
    "SamplingProvider",
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
    "ToolEntry",
    "TrustRequest",
    "UnknownEntryError",
    "UnknownRuleError",
    "UnknownServerError",
    "__version__",
]
"""Kept literal for static tools; equal to ``_EXPORTS`` plus ``__version__`` (tested)."""

if TYPE_CHECKING:  # pragma: no cover - static re-exports for type checkers and IDEs
    from reflex_mcphost.core.audit import (
        AuditLog,
        AuditPage,
        AuditQuery,
        AuditRecord,
        AuditRecordNotFoundError,
        AuditUnavailableError,
    )
    from reflex_mcphost.core.auth.flow import AuthorizationFlow
    from reflex_mcphost.core.catalog import (
        Catalog,
        PromptEntry,
        PromptResult,
        ResourceContents,
        ResourceEntry,
        ResourceTemplateEntry,
        ToolEntry,
    )
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
    from reflex_mcphost.core.consent.models import (
        ConsentAnswer,
        ConsentDecision,
        ConsentRequest,
        ConsentRule,
        ConsentTarget,
    )
    from reflex_mcphost.core.consent.outcome import ConsentOutcome, OperationKind
    from reflex_mcphost.core.consent.policy import ConsentPolicy
    from reflex_mcphost.core.consent.store import ConsentStoreError, UnknownRuleError
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
        CatalogChanged,
        ConsentRequested,
        ConsentResolved,
        EventBus,
        EventsDropped,
        HostEvent,
        InvocationFinished,
        InvocationProgress,
        InvocationStarted,
        RegistryChanged,
        ResourceUpdated,
        ServerLog,
        ServerStateChanged,
    )
    from reflex_mcphost.core.handlers.sampling import SamplingProvider
    from reflex_mcphost.core.host import Host
    from reflex_mcphost.core.invoker import (
        InvocationError,
        InvocationHandle,
        InvocationResult,
        Invoker,
    )
    from reflex_mcphost.core.outcomes import InvocationOutcome, Progress
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
