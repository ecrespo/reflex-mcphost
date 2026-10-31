"""Sampling handler and the ``SamplingProvider`` protocol (REQ-HSH-001…006, API §4.14).

*Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*

Flow, identical for MRTR input requests and legacy ``sampling/createMessage`` (REQ-HSH-045):

1. no provider → audited ``denied``/``sampling_denied`` (``decision_source="not_evaluated"``)
   and refused — nobody is asked;
2. ``ConsentPolicy.evaluate`` (``operation=sampling``, Server target, ``SamplingReview``)
   for the originating Consumer; an unattributed legacy request asks the human Consumer
   with ``attribution="unattributed"`` (REQ-HSH-047). The prompt runs inside
   ``ctx.prompter_wait()`` (DD-004). Denied → audited ``denied``/``sampling_denied``;
3. ``AuditLog.begin`` — failure → refused (reason ``audit_unavailable``), the provider is
   never called (Art. 3);
4. ``provider.create_message(edited_sampling or params, server_id=…)`` under the Server's
   timeout: an exception → finished ``protocol_error``/``provider_error``, a timeout →
   ``timeout``/``provider_timeout``; both refused (REQ-NFR-020: never crashes the Host);
5. if the Server's ``review_sampling_responses``: ``review_sampling_response`` inside
   ``ctx.prompter_wait()`` bounded by ``consent_timeout_s``; rejected, failing, timed out or
   no prompter → finished ``denied``/``sampling_denied``; ``edited_result`` replaces the
   result (REQ-HSH-004);
6. finished ``success``.

Exactly one audit record per attempt (``operation=sampling``, ``fqn=None``); no messages
are stored (Art. 5). Refusal channels: modern → ``SamplingDeniedError`` (denied) or
``InputHandlerError`` (other reasons); legacy → ``ErrorData(-1, "Sampling request rejected
by host")`` for a denial (the SDK's user-rejection code, spike S-1) and
``ErrorData(-32603, "Sampling request failed")`` otherwise. Only this module calls
``create_message`` (``tests/architecture/test_single_effect_path.py``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import anyio
import mcp_types as types
from mcp.client import ClientRequestContext
from mcp.client.session import SamplingFnT

from reflex_mcphost.core.audit import AuditLog, AuditUnavailableError
from reflex_mcphost.core.consent.models import ConsentRequest
from reflex_mcphost.core.consent.outcome import ConsentOutcome, OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.handlers.base import Attribute, Refusal
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.mrtr import (
    SAMPLING,
    Attribution,
    InputContext,
    InputHandler,
    InputHandlerError,
    SamplingDeniedError,
)
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.prompter import (
    Prompter,
    SamplingResponseReview,
    SamplingReview,
    SamplingVerdict,
)
from reflex_mcphost.core.types import Consumer, new_invocation_id

__all__ = [
    "SAMPLING_DENIED",
    "SamplingHandler",
    "SamplingProvider",
    "SamplingSettings",
]

SAMPLING_DENIED = "sampling_denied"
_O = InvocationOutcome
_DENIED = Refusal(
    SAMPLING_DENIED,
    types.ErrorData(code=-1, message="Sampling request rejected by host"),
    denied=True,
)
_log = get_logger(__name__)


def _failed(reason: str) -> Refusal:
    return Refusal(
        reason, types.ErrorData(code=types.INTERNAL_ERROR, message="Sampling request failed")
    )


@runtime_checkable
class SamplingProvider(Protocol):
    """Produces completions for Servers' sampling requests (API §4.14, REQ-HSH-003).

    *Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*
    """

    async def create_message(
        self, params: types.CreateMessageRequestParams, *, server_id: str
    ) -> types.CreateMessageResult: ...


@dataclass(frozen=True)
class SamplingSettings:
    """Per-Server sampling settings: provider timeout (the Server's ``timeout_s`` or the
    default tool timeout), response review (REQ-HSH-004) and its prompt bound."""

    timeout_s: float
    review: bool = False
    review_timeout_s: float = 120.0


class SamplingHandler:
    """One sampling core for both eras (REQ-HSH-045)."""

    def __init__(
        self,
        *,
        consent: ConsentPolicy,
        audit: AuditLog,
        provider_of: Callable[[], SamplingProvider | None],
        prompter_of: Callable[[], Prompter | None],
        settings_of: Callable[[str], SamplingSettings],
        server_name_of: Callable[[str], str] = str,
    ) -> None:
        self._consent = consent
        self._audit = audit
        self._provider_of = provider_of
        self._prompter_of = prompter_of
        self._settings_of = settings_of
        self._server_name_of = server_name_of

    # ------------------------------------------------------------------ core

    async def handle(
        self, ctx: InputContext, params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult | Refusal:
        """Run the flow; a refusal is returned, never raised (cancellation aside)."""
        log = ContextAdapter(_log, server_id=ctx.server_id, invocation_id=ctx.invocation_id)
        attempt = _Attempt(self._audit, ctx)
        provider = self._provider_of()
        if provider is None:
            log.info("sampling refused: no sampling provider configured")
            return await attempt.refuse(_DENIED, "not_evaluated", None)
        settings = self._settings_of(ctx.server_id)
        consent = await self._evaluate(ctx, attempt, params, log)
        if not consent.allowed:
            return await attempt.refuse(_DENIED, consent.source, consent.rule_id)
        try:
            await attempt.begin(consent)
        except AuditUnavailableError:
            log.error("sampling refused: the audit record could not be begun")
            return _failed("audit_unavailable")
        try:
            result = await self._complete(
                provider, consent.edited_sampling or params, ctx, settings
            )
        except TimeoutError:
            log.warning("sampling provider timed out after %g s", settings.timeout_s)
            return await attempt.end(_O.timeout, _failed("provider_timeout"))
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                await attempt.finish(_O.cancelled, "cancelled")
            raise
        except Exception as exc:
            log.warning("sampling provider failed (%s)", type(exc).__name__)
            return await attempt.end(_O.protocol_error, _failed("provider_error"))
        if settings.review:
            verdict = await self._review(ctx, attempt.invocation_id, result, settings, log)
            if verdict is None or not verdict.approved:
                return await attempt.end(_O.denied, _DENIED)
            result = verdict.edited_result or result
        await attempt.finish(_O.success, None)
        return result

    async def _evaluate(
        self,
        ctx: InputContext,
        attempt: _Attempt,
        params: types.CreateMessageRequestParams,
        log: ContextAdapter,
    ) -> ConsentOutcome:
        try:
            server_name = self._server_name_of(ctx.server_id)
        except Exception:
            server_name = ctx.server_id
        try:
            request = ConsentRequest(
                operation=OperationKind.SAMPLING,
                consumer=attempt.consumer,
                server_id=ctx.server_id,
                server_name=server_name,
                sampling=SamplingReview(server_id=ctx.server_id, params=params),
                attribution=attempt.attribution,
            )
        except Exception as exc:
            log.error("sampling consent request could not be built (%s)", type(exc).__name__)
            return ConsentOutcome(allowed=False, source="error", reason="invalid request")
        with ctx.prompter_wait():
            return await self._consent.evaluate(request)

    @staticmethod
    async def _complete(
        provider: SamplingProvider,
        params: types.CreateMessageRequestParams,
        ctx: InputContext,
        settings: SamplingSettings,
    ) -> types.CreateMessageResult:
        with anyio.fail_after(settings.timeout_s):
            result: object = await provider.create_message(params, server_id=ctx.server_id)
        if not isinstance(result, types.CreateMessageResult):
            raise TypeError(f"provider returned {type(result).__name__}")
        return result

    async def _review(
        self,
        ctx: InputContext,
        request_id: str,
        result: types.CreateMessageResult,
        settings: SamplingSettings,
        log: ContextAdapter,
    ) -> SamplingVerdict | None:
        prompter = self._prompter_of()
        if prompter is None:
            log.info("sampling response rejected: no prompter to review it")
            return None
        review = SamplingResponseReview(
            request_id=request_id, server_id=ctx.server_id, result=result
        )
        try:
            with ctx.prompter_wait(), anyio.fail_after(settings.review_timeout_s):
                verdict: object = await prompter.review_sampling_response(review)
        except TimeoutError:
            log.warning(
                "sampling review not answered within %g s; rejected", settings.review_timeout_s
            )
            return None
        except Exception as exc:
            log.warning("sampling review failed (%s); rejected", type(exc).__name__)
            return None
        if not isinstance(verdict, SamplingVerdict):
            log.warning("sampling review returned %s; rejected", type(verdict).__name__)
            return None
        return verdict

    # ------------------------------------------------------------------ adapters

    def as_input_handler(self) -> InputHandler:
        """The modern router slot ``sampling/createMessage``: a denial raises
        ``SamplingDeniedError`` (client-side abort), other refusals ``InputHandlerError``."""

        async def slot(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
            if not isinstance(request, types.CreateMessageRequest):  # pragma: no cover
                raise InputHandlerError(SAMPLING, "unexpected_request")
            result = await self.handle(ctx, request.params)
            if isinstance(result, Refusal):
                if result.denied:
                    raise SamplingDeniedError(SAMPLING_DENIED)
                raise InputHandlerError(SAMPLING, result.reason)
            return result

        return slot

    def as_callback(self, attribute: Attribute) -> SamplingFnT:
        """The legacy SDK ``sampling_callback``: a refusal returns ``ErrorData``; a denial of
        an attributed request also flags it (``ctx.on_refused``)."""

        async def callback(
            context: ClientRequestContext, params: types.CreateMessageRequestParams
        ) -> types.CreateMessageResult | types.ErrorData:
            ctx = attribute(SAMPLING, str(context.request_id))
            result = await self.handle(ctx, params)
            if isinstance(result, Refusal):
                if result.denied and ctx.on_refused is not None:
                    ctx.on_refused()
                return result.legacy
            return result

        return callback


class _Attempt:
    """The single audit record of one sampling attempt."""

    def __init__(self, audit: AuditLog, ctx: InputContext) -> None:
        self._audit = audit
        self._t0 = time.perf_counter()
        self.server_id = ctx.server_id
        self.invocation_id = new_invocation_id()
        consumer = ctx.consumer if ctx.attribution == "exact" else None
        self.consumer: Consumer = consumer if consumer is not None else Consumer.human()
        self.attribution: Attribution = "exact" if consumer is not None else "unattributed"

    def _ms(self) -> int:
        return max(0, int((time.perf_counter() - self._t0) * 1000))

    async def refuse(self, refusal: Refusal, source: str, rule_id: str | None) -> Refusal:
        """A single finished record for an attempt that never began (denied)."""
        try:
            with anyio.CancelScope(shield=True):
                await self._audit.record(
                    consumer=self.consumer,
                    outcome=_O.denied,
                    duration_ms=self._ms(),
                    decision_source=source,
                    server_id=self.server_id,
                    operation=OperationKind.SAMPLING,
                    rule_id=rule_id,
                    error_code=SAMPLING_DENIED,
                    attribution=self.attribution,
                    invocation_id=self.invocation_id,
                )
        except AuditUnavailableError:
            pass  # logged at ERROR by the AuditLog; the request is refused anyway
        return refusal

    async def begin(self, consent: ConsentOutcome) -> None:
        with anyio.CancelScope(shield=True):
            await self._audit.begin(
                consumer=self.consumer,
                decision_source=consent.source,
                server_id=self.server_id,
                operation=OperationKind.SAMPLING,
                rule_id=consent.rule_id,
                attribution=self.attribution,
                invocation_id=self.invocation_id,
            )

    async def finish(self, outcome: InvocationOutcome, error_code: str | None) -> None:
        with anyio.CancelScope(shield=True):
            await self._audit.finish(
                self.invocation_id, outcome=outcome, duration_ms=self._ms(), error_code=error_code
            )

    async def end(self, outcome: InvocationOutcome, refusal: Refusal) -> Refusal:
        await self.finish(outcome, refusal.reason)
        return refusal
