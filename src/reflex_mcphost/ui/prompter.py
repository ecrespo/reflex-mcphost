"""``ReflexPrompter`` and the process-wide ``PromptRegistry`` (API §4.10, §8; TD Flow D).

Every human decision reaches core only as a ``Prompter`` return value. Each
``ReflexPrompter`` method registers a ``PendingPrompt`` in the ``PromptRegistry`` and awaits
its answer; the entry is removed (and ``removed`` published) when it is answered, when the
core-side wait ends (``consent_timeout_s``) or when the awaiting coroutine is cancelled
(Server disconnected, Host closing). ``confirm_trust`` has no timeout of its own (delta D5).

Each tab's watcher subscribes to the registry's change stream (one bounded stream per
subscriber; on overflow the oldest changes are dropped and one ``dropped`` marker is
delivered first) and renders cards from ``pending()``. The first tab to answer wins
(``resolve`` returns ``False`` afterwards).

The registry is UI-layer state: payloads (core request objects, argument previews already
redacted by core) live in memory only and are never persisted or logged (Art. 5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, TypeVar

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    DenyLifetime,
    TargetKind,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)

__all__ = [
    "PROMPTER",
    "PROMPTS",
    "PendingPrompt",
    "PromptAnswerError",
    "PromptChange",
    "PromptKind",
    "PromptRegistry",
    "ReflexPrompter",
    "consent_answer",
    "consent_options",
]

PromptKind = Literal["consent", "sampling_review", "elicitation_form", "elicitation_url", "trust"]
ChangeType = Literal["added", "updated", "removed", "dropped"]
_T = TypeVar("_T")


class PromptAnswerError(ValueError):
    """An answer that is not one of the prompt's options (never sent to core)."""


@dataclass(frozen=True)
class PendingPrompt:
    """One prompt waiting for a human answer (Flow D step 3)."""

    kind: PromptKind
    request_id: str
    payload: object
    created_at: datetime


@dataclass(frozen=True)
class PromptChange:
    """An item of the registry's change stream; ``count`` is set on ``dropped``."""

    type: ChangeType
    request_id: str | None = None
    count: int = 0


@dataclass
class _Entry:
    prompt: PendingPrompt
    answered: anyio.Event = field(default_factory=anyio.Event)
    answer: object = None
    waiters: int = 0


class _Subscription:
    def __init__(self, buffer: int) -> None:
        self.send: MemoryObjectSendStream[PromptChange]
        self.receive: MemoryObjectReceiveStream[PromptChange]
        self.send, self.receive = anyio.create_memory_object_stream[PromptChange](buffer)
        self.dropped = 0

    def offer(self, change: PromptChange) -> None:
        try:
            self.send.send_nowait(change)
        except anyio.WouldBlock:
            self.receive.receive_nowait()  # drop the oldest buffered change
            self.dropped += 1
            self.send.send_nowait(change)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            pass

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> PromptChange:
        if self.dropped:
            count, self.dropped = self.dropped, 0
            return PromptChange("dropped", count=count)
        try:
            return await self.receive.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    def close(self) -> None:
        self.send.close()
        self.receive.close()


