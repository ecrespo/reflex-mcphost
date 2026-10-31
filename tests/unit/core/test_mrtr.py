"""MRTR loop, ``InputRequestRouter`` and the pausable deadline, against scripted legs (F3-10).

The integration counterpart against the modern fixture Server is
``tests/integration/test_mrtr.py``; these tests pin the loop's arithmetic without I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
import anyio.lowlevel
import mcp_types as types
import pytest
from mcp import MCPError

from reflex_mcphost.core.mrtr import (
    ELICITATION,
    MAX_INPUT_ROUNDS,
    ROOTS,
    SAMPLING,
    InputContext,
    InputHandlerError,
    InputRequestRouter,
    InputRoundsExceededError,
    LoopState,
    PausableDeadline,
    SamplingDeniedError,
    UndeclaredInputRequestError,
    call_tool_leg,
    consumer_scope,
    current_consumer,
    drive,
)
from reflex_mcphost.core.types import Consumer

pytestmark = pytest.mark.anyio
BOT = Consumer.agent("bot")


def _elicit() -> types.ElicitRequest:
    return types.ElicitRequest(
        params=types.ElicitRequestFormParams(
            message="name?",
            requested_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        )
    )


def _ask(state: str | None, **requests: types.InputRequest) -> types.InputRequiredResult:
    return types.InputRequiredResult(input_requests=requests or None, request_state=state)


def _done(text: str = "done") -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


@dataclass
class ScriptedLeg:
    """Returns the scripted results in order and records every leg's arguments."""

    script: Sequence[Any]
    calls: list[tuple[types.InputResponses | None, str | None]] = field(default_factory=list)

    async def __call__(
        self, responses: types.InputResponses | None, state: str | None
    ) -> types.Result:
        self.calls.append((responses, state))
        item = self.script[len(self.calls) - 1]
        if isinstance(item, BaseException):
            raise item
        result: types.Result = item
        return result


