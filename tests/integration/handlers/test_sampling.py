"""Sampling against the modern fixture Server (F4-02; legacy path in
``test_legacy_requests.py``). *Sampling is deprecated in the pinned spec; compatibility.*"""

from __future__ import annotations

import json

import pytest

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.consent.models import ConsentDecision, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.prompter import ElicitationResponse
from reflex_mcphost.core.types import Consumer
from tests.integration.handlers.helpers import (
    FakeProvider,
    HandlerPrompter,
    handler_host,
    modern_stdio_table,
    modern_table,
)
from tests.integration.test_host import Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
OTHER = Consumer.agent("other")
NAME = ElicitationResponse(action="accept", content={"name": "Ada"})
__all__ = ["modern_table", "setup"]


async def _grant_sampling(host: Host, consumer: Consumer | None = None) -> None:
    await host.consent.grant(
        effect="allow",
        operation=OperationKind.SAMPLING,
        target=ConsentTarget.server("srv"),
        consumer=consumer,
    )


async def _calls(host: Host, tool: str) -> int:
    result = await host.invoker.call("srv::call_counts", {}, consumer=BOT)
    return int(json.loads(result.text)[tool])


async def test_allowed_sampling_returns_and_is_audited(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-001, REQ-HSH-003: with an allow rule the provider's completion reaches the
    Server; audit rows exist for the tool call and for the sampling (fqn None, same
    Consumer)."""
    provider = FakeProvider()
    prompter = HandlerPrompter([NAME])
    async with handler_host(
        setup, {"srv": modern_table}, prompter=prompter, provider=provider
    ) as host:
        await _grant_sampling(host)
        result = await host.invoker.call("srv::two_rounds", {}, consumer=BOT)
        tool = await host.audit.get(result.id)
        sampling = (await host.audit.query(AuditQuery(operation=OperationKind.SAMPLING))).records
    assert (result.outcome, result.text) == (Out.success, "Ada -> Hi Ada!")
    assert (tool.outcome, tool.input_rounds) == (Out.success, 2)
    (row,) = sampling
    assert (row.outcome, row.fqn, row.server_id, row.consumer) == (Out.success, None, "srv", BOT)
    assert row.decision_source == "rule"
    assert provider.calls[0][1] == "srv"
    assert prompter.sampling_asks() == []


async def test_denied_sampling_ends_the_call_denied(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-005: a denied sampling ends the tool call denied / sampling_denied, the call
    is not retried and the denial is audited."""
    provider = FakeProvider()
    prompter = HandlerPrompter([NAME], decision=ConsentDecision.DENY)
    async with handler_host(
        setup, {"srv": modern_table}, prompter=prompter, provider=provider
    ) as host:
        result = await host.invoker.call("srv::sample", {}, consumer=BOT)
        calls = await _calls(host, "sample")
        sampling = (await host.audit.query(AuditQuery(operation=OperationKind.SAMPLING))).records
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "sampling_denied"
    assert calls == 1  # first leg only: no retry
    assert provider.calls == []
    assert [(r.outcome, r.error_code) for r in sampling] == [(Out.denied, "sampling_denied")]


async def test_rule_for_agent_a_does_not_apply_to_b(setup: Setup) -> None:
    """REQ-HSH-001 (REQ-CNS-015): an `always` sampling rule for agent A is not used for
    agent B, who is asked."""
    provider = FakeProvider()
    prompter = HandlerPrompter(decision=ConsentDecision.DENY)
    async with handler_host(
        setup, {"srv": modern_stdio_table()}, prompter=prompter, provider=provider
    ) as host:
        await _grant_sampling(host, BOT)
        allowed = await host.invoker.call("srv::sample", {}, consumer=BOT)
        denied = await host.invoker.call("srv::sample", {}, consumer=OTHER)
    assert (allowed.outcome, allowed.text) == (Out.success, "Hi Ada!")
    assert denied.outcome is Out.denied
    assert [r.consumer for r in prompter.sampling_asks()] == [OTHER]


async def test_review_is_asked_once(setup: Setup) -> None:
    """REQ-HSH-004: with review_sampling_responses the completion is reviewed once before
    it is returned; an edit replaces it."""
    from mcp_types import CreateMessageResult, TextContent

    from reflex_mcphost.core.prompter import SamplingVerdict

    edited = CreateMessageResult(
        role="assistant", content=TextContent(type="text", text="edited"), model="m"
    )
    prompter = HandlerPrompter(verdict=SamplingVerdict(approved=True, edited_result=edited))
    table = modern_stdio_table(review_sampling_responses=True)
    async with handler_host(
        setup, {"srv": table}, prompter=prompter, provider=FakeProvider()
    ) as host:
        await _grant_sampling(host)
        result = await host.invoker.call("srv::sample", {}, consumer=BOT)
    assert (result.outcome, result.text) == (Out.success, "edited")
    assert len(prompter.reviewed) == 1
    assert prompter.reviewed[0].server_id == "srv"


async def test_no_provider_is_undeclared(setup: Setup) -> None:
    """REQ-HSH-006, REQ-HSH-044: without a provider sampling is not declared; an MRTR
    sampling request is undeclared_input_request."""
    async with handler_host(
        setup, {"srv": modern_stdio_table()}, prompter=HandlerPrompter()
    ) as host:
        result = await host.invoker.call("srv::sample", {}, consumer=BOT)
    assert result.error is not None
    assert result.error.code == "undeclared_input_request"
