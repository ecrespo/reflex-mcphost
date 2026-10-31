"""Shared invocation value types (API §4.8, §4.9, §4.11; DM §2.3 ``outcome``).

Kept apart from ``invoker`` so the audit log, the event bus, the Invoker and the sampling
handler share one definition without importing each other. ``OperationKind`` lives in
``core.consent.outcome``. Member names follow API §4.8 verbatim.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel


class InvocationOutcome(StrEnum):
    """Terminal outcome of an invocation attempt (API §4.8, DM §2.3 ``outcome``)."""

    success = "success"
    tool_error = "tool_error"
    protocol_error = "protocol_error"
    timeout = "timeout"
    cancelled = "cancelled"
    denied = "denied"
    invalid_arguments = "invalid_arguments"
    not_found = "not_found"
    server_unavailable = "server_unavailable"
    audit_unavailable = "audit_unavailable"
    connection_lost = "connection_lost"  # REQ-INV-015


class Progress(BaseModel, frozen=True):
    """One ``notifications/progress`` of an invocation (API §4.8, REQ-INV-005)."""

    progress: float
    total: float | None = None
    message: str | None = None


ProgressCallback: TypeAlias = Callable[[Progress], Awaitable[None] | None]
"""Consumer progress hook (sync or async); exceptions it raises are logged and ignored."""
