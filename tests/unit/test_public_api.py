"""Public surface vs API Spec §3.6 / §4 (F5-05; closes the drift of Analyze P5-08)."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import reflex_mcphost
import reflex_mcphost.toolset

API_SPEC = Path(__file__).resolve().parents[2] / "specs" / "api" / "mcphost-api-v0.1.md"


def _section4_classes() -> set[str]:
    text = API_SPEC.read_text(encoding="utf-8")
    section = text[text.index("## 4. Python public API") : text.index("## 5. `mcphost.toml`")]
    blocks = re.findall(r"```python\n(.*?)```", section, re.S)
    return {name for block in blocks for name in re.findall(r"^\s*class (\w+)", block, re.M)}


def test_every_api_class_is_exported() -> None:
    """REQ-TLS-001, API §3.6: every class named in an API §4 code block is in __all__ of
    `reflex_mcphost` or `reflex_mcphost.toolset` and resolves."""
    classes = _section4_classes()
    assert {"Host", "Toolset", "ToolSpec", "ToolCallOutcome", "SamplingProvider"} <= classes
    public = set(reflex_mcphost.__all__) | set(reflex_mcphost.toolset.__all__)
    assert sorted(classes - public) == []
    for name in classes:
        module = reflex_mcphost if name in reflex_mcphost.__all__ else reflex_mcphost.toolset
        assert isinstance(getattr(module, name), type), name


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("AuthorizationFlow", "reflex_mcphost.core.auth.flow"),
        ("SamplingProvider", "reflex_mcphost.core.handlers.sampling"),
    ],
)
def test_phase4_drift_closed(name: str, module: str) -> None:
    """API §4.5, §4.14 (Analyze P5-08): AuthorizationFlow and SamplingProvider are public
    and are the core objects."""
    assert name in reflex_mcphost.__all__
    assert getattr(reflex_mcphost, name) is getattr(importlib.import_module(module), name)


def test_toolset_and_adapter_all() -> None:
    """REQ-TLS-001, REQ-TLS-006, REQ-TLS-007, API §3.6, §4.12, §4.13: the public names of
    the toolset module and both adapters."""
    assert sorted(reflex_mcphost.toolset.__all__) == [
        "ToolCallOutcome",
        "ToolSpec",
        "Toolset",
        "UnknownToolError",
    ]
    from reflex_mcphost.core.toolset import ToolCallOutcome, Toolset, ToolSpec

    assert reflex_mcphost.toolset.Toolset is Toolset
    assert reflex_mcphost.toolset.ToolSpec is ToolSpec
    assert reflex_mcphost.toolset.ToolCallOutcome is ToolCallOutcome
    pydantic_ai = pytest.importorskip("reflex_mcphost.adapters.pydantic_ai")
    langgraph = pytest.importorskip("reflex_mcphost.adapters.langgraph")
    assert pydantic_ai.__all__ == ["as_pydantic_ai_toolset"]
    assert langgraph.__all__ == ["as_langchain_tools"]


def test_unknown_tool_error_is_public() -> None:
    """REQ-TLS-004, API §3.4, §4.12: UnknownToolError is exported from the package root and
    from ``reflex_mcphost.toolset`` next to the other errors; it is both an MCPHostError and
    a KeyError."""
    from reflex_mcphost.core.errors import MCPHostError, UnknownToolError

    assert "UnknownToolError" in reflex_mcphost.__all__
    assert reflex_mcphost.UnknownToolError is UnknownToolError
    assert reflex_mcphost.toolset.UnknownToolError is UnknownToolError
    assert issubclass(UnknownToolError, MCPHostError)
    assert issubclass(UnknownToolError, KeyError)


def test_every_host_event_class_is_exported() -> None:
    """API §4.11 (Phase 8 Z item 13, gap G-14): every member of the ``HostEvent`` union —
    ``ConsentRulesChanged`` included — is in ``reflex_mcphost.__all__`` and is the
    ``core.events`` class."""
    from typing import get_args

    from reflex_mcphost.core import events

    union = get_args(events.HostEvent)[0]
    members = {cls.__name__: cls for cls in get_args(union)}
    assert "ConsentRulesChanged" in members
    assert sorted(set(members) - set(reflex_mcphost.__all__)) == []
    for name, cls in members.items():
        assert getattr(reflex_mcphost, name) is cls, name
