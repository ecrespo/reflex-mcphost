"""PydanticAI adapter over a Toolset with fake catalog and Invoker (F5-03; API §4.13)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pydantic_ai", reason="extra 'pydantic-ai' not installed")

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset

from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.toolset import outcome_of
from tests.unit.toolset.fakes import Rig, result, tool

pytestmark = pytest.mark.anyio


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
    ]


async def test_tools_mirror_the_listing() -> None:
    """REQ-TLS-006: get_tools exposes every ToolSpec as a PydanticAI tool with the raw
    JSON schema, the description and the FQN in metadata."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo"), tool("srv::get.item")]
    adapter = as_pydantic_ai_toolset(rig.toolset())
    assert isinstance(adapter, AbstractToolset)
    assert adapter.id is None
    tools = await adapter.get_tools(None)  # type: ignore[arg-type]
    specs = {s.name: s for s in rig.toolset().list()}
    assert set(tools) == set(specs)
    for name, ts_tool in tools.items():
        assert ts_tool.tool_def.name == name
        assert ts_tool.tool_def.parameters_json_schema == specs[name].input_schema
        assert ts_tool.tool_def.description == specs[name].description
        assert ts_tool.tool_def.metadata == {"fqn": str(specs[name].fqn)}
        assert ts_tool.max_retries == 1
        assert ts_tool.args_validator.validate_python({"anything": [1]}) == {"anything": [1]}


async def test_test_model_calls_the_tool() -> None:
    """REQ-TLS-006: an Agent with TestModel calls the tool through Toolset.call and gets
    the outcome text back as a successful tool return."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    agent = Agent(
        TestModel(call_tools=["srv__echo"]), toolsets=[as_pydantic_ai_toolset(rig.toolset())]
    )
    run = await agent.run("go")
    (ret,) = _returns(run.all_messages())
    assert (ret.tool_name, ret.content, ret.outcome) == ("srv__echo", "ok", "success")
    assert len(rig.invoker.calls) == 1


async def test_vanished_alias_is_a_tool_failure() -> None:
    """REQ-TLS-005 (PydanticAI channel): a tool that disappears between get_tools and
    call_tool comes back as a failed tool return ("no longer available"); the run goes
    on."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not _returns(messages):
            rig.catalog.entries.clear()  # gone before the tool runs
            return ModelResponse(parts=[ToolCallPart("srv__echo", {"text": "x"})])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model), toolsets=[as_pydantic_ai_toolset(rig.toolset())])
    run = await agent.run("go")
    (ret,) = _returns(run.all_messages())
    assert ret.outcome == "failed"
    assert ret.content == "Tool is no longer available: srv__echo"
    assert run.output == "done"
    assert rig.invoker.calls == []


@pytest.mark.parametrize(
    "outcome", [o for o in InvocationOutcome if o is not InvocationOutcome.success]
)
async def test_every_error_outcome_is_a_failed_return(outcome: InvocationOutcome) -> None:
    """REQ-TLS-005: every error outcome reaches the model as ToolReturnPart(outcome=
    "failed") with the Toolset's text, and the run completes."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    rig.invoker.reply = result(outcome, error="why")
    seen: list[Any] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not _returns(messages):
            return ModelResponse(parts=[ToolCallPart("srv__echo", {"text": "x"})])
        seen.extend(_returns(messages))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model), toolsets=[as_pydantic_ai_toolset(rig.toolset())])
    run = await agent.run("go")
    assert run.output == "done"
    (ret,) = seen
    assert ret.outcome == "failed"
    assert ret.content == outcome_of(rig.invoker.reply).text
