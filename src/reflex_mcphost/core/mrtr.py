"""The MRTR loop (Tech Design Flow C step 6), the ``InputRequestRouter`` and the pausable
deadline of DD-004.

A modern Server answers ``tools/call``, ``resources/read`` or ``prompts/get`` with
``resultType: "input_required"`` (SEP-2322). :func:`drive` fulfils every ``inputRequests``
entry through the router and retries the original request with a new JSON-RPC id, the
``inputResponses`` map and the exact ``requestState`` (REQ-HSH-040/041):

* at most ``MAX_INPUT_ROUNDS`` (8) fulfilled rounds; the 9th consecutive
  ``input_required`` raises :class:`InputRoundsExceededError` (REQ-HSH-043);
* a state-only result (``requestState`` without ``inputRequests``) is retried with no
  responses after a doubling backoff (50 ms → 250 ms, reset by a round with requests) and
  counts as a round (spike S-1);
* a method with no handler raises :class:`UndeclaredInputRequestError` before any handler
  of that round runs (REQ-HSH-044); a refused sampling is a client-side abort
  (:class:`SamplingDeniedError`, nothing is sent); ``ErrorData`` is never placed in
  ``inputResponses`` (a handler returning or raising one aborts with
  :class:`InputHandlerError`);
* a leg's exception (e.g. a broken stream) propagates once: legs are never re-issued here
  (REQ-INV-015).

Legacy Servers never return ``input_required``; their input requests arrive as
server-initiated requests through the SDK callbacks (Phase 4). The router's handler slots
(elicitation, sampling, roots) are empty in Phase 3, so every input request is undeclared.

Only this module and ``core.invoker`` call ``session.call_tool`` (Constitution Art. 3,
``tests/architecture/test_single_effect_path.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

import anyio
import mcp_types as types
from mcp import MCPError
from pydantic import ValidationError

from reflex_mcphost.core.errors import InputRequestCode, InputRequestError, MCPHostError
from reflex_mcphost.core.types import Consumer

__all__ = [
    "ELICITATION",
    "INPUT_METHODS",
    "MAX_INPUT_ROUNDS",
    "ROOTS",
    "SAMPLING",
    "InputContext",
    "InputHandler",
    "InputHandlerError",
    "InputRequestRouter",
    "InputRoundsExceededError",
    "Leg",
    "LoopState",
    "PausableDeadline",
    "SamplingDeniedError",
    "UndeclaredInputRequestError",
    "call_tool_leg",
    "consumer_scope",
    "current_consumer",
    "drive",
    "get_prompt",
    "read_resource",
]

MAX_INPUT_ROUNDS = 8
"""REQ-HSH-043: input rounds fulfilled per request before ``input_rounds_exceeded``."""
STATE_ONLY_BACKOFF_INITIAL_S = 0.05
STATE_ONLY_BACKOFF_CAP_S = 0.25
RELIST_PAGE_CAP = 100
"""Pages walked when re-listing tools after ``HEADER_MISMATCH``."""
ELICITATION = "elicitation/create"
SAMPLING = "sampling/createMessage"
ROOTS = "roots/list"
INPUT_METHODS = frozenset({ELICITATION, SAMPLING, ROOTS})

current_consumer: ContextVar[Consumer | None] = ContextVar("reflex_mcphost_consumer", default=None)
"""The Consumer of the request whose input requests are being handled (API §4.6). Visible
in the Host's own loop; legacy SDK callbacks do not see it (spike S-1)."""


@contextmanager
def consumer_scope(consumer: Consumer | None) -> Iterator[None]:
    """Set :data:`current_consumer` for the body."""
    token = current_consumer.set(consumer)
    try:
        yield
    finally:
        current_consumer.reset(token)


# ---------------------------------------------------------------------- errors (internal)


class UndeclaredInputRequestError(Exception):
    """An input request used a method the Host has no handler for (REQ-HSH-044)."""

    def __init__(self, method: str, key: str) -> None:
        self.method = method
        self.key = key
        super().__init__(f"undeclared input request method {method!r}")


