"""LangChain/LangGraph adapter over a Toolset with fake catalog and Invoker (F5-04)."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core", reason="extra 'langgraph' not installed")

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from reflex_mcphost.adapters.langgraph import HostTool, as_langchain_tools
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.toolset import outcome_of
from tests.unit.toolset.fakes import Rig, result, tool

pytestmark = pytest.mark.anyio
CALL = {"type": "tool_call", "id": "1", "name": "srv__echo", "args": {"text": "x"}}


def test_tools_mirror_the_listing() -> None:
    """REQ-TLS-007: one BaseTool per ToolSpec: alias, description, dict args_schema, FQN
    in metadata, handle_tool_error on."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo"), tool("srv::get.item")]
    tools = as_langchain_tools(rig.toolset())
    specs = rig.toolset().list()
    assert [t.name for t in tools] == [s.name for s in specs]
    for lc_tool, spec in zip(tools, specs, strict=True):
        assert isinstance(lc_tool, HostTool)
        assert isinstance(lc_tool, BaseTool)
        assert lc_tool.description == spec.description
        assert lc_tool.args_schema == spec.input_schema
        assert lc_tool.metadata == {"fqn": str(spec.fqn)}
        assert lc_tool.handle_tool_error is True
        assert "toolset" not in lc_tool.model_dump()


async def test_vanished_alias_is_an_error_message() -> None:
    """REQ-TLS-005 (LangChain channel): a tool gone since the snapshot → ToolMessage with
    status="error" and "Tool is no longer available"; nothing is raised."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    (lc_tool,) = as_langchain_tools(rig.toolset())
    rig.catalog.entries.clear()
    message = await lc_tool.ainvoke(CALL)
    assert isinstance(message, ToolMessage)
    assert (message.content, message.status) == ("Tool is no longer available: srv__echo", "error")
    assert rig.invoker.calls == []


async def test_vanished_alias_does_not_fail_a_graph() -> None:
    """REQ-TLS-005: inside a ToolNode graph the vanished alias is a ToolMessage error and
    the graph completes."""
    pytest.importorskip("langgraph", reason="langgraph (test group) not installed")
    from langgraph.graph import START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(as_langchain_tools(rig.toolset())))
    graph.add_edge(START, "tools")
    rig.catalog.entries.clear()
    call = {"id": "1", "name": "srv__echo", "args": {"text": "x"}}
    state = await graph.compile().ainvoke({"messages": [AIMessage(content="", tool_calls=[call])]})
    reply = state["messages"][-1]
    assert isinstance(reply, ToolMessage)
    assert reply.status == "error"


@pytest.mark.parametrize("outcome", list(InvocationOutcome))
async def test_outcomes_map_to_status(outcome: InvocationOutcome) -> None:
    """REQ-TLS-005, REQ-TLS-007: success → status "success" with the text; every error
    outcome → status "error" with the Toolset's text."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    rig.invoker.reply = result(outcome, text="out", error="why")
    (lc_tool,) = as_langchain_tools(rig.toolset())
    message = await lc_tool.ainvoke(CALL)
    assert message.content == outcome_of(rig.invoker.reply).text
    assert message.status == ("success" if outcome is InvocationOutcome.success else "error")


def test_sync_invoke_is_not_supported() -> None:
    """REQ-TLS-007, API §4.13: tools are async-only; sync invoke raises
    NotImplementedError naming ainvoke."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    (lc_tool,) = as_langchain_tools(rig.toolset())
    with pytest.raises(NotImplementedError, match="ainvoke"):
        lc_tool.invoke(CALL)
    assert rig.invoker.calls == []
