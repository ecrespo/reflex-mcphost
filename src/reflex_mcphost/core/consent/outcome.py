"""Consent outcome types shared with the event bus (API §4.7, §4.11).

Kept apart from ``models`` (which needs ``ToolEntry`` from the catalog) so that
``core.events`` can type ``consent.resolved`` without an import cycle
(events → catalog → events).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from mcp_types import CreateMessageRequestParams
from pydantic import BaseModel, ConfigDict

__all__ = ["ConsentOutcome", "ConsentSource", "OperationKind"]


class OperationKind(StrEnum):
    """Effectful operations routed through ``ConsentPolicy`` (Art. 3, REQ-CNS-001)."""

    TOOL_CALL = "tool_call"
    SAMPLING = "sampling"


ConsentSource = Literal["rule", "prompt", "default_deny", "error", "timeout", "no_prompter"]


class ConsentOutcome(BaseModel):
    """The policy's decision. ``reason`` explains denials and degraded paths; it never
    carries argument values or secrets (Art. 5)."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    source: ConsentSource
    rule_id: str | None = None
    reason: str | None = None
    edited_sampling: CreateMessageRequestParams | None = None
