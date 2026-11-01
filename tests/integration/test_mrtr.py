"""The ``tools/call`` MRTR loop against the modern fixture Server (F3-10; Flow C step 6).

The Host here declares elicitation (an eliciting Prompter) and sampling (a provider), and
the router slots are then replaced by fake handlers to exercise the rounds; the real
handlers are covered in ``tests/integration/handlers/``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.consent.models import ConsentDecision
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.mrtr import (
    ELICITATION,
    SAMPLING,
    InputContext,
    SamplingDeniedError,
    current_consumer,
)
from reflex_mcphost.core.types import Consumer
from tests.integration.test_host import MODERN_TABLE, Setup, setup
from tests.integration.test_invoker import BOT, FakePrompter, call_counts, running_host

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["setup"]  # re-exported fixture


async def name_handler(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
    return types.ElicitResult(action="accept", content={"name": "Ada"})


async def sample_handler(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
    return types.CreateMessageResult(
        role="assistant", content=types.TextContent(type="text", text="Hi Ada!"), model="fake"
    )


class _Provider:
    async def create_message(
        self, params: types.CreateMessageRequestParams, *, server_id: str
    ) -> types.CreateMessageResult:
        raise AssertionError("the router slot is replaced in these tests")


def _declaring(prompter: FakePrompter | None = None) -> dict[str, Any]:
    """Host kwargs declaring elicitation and sampling (DD-003: declared iff available)."""
    prompter = prompter or FakePrompter()
    prompter.supports_elicitation = True
    return {"prompter": prompter, "sampling_provider": _Provider()}


@pytest.fixture
async def host(setup: Setup) -> AsyncIterator[Host]:
    async with running_host(setup, {"srv": MODERN_TABLE}, **_declaring()) as h:
        yield h


async def _record_of(host: Host, invocation_id: str) -> tuple[Out, str | None, int]:
    record = await host.audit.get(invocation_id)
    return record.outcome, record.error_code, record.input_rounds


async def test_undeclared_input_request(setup: Setup) -> None:
    """REQ-HSH-044: with elicitation undeclared (the default Prompter does not elicit)
    ask_name's MRTR elicitation stops the call with protocol_error / undeclared_input_request."""
    async with running_host(setup, {"srv": MODERN_TABLE}) as host:
        result = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
        record = await _record_of(host, result.id)
    assert result.outcome is Out.protocol_error
    assert result.error is not None
    assert result.error.code == "undeclared_input_request"
    assert result.error.details == {"method": ELICITATION}
    assert record == (Out.protocol_error, "undeclared_input_request", 0)


