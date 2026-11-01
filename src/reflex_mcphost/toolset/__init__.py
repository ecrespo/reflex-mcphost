"""Framework-neutral agent bridge exposing Host tools (RF-TLS, API §4.12).

This is the public import path; the implementation lives in a private core module
(delta 2026-10-phase5-prep D1). Importing it imports no agent framework (REQ-TLS-008).
"""

from __future__ import annotations

from reflex_mcphost.core.toolset import ToolCallOutcome, Toolset, ToolSpec

__all__ = ["ToolCallOutcome", "ToolSpec", "Toolset"]
