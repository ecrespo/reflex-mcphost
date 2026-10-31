"""Fakes shared by the handler unit tests (F4-01…F4-04): a scriptable prompter and a
legacy ``ClientRequestContext``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
from mcp.client import ClientRequestContext

from reflex_mcphost.core.consent.models import ConsentAnswer, ConsentDecision, ConsentRequest
from reflex_mcphost.core.mrtr import InputContext, PausableDeadline
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)
from reflex_mcphost.core.types import Consumer

BOT = Consumer.agent("bot")
Script = ElicitationResponse | BaseException | Callable[[ElicitationRequest], Any]


@dataclass
class ScriptedPrompter:
    """Answers ``elicit`` from ``script`` in order (the last item repeats); an exception
    item is raised, ``"hang"`` sleeps forever. Records every request it is shown."""

    script: Sequence[Script | str] = ()
    verdict: SamplingVerdict | BaseException | str = field(
        default_factory=lambda: SamplingVerdict(approved=True)
    )
    decision: ConsentDecision = ConsentDecision.ALLOW_ONCE
    supports_elicitation: bool = True
    elicited: list[ElicitationRequest] = field(default_factory=list)
    reviewed: list[SamplingResponseReview] = field(default_factory=list)
    asked: list[ConsentRequest] = field(default_factory=list)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        self.elicited.append(request)
        item = self.script[min(len(self.elicited), len(self.script)) - 1]
        if item == "hang":
            await anyio.sleep_forever()
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return cast(ElicitationResponse, await item(request))
        return cast(ElicitationResponse, item)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        self.reviewed.append(review)
        if self.verdict == "hang":
            await anyio.sleep_forever()
        if isinstance(self.verdict, BaseException):
            raise self.verdict
        return cast(SamplingVerdict, self.verdict)

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        return ConsentAnswer(decision=self.decision)

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False


def ctx(
    method: str,
    *,
    consumer: Consumer | None = BOT,
    deadline: PausableDeadline | None = None,
    attribution: Any = "exact",
    invocation_id: str | None = "inv_1",
) -> InputContext:
    return InputContext(
        server_id="srv",
        consumer=consumer,
        key="k1",
        method=method,
        deadline=deadline,
        invocation_id=invocation_id,
        attribution=attribution,
    )


def legacy_context(request_id: int = 7) -> ClientRequestContext:
    """A legacy SDK callback context; the handlers never touch its session."""
    return ClientRequestContext(session=cast(Any, None), request_id=request_id)