class InputRoundsExceededError(Exception):
    """The Server kept asking past ``MAX_INPUT_ROUNDS`` (REQ-HSH-043)."""

    def __init__(self, rounds: int) -> None:
        self.rounds = rounds
        super().__init__(f"input_required after {rounds} fulfilled rounds")


class SamplingDeniedError(Exception):
    """A sampling input request was refused; the exchange is abandoned client-side."""


class InputHandlerError(MCPHostError):
    """An input handler failed or returned an error; nothing is sent (fail closed)."""

    def __init__(self, method: str, reason: str) -> None:
        self.method = method
        self.reason = reason
        super().__init__(f"input handler for {method!r} failed ({reason})")


# ---------------------------------------------------------------------- deadline (DD-004)


class PausableDeadline:
    """A call budget that stops running while the Host waits on the ``Prompter``.

    ``running()`` opens the cancel scope that enforces the budget; ``paused()`` (nestable,
    and safe from concurrent handlers) lifts the deadline for its body and restores the
    remaining budget afterwards; ``reset()`` restarts the full budget (REQ-INV-005).
    ``budget_s=None`` never expires.
    """

    def __init__(self, budget_s: float | None) -> None:
        self.budget_s = budget_s
        self._scope: anyio.CancelScope | None = None
        self._pauses = 0
        self._pause_started = 0.0
        self._held = budget_s if budget_s is not None else float("inf")
        self.paused_s = 0.0
        self.expired = False

    def remaining(self) -> float:
        """Budget left in seconds (``inf`` when unbounded)."""
        if self.budget_s is None:
            return float("inf")
        if self._scope is None or self._pauses:
            return self._held
        return max(0.0, self._scope.deadline - anyio.current_time())

    @contextmanager
    def running(self) -> Iterator[anyio.CancelScope]:
        deadline = anyio.current_time() + self.remaining()
        with anyio.CancelScope(deadline=deadline) as scope:
            self._scope = scope
            try:
                yield scope
            finally:
                self._held = self.remaining()
                self._scope = None
        self.expired = self.expired or scope.cancelled_caught

    @contextmanager
    def paused(self) -> Iterator[None]:
        scope = self._scope
        if scope is None:
            yield
            return
        if self._pauses == 0:
            self._held = self.remaining()
            self._pause_started = anyio.current_time()
            scope.deadline = float("inf")
        self._pauses += 1
        try:
            yield
        finally:
            self._pauses -= 1
            if self._pauses == 0:
                now = anyio.current_time()
                self.paused_s += now - self._pause_started
                if self._scope is scope and self.budget_s is not None:
                    scope.deadline = now + self._held

    def reset(self) -> None:
        if self.budget_s is None:
            return
        if self._scope is None or self._pauses:
            self._held = self.budget_s
        else:
            self._scope.deadline = anyio.current_time() + self.budget_s


# ---------------------------------------------------------------------- router


@dataclass(frozen=True)
class InputContext:
    """What a handler knows about the input request it serves."""

    server_id: str
    consumer: Consumer | None
    key: str
    method: str
    deadline: PausableDeadline | None = None
    invocation_id: str | None = None

    def prompter_wait(self) -> Any:
        """Context manager to wrap every await on the ``Prompter`` (DD-004)."""
        return self.deadline.paused() if self.deadline is not None else nullcontext()


class InputHandler(Protocol):
    async def __call__(
        self, ctx: InputContext, request: types.InputRequest
    ) -> types.InputResponse: ...


