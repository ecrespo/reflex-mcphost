"""A LangGraph agent calling a fixture tool through consent, both eras (F5-04).

No real LLM: ``FakeToolChatModel`` (a ``GenericFakeChatModel`` that accepts
``bind_tools``) first asks for the tool, then answers. The graph is the snippet shown in
``docs/library.md``: ``model → tools_condition → ToolNode → model``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain_core", reason="extra 'langgraph' not installed")
pytest.importorskip("langgraph", reason="langgraph (test group) not installed")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from reflex_mcphost import Consumer
from reflex_mcphost.adapters.langgraph import as_langchain_tools
from reflex_mcphost.core.consent.models import ConsentDecision
from reflex_mcphost.core.outcomes import InvocationOutcome
from tests.integration.toolset.rig import (
    FakePrompter,
    Setup,
    era_server,
    host_for,
    records,
    setup,
)

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures


class FakeToolChatModel(GenericFakeChatModel):
    """Scripted chat model; ``bind_tools`` is accepted and ignored (no real LLM)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolChatModel:
        return self


def build_agent(model: Any, tools: list[Any]) -> Any:
    """model → (tool calls?) → ToolNode → model, until the model answers."""
    llm = model.bind_tools(tools)

    async def call_model(state: MessagesState) -> dict[str, Any]:
        return {"messages": [await llm.ainvoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()


@pytest.mark.parametrize(
    ("decision", "status"),
    [(ConsentDecision.ALLOW_ONCE, "success"), (ConsentDecision.DENY, "error")],
)
async def test_graph_calls_fixture_tool_through_consent(
    setup: Setup, era_server: tuple[str, str], decision: ConsentDecision, status: str
) -> None:
    """REQ-TLS-007, REQ-TLS-005, REQ-CNS-015: a two-node graph driven by a fake chat
    model calls `<id>__echo`; an allowing prompter → ToolMessage with the echo text and a
    final AI message; a denying prompter → ToolMessage(status="error") with the denial
    text, the graph completes, and the audit record is `denied`."""
    prompter = FakePrompter(decision=decision)
    async with host_for(setup, era_server, prompter=prompter) as (host, server_id):
        tools = as_langchain_tools(host.toolset(Consumer.agent("helper"), include=[server_id]))
        call = {"id": "call-1", "name": f"{server_id}__echo", "args": {"text": "hello"}}
        model = FakeToolChatModel(
            messages=iter([AIMessage(content="", tool_calls=[call]), AIMessage(content="done")])
        )
        state = await build_agent(model, tools).ainvoke({"messages": [("user", "echo hello")]})

        tool_message = next(m for m in state["messages"] if isinstance(m, ToolMessage))
        assert tool_message.status == status
        final = state["messages"][-1]
        assert isinstance(final, AIMessage)
        assert final.content == "done"
        (record,) = await records(host)
        assert record.consumer == Consumer.agent("helper")
        if decision is ConsentDecision.DENY:
            assert str(tool_message.content).startswith("Permission denied by the host: ")
            assert record.outcome is InvocationOutcome.denied
        else:
            assert tool_message.content == "hello"
            assert record.outcome is InvocationOutcome.success