async def _accept(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
    return types.ElicitResult(action="accept", content={"name": ctx.key})


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await anyio.lowlevel.checkpoint()


def _router(**handlers: Any) -> InputRequestRouter:
    return InputRequestRouter({ELICITATION: handlers.get("elicit", _accept)})


async def test_terminal_first_result_needs_no_round() -> None:
    """REQ-INV-014: a result without resultType "input_required" is complete at once."""
    leg = ScriptedLeg([_done()])
    state = LoopState()
    result = await drive(leg, router=InputRequestRouter(), server_id="s", state=state)
    assert isinstance(result, types.CallToolResult)
    assert (state.rounds, leg.calls) == (0, [(None, None)])


async def test_rounds_fulfil_every_request_and_echo_state() -> None:
    """REQ-HSH-040, REQ-HSH-041: every inputRequests entry is fulfilled, the retry carries the
    inputResponses map and the exact requestState; fulfilled rounds are counted."""
    opaque = 'opaque:é "x" \\ \n\U0001f600'
    leg = ScriptedLeg([_ask(opaque, a=_elicit(), b=_elicit()), _ask("2", c=_elicit()), _done()])
    state = LoopState()
    await drive(leg, router=_router(), server_id="s", state=state)
    assert state.rounds == 2
    first, second, third = leg.calls
    assert first == (None, None)
    responses, echoed = second
    assert echoed == opaque  # byte-identical, never parsed
    assert set(responses or {}) == {"a", "b"}
    assert (responses or {})["a"] == types.ElicitResult(action="accept", content={"name": "a"})
    assert set(third[0] or {}) == {"c"}  # responses only for the request that asked
    assert third[1] == "2"


async def test_round_cap_is_eight() -> None:
    """REQ-HSH-043: the 9th consecutive input_required stops the loop with
    input_rounds_exceeded after 8 fulfilled rounds."""
    leg = ScriptedLeg([_ask(str(i), r=_elicit()) for i in range(MAX_INPUT_ROUNDS + 1)])
    state = LoopState()
    with pytest.raises(InputRoundsExceededError) as info:
        await drive(leg, router=_router(), server_id="s", state=state)
    assert MAX_INPUT_ROUNDS == 8
    assert info.value.rounds == state.rounds == 8
    assert len(leg.calls) == 9


async def test_eight_rounds_still_complete() -> None:
    """REQ-HSH-043: exactly 8 rounds is within the cap."""
    script: list[Any] = [_ask(str(i), r=_elicit()) for i in range(MAX_INPUT_ROUNDS)]
    leg = ScriptedLeg([*script, _done()])
    state = LoopState()
    await drive(leg, router=_router(), server_id="s", state=state)
    assert state.rounds == 8


async def test_state_only_results_back_off_and_count_as_rounds() -> None:
    """REQ-HSH-040, REQ-HSH-043: a state-only result is retried with no inputResponses after
    a doubling backoff (50 ms → 250 ms cap); each retry counts as a round."""
    sleep = FakeSleep()
    leg = ScriptedLeg([_ask(str(i)) for i in range(5)] + [_done()])
    state = LoopState()
    await drive(leg, router=InputRequestRouter(), server_id="s", state=state, sleep=sleep)
    assert sleep.delays == [0.05, 0.1, 0.2, 0.25, 0.25]
    assert state.rounds == 5
    assert all(responses is None for responses, _ in leg.calls)
    assert [s for _, s in leg.calls] == [None, "0", "1", "2", "3", "4"]


async def test_backoff_resets_after_a_round_with_requests() -> None:
    sleep = FakeSleep()
    leg = ScriptedLeg([_ask("0"), _ask("1"), _ask("2", r=_elicit()), _ask("3"), _done()])
    await drive(leg, router=_router(), server_id="s", sleep=sleep)
    assert sleep.delays == [0.05, 0.1, 0.05]


async def test_undeclared_method_stops_before_any_handler_runs() -> None:
    """REQ-HSH-044: an inputRequests entry whose method has no handler stops the loop with
    undeclared_input_request; no handler of the round runs and no retry is sent."""
    asked: list[str] = []

    async def elicit(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        asked.append(ctx.key)
        return types.ElicitResult(action="decline")

    sample = types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(messages=[], max_tokens=1)
    )
    leg = ScriptedLeg([_ask("s", a=_elicit(), b=sample)])
    with pytest.raises(UndeclaredInputRequestError) as info:
        await drive(leg, router=_router(elicit=elicit), server_id="s")
    assert info.value.method == SAMPLING
    assert asked == []
    assert len(leg.calls) == 1


async def test_empty_router_declares_nothing() -> None:
    """REQ-HSH-044: in Phase 3 no handler slot is filled, so every method is undeclared."""
    router = InputRequestRouter()
    assert router.declared == frozenset()
    with pytest.raises(UndeclaredInputRequestError) as info:
        await drive(ScriptedLeg([_ask("s", r=_elicit())]), router=router, server_id="s")
    assert info.value.method == ELICITATION
    router.set_handler(ROOTS, _accept)
    assert router.declared == frozenset({ROOTS})
    router.set_handler(ROOTS, None)
    assert router.declared == frozenset()
    with pytest.raises(ValueError, match="unknown input request method"):
        router.set_handler("tools/call", _accept)


async def test_sampling_denied_aborts_without_retry() -> None:
    """Flow C (spike S-1): a refused sampling is a client-side abort — nothing more is sent
    to the Server."""

    async def deny(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        raise SamplingDeniedError("denied by consent")

    sample = types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(messages=[], max_tokens=1)
    )
    leg = ScriptedLeg([_ask("s", llm=sample)])
    with pytest.raises(SamplingDeniedError):
        await drive(leg, router=InputRequestRouter({SAMPLING: deny}), server_id="s")
    assert len(leg.calls) == 1


async def test_failing_handler_aborts_with_input_handler_error() -> None:
    """Fail closed: a handler that raises aborts the loop; ErrorData is never sent."""

    async def boom(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        raise RuntimeError("handler bug")

    leg = ScriptedLeg([_ask("s", r=_elicit())])
    with pytest.raises(InputHandlerError) as info:
        await drive(leg, router=_router(elicit=boom), server_id="s")
    assert info.value.method == ELICITATION
    assert len(leg.calls) == 1


async def test_handlers_see_server_consumer_and_contextvar() -> None:
    """API §4.6: the Consumer of input requests is visible to handlers (context and the
    ``current_consumer`` ContextVar)."""
    seen: list[tuple[str, Consumer | None, Consumer | None, str]] = []

    async def record(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        seen.append((ctx.server_id, ctx.consumer, current_consumer.get(), ctx.method))
        return types.ElicitResult(action="cancel")

    assert current_consumer.get() is None
    with consumer_scope(BOT):
        await drive(
            ScriptedLeg([_ask("s", r=_elicit()), _done()]),
            router=_router(elicit=record),
            server_id="srv",
            consumer=BOT,
        )
    assert current_consumer.get() is None
    assert seen == [("srv", BOT, BOT, ELICITATION)]


async def test_legs_errors_propagate_without_reissue() -> None:
    """REQ-INV-015: a broken stream (CONNECTION_CLOSED) surfaces once; the loop never
    re-issues the leg."""
    leg = ScriptedLeg([MCPError(types.CONNECTION_CLOSED, "Connection closed")])
    with pytest.raises(MCPError):
        await drive(leg, router=InputRequestRouter(), server_id="s")
    assert len(leg.calls) == 1


# ------------------------------------------------------------------ HEADER_MISMATCH


@dataclass
class FakeSession:
    script: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call_tool(self, name: str, arguments: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"name": name, "arguments": arguments, **kwargs})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@dataclass
class FakeClient:
    session: FakeSession
    lists: int = 0

    async def list_tools(self, *, cursor: str | None = None, cache_mode: str = "use") -> Any:
        self.lists += 1
        assert cache_mode == "refresh"
        return types.ListToolsResult(tools=[])


def _mismatch() -> MCPError:
    return MCPError(types.HEADER_MISMATCH, "header mismatch")


async def test_header_mismatch_relists_once_and_resends_once() -> None:
    """Flow C (spike S-1): modern HEADER_MISMATCH (-32020) → one tools/list refresh and one
    resend; progress is passed by keyword on every leg."""
    session = FakeSession([_mismatch(), _done()])
    client = FakeClient(session)

    async def progress(p: float, total: float | None, message: str | None) -> None:
        return None

    leg = call_tool_leg(client, "t", {"a": 1}, progress_callback=progress, modern=True)
    result = await leg(None, None)
    assert isinstance(result, types.CallToolResult)
    assert client.lists == 1
    assert len(session.calls) == 2
    assert all(c["progress_callback"] is progress for c in session.calls)
    assert all(c["allow_input_required"] is True for c in session.calls)


async def test_second_header_mismatch_is_raised() -> None:
    client = FakeClient(FakeSession([_mismatch(), _mismatch()]))
    leg = call_tool_leg(client, "t", {}, progress_callback=None, modern=True)
    with pytest.raises(MCPError) as info:
        await leg(None, None)
    assert info.value.code == types.HEADER_MISMATCH
    assert client.lists == 1


async def test_header_mismatch_on_legacy_is_not_retried() -> None:
    client = FakeClient(FakeSession([_mismatch()]))
    leg = call_tool_leg(client, "t", {}, progress_callback=None, modern=False)
    with pytest.raises(MCPError):
        await leg(None, None)
    assert client.lists == 0


# ------------------------------------------------------------------ PausableDeadline


async def test_deadline_expires() -> None:
    """DD-004: the deadline cancels the running scope when the budget is spent."""
    deadline = PausableDeadline(0.05)
    with deadline.running():
        await anyio.sleep(1)
    assert deadline.expired


async def test_paused_time_is_excluded() -> None:
    """DD-004, API §4.8: time spent waiting on the Prompter does not consume the budget."""
    deadline = PausableDeadline(0.2)
    with deadline.running():
        await anyio.sleep(0.05)
        with deadline.paused():
            await anyio.sleep(0.4)  # longer than the whole budget
        with deadline.paused(), deadline.paused():  # nested pauses
            await anyio.sleep(0.05)
        await anyio.sleep(0.05)
    assert not deadline.expired
    assert deadline.paused_s >= 0.4
    assert 0.0 < deadline.remaining() < 0.2


async def test_reset_restores_the_full_budget() -> None:
    """REQ-INV-005: with reset-on-progress the budget restarts at each progress."""
    deadline = PausableDeadline(0.15)
    with deadline.running():
        for _ in range(4):
            await anyio.sleep(0.08)
            deadline.reset()
    assert not deadline.expired
    with deadline.paused():
        deadline.reset()  # outside running(): harmless


async def test_unbounded_deadline_never_expires() -> None:
    deadline = PausableDeadline(None)
    with deadline.running():
        await anyio.sleep(0.01)
        deadline.reset()
    assert not deadline.expired
    assert deadline.remaining() == float("inf")
