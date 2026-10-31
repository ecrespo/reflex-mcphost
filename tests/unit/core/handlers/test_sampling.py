"""``SamplingHandler`` with a real consent store and audit log and fake provider/prompter
(F4-02; API §4.14). *Sampling is deprecated in the pinned spec; compatibility feature.*"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.audit import AuditLog, AuditQuery, AuditRecord, AuditUnavailableError
from reflex_mcphost.core.consent.models import ConsentAnswer, ConsentDecision, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.handlers.base import Refusal
from reflex_mcphost.core.handlers.sampling import SamplingHandler, SamplingSettings
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.mrtr import (
    SAMPLING,
    InputContext,
    InputHandlerError,
    PausableDeadline,
    SamplingDeniedError,
)
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.prompter import SamplingResponseReview, SamplingVerdict
from reflex_mcphost.core.types import Consumer
from tests.unit.core.handlers.fakes import BOT, ScriptedPrompter, ctx, legacy_context

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
OTHER = Consumer.agent("other")


def params(text: str = "Greet Ada") -> types.CreateMessageRequestParams:
    return types.CreateMessageRequestParams(
        messages=[
            types.SamplingMessage(role="user", content=types.TextContent(type="text", text=text))
        ],
        max_tokens=16,
        system_prompt="be brief",
    )


def completion(text: str = "Hi Ada!") -> types.CreateMessageResult:
    return types.CreateMessageResult(
        role="assistant", content=types.TextContent(type="text", text=text), model="fake"
    )


@dataclass
class FakeProvider:
    result: types.CreateMessageResult | BaseException | str = field(default_factory=completion)
    calls: list[tuple[types.CreateMessageRequestParams, str]] = field(default_factory=list)

    async def create_message(
        self, params: types.CreateMessageRequestParams, *, server_id: str
    ) -> types.CreateMessageResult:
        self.calls.append((params, server_id))
        if self.result == "hang":
            await anyio.sleep_forever()
        if isinstance(self.result, BaseException):
            raise self.result
        assert isinstance(self.result, types.CreateMessageResult)
        return self.result


class FailingAudit(AuditLog):
    async def begin(self, **kwargs: Any) -> str:
        raise AuditUnavailableError("x", "db down")


@dataclass
class Env:
    db: Database
    audit: AuditLog
    policy: ConsentPolicy
    prompter: ScriptedPrompter | None
    provider: FakeProvider | None
    review: bool = False
    timeout_s: float = 5.0

    def handler(self, audit: AuditLog | None = None) -> SamplingHandler:
        return SamplingHandler(
            consent=self.policy,
            audit=audit or self.audit,
            provider_of=lambda: self.provider,
            prompter_of=lambda: self.prompter,
            settings_of=lambda server_id: SamplingSettings(
                timeout_s=self.timeout_s, review=self.review, review_timeout_s=5.0
            ),
        )

    async def records(self) -> list[AuditRecord]:
        page = await self.audit.query(AuditQuery(operation=OperationKind.SAMPLING))
        return list(page.records)

    async def allow(self, consumer: Consumer | None = None) -> None:
        await self.policy.grant(
            effect="allow",
            operation=OperationKind.SAMPLING,
            target=ConsentTarget.server("srv"),
            consumer=consumer,
        )


@pytest.fixture
async def env(tmp_path: Path) -> Iterator[Env]:  # type: ignore[misc]
    db = open_database(tmp_path / "data")
    holder: dict[str, Any] = {}
    policy = ConsentPolicy(events=EventBus(), prompter=lambda: holder["env"].prompter)
    await policy.attach(ConsentStore(db), timeout_s=5.0)
    e = Env(db, AuditLog(db, InstallKey(b"k" * 32)), policy, ScriptedPrompter(), FakeProvider())
    holder["env"] = e
    yield e
    db.close()


async def test_consent_is_evaluated_before_the_provider(env: Env) -> None:
    """REQ-HSH-001, REQ-HSH-002, REQ-HSH-003: the Consumer is asked (messages, system
    prompt, max tokens via SamplingReview) before any provider call; approval routes the
    request to the provider and returns its completion; one audit record."""
    assert env.prompter is not None
    assert env.provider is not None
    order: list[str] = []
    real_ask = env.prompter.ask_consent

    async def ask(request: Any) -> ConsentAnswer:
        order.append("consent")
        assert env.provider is not None
        assert env.provider.calls == []
        return await real_ask(request)

    env.prompter.ask_consent = ask  # type: ignore[method-assign]
    result = await env.handler().handle(ctx(SAMPLING), params())
    assert result == completion()
    (request,) = env.prompter.asked
    assert request.operation is OperationKind.SAMPLING
    assert request.consumer == BOT
    assert request.sampling is not None
    assert request.sampling.params == params()
    assert request.sampling.server_id == "srv"
    assert order == ["consent"]
    assert env.provider.calls == [(params(), "srv")]
    (record,) = await env.records()
    assert (record.outcome, record.fqn, record.server_id) == (Out.success, None, "srv")
    assert (record.consumer, record.decision_source) == (BOT, "prompt")


async def test_edited_params_reach_the_provider(env: Env) -> None:
    """REQ-HSH-002: the Consumer may edit the request; the edited params are sent."""
    assert env.prompter is not None
    assert env.provider is not None
    edited = params("Greet Grace")

    async def ask(request: Any) -> ConsentAnswer:
        return ConsentAnswer(decision=ConsentDecision.ALLOW_ONCE, edited_sampling=edited)

    env.prompter.ask_consent = ask  # type: ignore[method-assign]
    await env.handler().handle(ctx(SAMPLING), params())
    assert env.provider.calls == [(edited, "srv")]


async def test_denied_is_audited_and_refused(env: Env) -> None:
    """REQ-HSH-005: a denied request is not sent to the provider, is recorded as
    denied/sampling_denied, and refused (modern SamplingDeniedError; legacy ErrorData(-1))."""
    assert env.prompter is not None
    assert env.provider is not None
    env.prompter.decision = ConsentDecision.DENY
    h = env.handler()
    result = await h.handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert result.denied
    with pytest.raises(SamplingDeniedError):
        await h.as_input_handler()(ctx(SAMPLING), types.CreateMessageRequest(params=params()))
    flagged: list[bool] = []
    legacy_ctx = InputContext(
        server_id="srv",
        consumer=BOT,
        key="3",
        method=SAMPLING,
        on_refused=lambda: flagged.append(True),
    )
    legacy = await h.as_callback(lambda m, k: legacy_ctx)(legacy_context(3), params())
    assert legacy == types.ErrorData(code=-1, message="Sampling request rejected by host")
    assert flagged == [True]
    assert env.provider.calls == []
    records = await env.records()
    assert [(r.outcome, r.error_code) for r in records] == [(Out.denied, "sampling_denied")] * 3


async def test_no_provider_is_denied_without_prompting(env: Env) -> None:
    """REQ-HSH-005, REQ-HSH-006: no SamplingProvider → refused and audited
    denied/sampling_denied; nobody is asked."""
    assert env.prompter is not None
    env.provider = None
    result = await env.handler().handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert result.denied
    assert env.prompter.asked == []
    (record,) = await env.records()
    assert (record.outcome, record.error_code, record.decision_source) == (
        Out.denied,
        "sampling_denied",
        "not_evaluated",
    )


async def test_review_reject_is_sampling_denied(env: Env) -> None:
    """REQ-HSH-004: with review on, a rejected completion is not returned
    (denied/sampling_denied)."""
    assert env.prompter is not None
    env.review = True
    env.prompter.verdict = SamplingVerdict(approved=False)
    result = await env.handler().handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert result.denied
    assert len(env.prompter.reviewed) == 1
    (record,) = await env.records()
    assert (record.outcome, record.error_code) == (Out.denied, "sampling_denied")


@pytest.mark.parametrize("verdict", [RuntimeError("ui"), "hang", None])
async def test_review_failure_is_denied(env: Env, verdict: Any) -> None:
    """REQ-HSH-004 (API §4.10, delta D8): a review that raises, times out or has no prompter
    is rejected."""
    assert env.prompter is not None
    env.review = True
    await env.allow()
    handler = env.handler()
    if verdict is None:
        env.prompter = None
    else:
        env.prompter.verdict = verdict
    handler._settings_of = lambda s: SamplingSettings(  # type: ignore[method-assign]
        timeout_s=5.0, review=True, review_timeout_s=0.05
    )
    with anyio.fail_after(3):
        result = await handler.handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert result.denied


async def test_review_edit_replaces_the_result(env: Env) -> None:
    """REQ-HSH-004: an edited result replaces the provider's completion; the review shows
    the provider's result for this Server."""
    assert env.prompter is not None
    env.review = True
    env.prompter.verdict = SamplingVerdict(approved=True, edited_result=completion("edited"))
    result = await env.handler().handle(ctx(SAMPLING), params())
    assert result == completion("edited")
    (review,) = env.prompter.reviewed
    assert isinstance(review, SamplingResponseReview)
    assert (review.server_id, review.result) == ("srv", completion())