async def test_fulfilled_rounds_are_counted(host: Host) -> None:
    """REQ-HSH-040, REQ-INV-009: handlers fulfil each round, the call is retried to its final
    result and the fulfilled rounds are recorded as input_rounds."""
    seen: list[Consumer | None] = []

    async def elicit(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        seen.append(current_consumer.get())
        assert ctx.invocation_id is not None
        assert ctx.server_id == "srv"
        return await name_handler(ctx, request)

    host.input_router.set_handler(ELICITATION, elicit)
    host.input_router.set_handler(SAMPLING, sample_handler)
    one = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
    two = await host.invoker.call("srv::two_rounds", {}, consumer=BOT)
    assert (one.outcome, one.text) == (Out.success, "hello Ada")
    assert (two.outcome, two.text) == (Out.success, "Ada -> Hi Ada!")
    assert await _record_of(host, one.id) == (Out.success, None, 1)
    assert await _record_of(host, two.id) == (Out.success, None, 2)
    assert seen == [BOT, BOT]


async def test_round_cap(host: Host) -> None:
    """REQ-HSH-043: a tool needing 9 rounds ends protocol_error / input_rounds_exceeded with
    input_rounds = 8; 8 rounds still complete."""
    host.input_router.set_handler(ELICITATION, name_handler)
    over = await host.invoker.call("srv::rounds", {"n": 9}, consumer=BOT)
    assert over.outcome is Out.protocol_error
    assert over.error is not None
    assert over.error.code == "input_rounds_exceeded"
    assert await _record_of(host, over.id) == (Out.protocol_error, "input_rounds_exceeded", 8)
    within = await host.invoker.call("srv::rounds", {"n": 8}, consumer=BOT)
    assert (within.outcome, within.text) == (Out.success, "done after 8")
    assert await _record_of(host, within.id) == (Out.success, None, 8)


async def test_request_state_echoed_byte_identical(host: Host) -> None:
    """REQ-HSH-041: requestState is passed back exactly as received (never parsed or
    modified)."""
    host.input_router.set_handler(ELICITATION, name_handler)
    result = await host.invoker.call("srv::opaque_state", {}, consumer=BOT)
    assert result.ok, result.error
    echoed = json.loads(result.text)
    assert echoed["same"] is True
    assert echoed["state"].encode("utf-8") == (
        'opaque:é "q" \\ \n\t✓ {"not": "json"} \U0001f600'.encode()
    )


async def test_state_only_results_are_rounds(host: Host) -> None:
    """REQ-HSH-040, REQ-HSH-043 (spike S-1): state-only results are retried after a backoff
    and count as rounds."""
    result = await host.invoker.call("srv::state_only", {"n": 3}, consumer=BOT)
    assert (result.outcome, result.text) == (Out.success, "state-only 3")
    assert await _record_of(host, result.id) == (Out.success, None, 3)


async def test_consent_asked_once_across_rounds(setup: Setup) -> None:
    """REQ-HSH-042: tool-call consent is evaluated once per invocation; MRTR retries of the
    allowed call do not re-prompt."""
    prompter = FakePrompter(decision=ConsentDecision.ALLOW_ONCE)
    async with running_host(
        setup, {"srv": MODERN_TABLE}, allow=False, **_declaring(prompter)
    ) as host:
        host.input_router.set_handler(ELICITATION, name_handler)
        host.input_router.set_handler(SAMPLING, sample_handler)
        result = await host.invoker.call("srv::two_rounds", {}, consumer=BOT)
    assert result.ok, result.error
    assert len(prompter.asked) == 1
    assert result.consent is not None
    assert result.consent.source == "prompt"


async def test_sampling_denied_sends_nothing_more(host: Host) -> None:
    """Flow C, API §4.8 step 6: a refused sampling ends denied / sampling_denied and the call
    is not retried (client-side abort)."""

    async def deny(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        raise SamplingDeniedError("denied")

    host.input_router.set_handler(ELICITATION, name_handler)
    host.input_router.set_handler(SAMPLING, deny)
    result = await host.invoker.call("srv::two_rounds", {}, consumer=BOT)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "sampling_denied"
    assert await _record_of(host, result.id) == (Out.denied, "sampling_denied", 1)
    assert (await call_counts(host, "srv"))["two_rounds"] == 2  # first leg + one retry only


async def test_header_mismatch_relist_and_resend(host: Host) -> None:
    """Flow C (spike S-1): a modern HEADER_MISMATCH (-32020) re-lists the tools once and
    resends the call once."""
    result = await host.invoker.call("srv::mismatch", {}, consumer=BOT)
    assert result.ok, result.error
    assert (await call_counts(host, "srv"))["mismatch"] == 2


async def test_input_handler_wait_excluded_from_timeout(host: Host) -> None:
    """DD-004: the timeout covers MRTR rounds but not handler time spent waiting on the
    Prompter (``prompter_wait``); handler time outside it does count."""

    async def patient(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        with ctx.prompter_wait():
            await anyio.sleep(0.8)
        return await name_handler(ctx, request)

    async def busy(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        await anyio.sleep(0.8)
        return await name_handler(ctx, request)

    host.input_router.set_handler(ELICITATION, patient)
    waited = await host.invoker.call("srv::ask_name", {}, consumer=BOT, timeout=0.5)
    assert waited.outcome is Out.success, waited.error
    host.input_router.set_handler(ELICITATION, busy)
    timed_out = await host.invoker.call("srv::ask_name", {}, consumer=BOT, timeout=0.5)
    assert timed_out.outcome is Out.timeout


async def test_broken_stream_is_connection_lost(host: Host) -> None:
    """REQ-INV-015: a tools/call whose stream breaks before the final response ends
    connection_lost (the loop never re-issues it; pinned in tests/unit/core/test_mrtr.py)."""
    result = await host.invoker.call("srv::crash", {}, consumer=BOT)
    assert result.outcome is Out.connection_lost
    assert result.error is not None
    assert result.error.code == "connection_lost"
    record = await host.audit.get(result.id)
    assert record.outcome is Out.connection_lost
