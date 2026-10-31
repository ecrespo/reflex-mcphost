"""Shared invocation outcome enum (API §4.8, §4.9; DM §2.3 ``outcome``).

Kept apart from ``invoker`` so the audit log, the Invoker and the sampling handler share one
definition without importing each other. ``OperationKind`` lives in ``core.consent.outcome``.
Member names follow API §4.8 verbatim.
"""

from __future__ import annotations

from enum import StrEnum


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
