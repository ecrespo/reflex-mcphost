"""Pinned agent-framework surfaces the adapters rely on (spike S-6, API §4.13).

Static items (S-6 #1-6, #10-12) pin the framework APIs; behavioural items (S-6 #7-9,
#13-17) drive the real adapters over a ``Toolset`` with a fake catalog and Invoker (F5-03,
F5-04). The missing-extra ``ImportError`` is in ``tests/architecture/
test_no_framework_import.py``. Each block is skipped unless its extra is installed.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing

import pytest

from reflex_mcphost.core.outcomes import InvocationOutcome
from tests.unit.toolset.fakes import Rig, result, tool

pytestmark = pytest.mark.anyio
DENIAL = "Permission denied by the host: consent declined by user"


def _denying_rig() -> Rig:
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    rig.invoker.reply = result(InvocationOutcome.denied, error="consent declined by user")
    return rig


class TestPydanticAI:
    @pytest.fixture(autouse=True)
    def _requires_extra(self) -> None:
        pytest.importorskip("pydantic_ai", reason="extra 'pydantic-ai' not installed")

    def test_imports(self) -> None:
        """S-6 #1: the names the adapter imports exist."""
        from pydantic_ai import RunContext, ToolFailed  # noqa: F401
        from pydantic_ai.tools import ToolDefinition  # noqa: F401
        from pydantic_ai.toolsets import AbstractToolset, ToolsetTool  # noqa: F401

    def test_abstract_toolset(self) -> None:
        """S-6 #2-4: AbstractToolset abstract members and coroutine signatures."""
        from pydantic_ai.toolsets import AbstractToolset

        assert AbstractToolset.__abstractmethods__ == frozenset({"id", "get_tools", "call_tool"})
        assert isinstance(inspect.getattr_static(AbstractToolset, "id"), property)
        get_tools = AbstractToolset.get_tools
        call_tool = AbstractToolset.call_tool
        assert list(inspect.signature(get_tools).parameters) == ["self", "ctx"]
        assert list(inspect.signature(call_tool).parameters) == [
            "self",
            "name",
            "tool_args",
            "ctx",
            "tool",
        ]
        assert inspect.iscoroutinefunction(get_tools)
        assert inspect.iscoroutinefunction(call_tool)

    def test_toolset_tool_and_definition(self) -> None:
        """S-6 #5-6: ToolsetTool keyword-only fields and ToolDefinition fields."""
        from pydantic_ai.tools import ToolDefinition
        from pydantic_ai.toolsets import ToolsetTool

        params = inspect.signature(ToolsetTool).parameters
        for name in ("toolset", "tool_def", "max_retries", "args_validator"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty, name
        assert params["args_validator_func"].default is None
        fields = {f.name for f in dataclasses.fields(ToolDefinition)}
        assert {"name", "parameters_json_schema", "description", "metadata"} <= fields

    async def test_denial_is_a_failed_tool_return(self) -> None:
        """S-6 #7, REQ-TLS-005: with FunctionModel, a denied call yields
        ToolReturnPart(content=<denial text>, outcome="failed") and the run completes."""
        from pydantic_ai import Agent
        from pydantic_ai.messages import (
            ModelMessage,
            ModelResponse,
            TextPart,
            ToolCallPart,
            ToolReturnPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset

        returns: list[ToolReturnPart] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            parts = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if not parts:
                return ModelResponse(parts=[ToolCallPart("srv__echo", {"text": "x"})])
            returns.extend(parts)
            return ModelResponse(parts=[TextPart("understood")])

        toolset = as_pydantic_ai_toolset(_denying_rig().toolset())
        run = await Agent(FunctionModel(model), toolsets=[toolset]).run("go")
        assert run.output == "understood"
        (ret,) = returns
        assert (ret.content, ret.outcome) == (DENIAL, "failed")

    async def test_relisted_per_model_request(self) -> None:
        """S-6 #8, REQ-TLS-004: get_tools runs once per model request; a tool added
        between two requests of one run is in AgentInfo.function_tools of the second."""
        from pydantic_ai import Agent
        from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from reflex_mcphost.adapters.pydantic_ai import HostToolset, as_pydantic_ai_toolset

        rig = Rig()
        rig.catalog.entries = [tool("srv::echo")]
        seen: list[set[str]] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append({t.name for t in info.function_tools})
            if len(seen) == 1:
                rig.catalog.entries.append(tool("srv::added"))
                return ModelResponse(parts=[ToolCallPart("srv__echo", {"text": "x"})])
            if len(seen) == 2:
                return ModelResponse(parts=[ToolCallPart("srv__added", {"text": "y"})])
            return ModelResponse(parts=[TextPart("done")])

        adapter = as_pydantic_ai_toolset(rig.toolset())
        assert isinstance(adapter, HostToolset)
        listings = 0
        original = adapter.get_tools

        async def counting(ctx: typing.Any) -> typing.Any:
            nonlocal listings
            listings += 1
            return await original(ctx)

        adapter.get_tools = counting  # type: ignore[method-assign]
        run = await Agent(FunctionModel(model), toolsets=[adapter]).run("go")
        assert run.output == "done"
        assert seen[0] == {"srv__echo"}
        assert seen[1] == {"srv__echo", "srv__added"}
        assert listings == len(seen) == 3
        assert [str(c[0]) for c in rig.invoker.calls] == ["srv::echo", "srv::added"]

    def test_is_an_abstract_toolset_accepted_by_agent(self) -> None:
        """S-6 #9, REQ-TLS-006: the adapter is an AbstractToolset and Agent accepts it."""
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.toolsets import AbstractToolset

        from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset

        adapter = as_pydantic_ai_toolset(_denying_rig().toolset())
        assert isinstance(adapter, AbstractToolset)
        Agent(TestModel(), toolsets=[adapter])


class TestLangChain:
    @pytest.fixture(autouse=True)
    def _requires_extra(self) -> None:
        pytest.importorskip("langchain_core", reason="extra 'langgraph' not installed")

    def test_base_tool(self) -> None:
        """S-6 #10-11: BaseTool abstract members and the fields the adapter sets."""
        from langchain_core.tools import BaseTool, ToolException  # noqa: F401

        assert BaseTool.__abstractmethods__ == frozenset({"_run"})
        fields = BaseTool.model_fields
        assert {"args_schema", "handle_tool_error", "metadata"} <= set(fields)
        annotation = str(fields["args_schema"].annotation)
        assert "dict[str, typing.Any]" in annotation or "Dict[str, Any]" in annotation

    def test_private_args_hook(self) -> None:
        """S-6 #12: private _to_args_and_kwargs signature the adapter overrides."""
        from langchain_core.tools import BaseTool

        sig = inspect.signature(BaseTool._to_args_and_kwargs)
        assert list(sig.parameters) == ["self", "tool_input", "tool_call_id"]
        assert typing.get_type_hints(BaseTool._to_args_and_kwargs)  # resolvable annotations

    def test_openai_parameters_are_the_input_schema(self) -> None:
        """S-6 #13, REQ-TLS-007: the dict args_schema is emitted unchanged as parameters."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        from reflex_mcphost.adapters.langgraph import as_langchain_tools

        rig = Rig()
        rig.catalog.entries = [tool("srv::echo")]
        (lc_tool,) = as_langchain_tools(rig.toolset())
        (spec,) = rig.toolset().list()
        assert convert_to_openai_tool(lc_tool)["function"]["parameters"] == spec.input_schema

    async def test_tool_call_returns_tool_messages(self) -> None:
        """S-6 #14, REQ-TLS-005: ainvoke with a ToolCall → ToolMessage(status="success"),
        and for a denial ToolMessage(content=<denial text>, status="error")."""
        from reflex_mcphost.adapters.langgraph import as_langchain_tools

        rig = Rig()
        rig.catalog.entries = [tool("srv::echo")]
        (ok,) = as_langchain_tools(rig.toolset())
        call = {"type": "tool_call", "id": "1", "name": "srv__echo", "args": {"text": "x"}}
        message = await ok.ainvoke(call)
        assert (message.content, message.status) == ("ok", "success")
        (denied,) = as_langchain_tools(_denying_rig().toolset())
        message = await denied.ainvoke(call)
        assert (message.content, message.status) == (DENIAL, "error")

    async def test_reserved_argument_names_pass_through(self) -> None:
        """S-6 #15, REQ-TLS-007: arguments named config, run_manager, callbacks and self
        reach Toolset.call unchanged."""
        from reflex_mcphost.adapters.langgraph import as_langchain_tools

        names = ("config", "run_manager", "callbacks", "self", "q")
        schema = {"type": "object", "properties": {n: {"type": "string"} for n in names}}
        rig = Rig()
        rig.catalog.entries = [tool("srv::tricky", schema=schema)]
        (lc_tool,) = as_langchain_tools(rig.toolset())
        args = {n: f"value-{n}" for n in names}
        call = {"type": "tool_call", "id": "1", "name": "srv__tricky", "args": args}
        message = await lc_tool.ainvoke(call)
        assert message.status == "success"
        assert [c[1] for c in rig.invoker.calls] == [args]

    async def test_tool_node_turns_denial_into_error_message(self) -> None:
        """S-6 #16, REQ-TLS-005: a one-node StateGraph(MessagesState) with ToolNode
        (default handle_tool_errors) returns ToolMessage(status="error") and does not
        raise."""
        pytest.importorskip("langgraph", reason="langgraph (test group) not installed")
        from langchain_core.messages import AIMessage, ToolMessage
        from langgraph.graph import START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        from reflex_mcphost.adapters.langgraph import as_langchain_tools

        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode(as_langchain_tools(_denying_rig().toolset())))
        graph.add_edge(START, "tools")
        call = {"id": "1", "name": "srv__echo", "args": {"text": "x"}}
        state = await graph.compile().ainvoke(
            {"messages": [AIMessage(content="", tool_calls=[call])]}
        )
        reply = state["messages"][-1]
        assert isinstance(reply, ToolMessage)
        assert (reply.content, reply.status) == (DENIAL, "error")

    def test_snapshot_semantics(self) -> None:
        """S-6 #17, REQ-TLS-004, REQ-TLS-007: every element is a BaseTool; calling
        as_langchain_tools again after a catalog change returns the new set."""
        from langchain_core.tools import BaseTool

        from reflex_mcphost.adapters.langgraph import as_langchain_tools

        rig = Rig()
        rig.catalog.entries = [tool("srv::echo")]
        toolset = rig.toolset()
        first = as_langchain_tools(toolset)
        assert all(isinstance(t, BaseTool) for t in first)
        rig.catalog.entries.append(tool("srv::added"))
        assert [t.name for t in first] == ["srv__echo"]
        assert [t.name for t in as_langchain_tools(toolset)] == ["srv__echo", "srv__added"]