async def test_provider_raising_is_one_protocol_error_record(env: Env) -> None:
    """REQ-NFR-020 (provider part), API §4.14: a provider exception never escapes; one
    audit record protocol_error/provider_error and a refusal with reason provider_error."""
    assert env.provider is not None
    env.provider.result = RuntimeError("model down sekret-abc")
    h = env.handler()
    result = await h.handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert not result.denied
    assert result.reason == "provider_error"
    with pytest.raises(InputHandlerError) as info:
        await h.as_input_handler()(ctx(SAMPLING), types.CreateMessageRequest(params=params()))
    assert info.value.reason == "provider_error"
    records = await env.records()
    assert [(r.outcome, r.error_code) for r in records] == [
        (Out.protocol_error, "provider_error")
    ] * 2


async def test_provider_hanging_is_one_timeout_record(env: Env) -> None:
    """REQ-NFR-020, API §4.14: the provider runs under the Server's timeout; a timeout is
    audited timeout/provider_timeout and refused with reason provider_timeout."""
    assert env.provider is not None
    env.provider.result = "hang"
    env.timeout_s = 0.05
    with anyio.fail_after(3):
        result = await env.handler().handle(ctx(SAMPLING), params())
    assert isinstance(result, Refusal)
    assert result.reason == "provider_timeout"
    (record,) = await env.records()
    assert (record.outcome, record.error_code) == (Out.timeout, "provider_timeout")