class InputRequestRouter:
    """Dispatches input requests per method to handler slots (Flow C step 6).

    Phase 4 fills the elicitation, sampling and roots slots; a method with an empty slot is
    undeclared (REQ-HSH-044).
    """

    def __init__(self, handlers: Mapping[str, InputHandler] | None = None) -> None:
        self._handlers: dict[str, InputHandler] = {}
        for method, handler in (handlers or {}).items():
            self.set_handler(method, handler)

    @property
    def declared(self) -> frozenset[str]:
        """Methods with a handler."""
        return frozenset(self._handlers)

    def set_handler(self, method: str, handler: InputHandler | None) -> None:
        if method not in INPUT_METHODS:
            raise ValueError(f"unknown input request method {method!r}")
        if handler is None:
            self._handlers.pop(method, None)
        else:
            self._handlers[method] = handler

    async def fulfil(
        self,
        requests: Mapping[str, types.InputRequest],
        *,
        server_id: str,
        consumer: Consumer | None,
        deadline: PausableDeadline | None = None,
        invocation_id: str | None = None,
    ) -> types.InputResponses:
        """Responses for every entry, run concurrently; the first failure cancels the rest."""
        plan: list[tuple[str, types.InputRequest, InputHandler]] = []
        for key, request in requests.items():
            handler = self._handlers.get(request.method)
            if handler is None:
                raise UndeclaredInputRequestError(request.method, key)
            plan.append((key, request, handler))
        responses: types.InputResponses = {}
        failure: Exception | None = None

        async with anyio.create_task_group() as tg:

            async def run(key: str, request: types.InputRequest, handler: InputHandler) -> None:
                nonlocal failure
                ctx = InputContext(
                    server_id=server_id,
                    consumer=consumer,
                    key=key,
                    method=request.method,
                    deadline=deadline,
                    invocation_id=invocation_id,
                )
                try:
                    response: object = await handler(ctx, request)
                    if isinstance(response, types.ErrorData):
                        raise InputHandlerError(request.method, "returned ErrorData")
                except (SamplingDeniedError, InputHandlerError) as exc:
                    failure = failure or exc
                    tg.cancel_scope.cancel()
                    return
                except Exception as exc:
                    failure = failure or InputHandlerError(request.method, type(exc).__name__)
                    tg.cancel_scope.cancel()
                    return
                responses[key] = response  # type: ignore[assignment]  # handler contract

            for key, request, handler in plan:
                tg.start_soon(run, key, request, handler)
        if failure is not None:
            raise failure
        return responses


# ---------------------------------------------------------------------- the loop

Leg: TypeAlias = Callable[[types.InputResponses | None, str | None], Awaitable[types.Result]]
"""One request leg: ``(input_responses, request_state)`` → the Server's result."""
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class LoopState:
    """Mutable loop facts that outlive an interrupted loop (audit ``input_rounds``)."""

    rounds: int = 0


async def drive(
    leg: Leg,
    *,
    router: InputRequestRouter,
    server_id: str,
    consumer: Consumer | None = None,
    deadline: PausableDeadline | None = None,
    state: LoopState | None = None,
    invocation_id: str | None = None,
    max_rounds: int = MAX_INPUT_ROUNDS,
    sleep: Sleep = anyio.sleep,
) -> types.Result:
    """Run ``leg`` until the Server returns a terminal result (REQ-HSH-040…044)."""
    state = state if state is not None else LoopState()
    delay = STATE_ONLY_BACKOFF_INITIAL_S
    with consumer_scope(consumer):
        result = await leg(None, None)
        while isinstance(result, types.InputRequiredResult):
            if state.rounds >= max_rounds:
                raise InputRoundsExceededError(state.rounds)
            responses: types.InputResponses | None
            if result.input_requests:
                delay = STATE_ONLY_BACKOFF_INITIAL_S
                responses = await router.fulfil(
                    result.input_requests,
                    server_id=server_id,
                    consumer=consumer,
                    deadline=deadline,
                    invocation_id=invocation_id,
                )
            else:
                await sleep(delay)
                delay = min(delay * 2, STATE_ONLY_BACKOFF_CAP_S)
                responses = None
            state.rounds += 1
            result = await leg(responses, result.request_state)
    return result


