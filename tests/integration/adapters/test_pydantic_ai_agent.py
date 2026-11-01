"""A PydanticAI agent calling a fixture tool through consent, both eras (F5-03).

No real LLM: ``TestModel`` calls the tool with arguments generated from its JSON schema.
The agent code here is the snippet shown in ``docs/library.md`` and the README.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai", reason="extra 'pydantic-ai' not installed")

from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel

from reflex_mcphost import Consumer
from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset
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


@pytest.mark.parametrize(
    ("decision", "outcome"),
    [(ConsentDecision.ALLOW_ONCE, "success"), (ConsentDecision.DENY, "failed")],
)
async def test_agent_calls_fixture_tool_through_consent(
    setup: Setup, era_server: tuple[str, str], decision: ConsentDecision, outcome: str
) -> None:
    """REQ-TLS-006, REQ-TLS-005, REQ-CNS-015: Agent(TestModel) calls `<id>__echo` through
    the Host; an allowing prompter → the echoed text as a successful tool return and one
    audit record for agent:helper; a denying prompter → a failed tool return with the
    denial text, the run still completes, and the audit record is `denied`."""
    prompter = FakePrompter(decision=decision)
    async with host_for(setup, era_server, prompter=prompter) as (host, server_id):
        tools = as_pydantic_ai_toolset(host.toolset(Consumer.agent("helper"), include=[server_id]))
        agent = Agent(TestModel(call_tools=[f"{server_id}__echo"]), toolsets=[tools])
        run = await agent.run("Echo something")

        returns = [
            part
            for message in run.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        (ret,) = returns
        assert (ret.tool_name, ret.outcome) == (f"{server_id}__echo", outcome)
        (record,) = await records(host)
        assert record.consumer == Consumer.agent("helper")
        (asked,) = prompter.asked
        assert asked.consumer == Consumer.agent("helper")
        assert run.output  # the run completed with a final answer
        if decision is ConsentDecision.DENY:
            assert ret.content.startswith("Permission denied by the host: ")
            assert record.outcome is InvocationOutcome.denied
        else:
            assert ret.content == "a"  # TestModel's generated string argument, echoed
            assert record.outcome is InvocationOutcome.success