async def test_audit_begin_failure_never_calls_the_provider(env: Env) -> None:
    """Art. 3, API §4.14: if the audit record cannot be begun the provider is never called
    and the sampling is refused (reason audit_unavailable)."""
    assert env.provider is not None
    result = await env.handler(FailingAudit(env.db, InstallKey(b"k" * 32))).handle(
        ctx(SAMPLING), params()
    )
    assert isinstance(result, Refusal)
    assert result.reason == "audit_unavailable"
    assert env.provider.calls == []


async def test_stored_rule_is_scoped_to_its_consumer(env: Env) -> None:
    """REQ-HSH-001, REQ-CNS-015: an `always` sampling rule for agent A does not apply to
    agent B (B is asked)."""
    assert env.prompter is not None
    await env.allow(BOT)
    env.prompter.decision = ConsentDecision.DENY
    assert await env.handler().handle(ctx(SAMPLING), params()) == completion()
    assert env.prompter.asked == []
    denied = await env.handler().handle(ctx(SAMPLING, consumer=OTHER), params())
    assert isinstance(denied, Refusal)
    assert denied.denied
    assert [r.consumer for r in env.prompter.asked] == [OTHER]


async def test_unattributed_ignores_stored_allow(env: Env) -> None:
    """REQ-HSH-047: an unattributed request asks the human Consumer once-only (stored allow
    rules do not apply) and is audited unattributed."""
    assert env.prompter is not None
    await env.allow()
    result = await env.handler().handle(
        ctx(SAMPLING, consumer=None, attribution="unattributed"), params()
    )
    assert result == completion()
    (request,) = env.prompter.asked
    assert (request.consumer, request.attribution) == (Consumer.human(), "unattributed")
    (record,) = await env.records()
    assert record.attribution == "unattributed"


async def test_unattributed_without_prompter_is_denied(env: Env) -> None:
    """REQ-HSH-047: with no prompter an unattributed request is denied."""
    await env.allow()
    env.prompter = None
    result = await env.handler().handle(
        ctx(SAMPLING, consumer=None, attribution="unattributed"), params()
    )
    assert isinstance(result, Refusal)
    assert result.denied


async def test_consent_wait_pauses_the_deadline(env: Env) -> None:
    """DD-004: the consent prompt for a sampling request is not counted against the
    invocation's budget."""
    assert env.prompter is not None
    real_ask = env.prompter.ask_consent

    async def slow(request: Any) -> ConsentAnswer:
        await anyio.sleep(0.3)
        return await real_ask(request)

    env.prompter.ask_consent = slow  # type: ignore[method-assign]
    deadline = PausableDeadline(0.2)
    with deadline.running():
        result = await env.handler().handle(ctx(SAMPLING, deadline=deadline), params())
    assert result == completion()
    assert not deadline.expired