ProgressFn = Callable[[float, float | None, str | None], Awaitable[None]]


async def _relist(client: Any) -> None:
    cursor: str | None = None
    for _ in range(RELIST_PAGE_CAP):
        page = await client.list_tools(cursor=cursor, cache_mode="refresh")
        cursor = page.next_cursor
        if cursor is None:
            return


def call_tool_leg(
    client: Any,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    progress_callback: ProgressFn | None,
    modern: bool,
) -> Leg:
    """The ``tools/call`` leg of Flow C step 6 on ``client.session``.

    ``progress_callback`` is passed by keyword on every leg (each leg is a new request with
    its own progress token, spike S-1). On a modern connection a ``HEADER_MISMATCH``
    (``-32020``) re-lists the tools once (refreshing the ``Mcp-Param-*`` header maps) and
    resends once; a second mismatch, or a failing re-list, raises the mismatch.
    """

    async def send(responses: types.InputResponses | None, state: str | None) -> types.Result:
        result: types.Result = await client.session.call_tool(
            name,
            arguments,
            progress_callback=progress_callback,
            input_responses=responses,
            request_state=state,
            allow_input_required=True,
        )
        return result

    async def leg(responses: types.InputResponses | None, state: str | None) -> types.Result:
        try:
            return await send(responses, state)
        except MCPError as mismatch:
            if mismatch.code != types.HEADER_MISMATCH or not modern:
                raise
            try:
                await _relist(client)
            except (MCPError, TimeoutError, ValidationError) as relist_error:
                raise mismatch from relist_error
            return await send(responses, state)

    return leg


# ---------------------------------------------------------------------- catalog reads


async def _drive_read(
    leg: Leg, *, router: InputRequestRouter, server_id: str, consumer: Consumer | None
) -> types.Result:
    """:func:`drive` for reads: loop failures raise ``InputRequestError`` (REQ-HSH-046)."""
    code: InputRequestCode
    try:
        return await drive(leg, router=router, server_id=server_id, consumer=consumer)
    except UndeclaredInputRequestError:
        code = "undeclared_input_request"
    except InputRoundsExceededError:
        code = "input_rounds_exceeded"
    except SamplingDeniedError:
        code = "sampling_denied"
    raise InputRequestError(server_id, code) from None


async def read_resource(
    client: Any,
    uri: str,
    *,
    router: InputRequestRouter,
    server_id: str,
    consumer: Consumer | None,
) -> types.ReadResourceResult:
    """``resources/read`` through the MRTR loop (REQ-CAT-005, REQ-HSH-040…046)."""

    async def leg(responses: types.InputResponses | None, state: str | None) -> types.Result:
        result: types.Result = await client.session.read_resource(
            uri, input_responses=responses, request_state=state, allow_input_required=True
        )
        return result

    result = await _drive_read(leg, router=router, server_id=server_id, consumer=consumer)
    if not isinstance(result, types.ReadResourceResult):  # pragma: no cover - SDK-typed
        raise TypeError(f"unexpected resources/read result {type(result).__name__}")
    return result


async def get_prompt(
    client: Any,
    name: str,
    arguments: dict[str, str] | None,
    *,
    router: InputRequestRouter,
    server_id: str,
    consumer: Consumer | None,
) -> types.GetPromptResult:
    """``prompts/get`` through the MRTR loop (REQ-CAT-007, REQ-HSH-040…046)."""

    async def leg(responses: types.InputResponses | None, state: str | None) -> types.Result:
        result: types.Result = await client.session.get_prompt(
            name,
            arguments,
            input_responses=responses,
            request_state=state,
            allow_input_required=True,
        )
        return result

    result = await _drive_read(leg, router=router, server_id=server_id, consumer=consumer)
    if not isinstance(result, types.GetPromptResult):  # pragma: no cover - SDK-typed
        raise TypeError(f"unexpected prompts/get result {type(result).__name__}")
    return result