class PromptRegistry:
    """Pending prompts of this backend process, keyed by request id."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._subscriptions: list[_Subscription] = []

    def pending(self) -> list[PendingPrompt]:
        """Snapshot of every pending prompt, oldest first."""
        return sorted((e.prompt for e in self._entries.values()), key=lambda p: p.created_at)

    def get(self, request_id: str) -> PendingPrompt | None:
        entry = self._entries.get(request_id)
        return entry.prompt if entry is not None else None

    def _publish(self, change: PromptChange) -> None:
        for sub in tuple(self._subscriptions):
            sub.offer(change)

    @asynccontextmanager
    async def subscribe(self, *, buffer: int = 64) -> AsyncIterator[AsyncIterator[PromptChange]]:
        """A bounded stream of ``added`` / ``updated`` / ``removed`` / ``dropped``."""
        if buffer < 1:
            raise ValueError("buffer must be >= 1")
        sub = _Subscription(buffer)
        self._subscriptions.append(sub)
        try:
            yield sub
        finally:
            self._subscriptions.remove(sub)
            sub.close()

    def resolve(self, request_id: str, answer: object) -> bool:
        """Answer a pending prompt; ``False`` if it is gone or already answered."""
        entry = self._entries.get(request_id)
        if entry is None or entry.answered.is_set():
            return False
        entry.answer = answer
        entry.answered.set()
        return True

    async def ask(self, kind: PromptKind, request_id: str, payload: object) -> object:
        """Register a prompt (or update the pending one with this id) and await its answer."""
        prompt = PendingPrompt(kind, request_id, payload, datetime.now(UTC))
        entry = self._entries.get(request_id)
        if entry is None or entry.answered.is_set():
            entry = _Entry(prompt)
            self._entries[request_id] = entry
            self._publish(PromptChange("added", request_id))
        else:
            entry.prompt = PendingPrompt(kind, request_id, payload, entry.prompt.created_at)
            self._publish(PromptChange("updated", request_id))
        entry.waiters += 1
        try:
            await entry.answered.wait()
            return entry.answer
        finally:
            entry.waiters -= 1
            if entry.waiters == 0 and self._entries.get(request_id) is entry:
                del self._entries[request_id]
                self._publish(PromptChange("removed", request_id))


# ------------------------------------------------------------------ consent card answers

_ALL_ANSWERS = ("allow_once", "allow_session", "allow_always", "deny")
_ALL_LIFETIMES: tuple[DenyLifetime, ...] = ("once", "session", "always")


def consent_options(
    request: ConsentRequest,
) -> tuple[tuple[str, ...], tuple[TargetKind, ...], tuple[DenyLifetime, ...]]:
    """``(answers, targets, deny lifetimes)`` a consent card offers (REQ-HSH-047)."""
    tool_targets: tuple[TargetKind, ...] = (
        ("server", "global") if request.operation is OperationKind.SAMPLING else ("tool",)
    )
    if request.attribution == "unattributed":
        return ("allow_once", "deny"), tool_targets[:1], ("once",)
    targets: tuple[TargetKind, ...] = (
        ("server", "global")
        if request.operation is OperationKind.SAMPLING
        else ("tool", "server", "global")
    )
    return _ALL_ANSWERS, targets, _ALL_LIFETIMES


def consent_answer(
    request: ConsentRequest, choice: str, target: str, deny_lifetime: str
) -> ConsentAnswer:
    """Map a card answer to the exact ``ConsentAnswer``; ``PromptAnswerError`` for any
    option the card does not offer (checked before core sees it)."""
    answers, targets, lifetimes = consent_options(request)
    if choice not in answers:
        raise PromptAnswerError(f"answer {choice!r} is not offered for this request")
    if target not in targets:
        raise PromptAnswerError(f"target {target!r} is not offered for this request")
    wanted = deny_lifetime if choice == "deny" else "once"
    lifetime = next((x for x in lifetimes if x == wanted), None)
    if lifetime is None:
        raise PromptAnswerError(f"deny lifetime {deny_lifetime!r} is not offered")
    target_kind = next(t for t in targets if t == target)
    return ConsentAnswer(
        decision=ConsentDecision(choice), target_kind=target_kind, deny_lifetime=lifetime
    )


# ------------------------------------------------------------------ the prompter


class ReflexPrompter:
    """``Prompter`` of the Reflex UI, backed by a ``PromptRegistry`` (API §8)."""

    supports_elicitation = True

    def __init__(self, registry: PromptRegistry | None = None) -> None:
        self._registry = registry

    @property
    def registry(self) -> PromptRegistry:
        """The registry prompts go to (default: the process-wide ``PROMPTS``)."""
        return self._registry if self._registry is not None else PROMPTS

    async def _ask(
        self, kind: PromptKind, request_id: str, payload: object, expected: type[_T]
    ) -> _T:
        answer = await self.registry.ask(kind, request_id, payload)
        if not isinstance(answer, expected):  # fail closed: core denies on an exception
            raise PromptAnswerError(f"a {kind} prompt was answered with {type(answer).__name__}")
        return answer

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        return await self._ask("consent", request.id, request, ConsentAnswer)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        return await self._ask("sampling_review", review.request_id, review, SamplingVerdict)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        kind: PromptKind = "elicitation_url" if request.mode == "url" else "elicitation_form"
        return await self._ask(kind, request.id, request, ElicitationResponse)

    async def confirm_trust(self, request: TrustRequest) -> bool:
        """``True`` only for an explicit Trust; not bounded by ``consent_timeout_s`` (D5)."""
        return await self.registry.ask("trust", request.id, request) is True


PROMPTS = PromptRegistry()
"""The pending prompts of this backend process."""
PROMPTER = ReflexPrompter()
"""The process-wide prompter ``install()`` binds to the Host (uses ``PROMPTS``)."""
