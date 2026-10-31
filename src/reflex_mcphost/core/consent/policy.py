"""``ConsentPolicy``: the single consent gate for effectful operations (API §4.7, Art. 3).

``evaluate`` implements the normative algorithm:

0. ``unattributed`` requests (REQ-HSH-047) see only deny rules and never create rules;
1-2. matching rules ranked ``(target, consumer)``; deny wins at the top rank (REQ-CNS-003,
   REQ-CNS-015) — ``RuleIndex``;
3. stale tool rules are suspended through the ``ConsentStore`` (persisted) and the request
   is marked ``definition_changed`` (REQ-CNS-007);
4. otherwise the prompter is asked within ``consent_timeout_s`` (``timeout`` deny,
   REQ-CNS-009); no prompter → ``no_prompter`` deny (REQ-CNS-010);
5. any exception → deny with ``source="error"`` and the reason (type names only, never
   argument values — Art. 5; REQ-CNS-008);
6. answers become rules per the mapping table, scoped to the *requesting* Consumer
   (REQ-CNS-015); ``target_kind`` widens them.

``consent.requested`` is published before the prompter is asked and ``consent.resolved``
after it returns, fails, times out or is cancelled (Flow D). A persistent answer whose
write-through fails still applies once and the outcome says so (DD-005).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import anyio

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.config.models import DEFAULT_SECRET_ARG_PATTERN
from reflex_mcphost.core.consent.models import (
    ArgumentPreview,
    ArgumentPreviewHook,
    Attribution,
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentRule,
    ConsentTarget,
    Effect,
    Lifetime,
)
from reflex_mcphost.core.consent.outcome import ConsentOutcome, OperationKind
from reflex_mcphost.core.consent.preview import PreviewRegistry
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.events import ConsentRequested, ConsentResolved, EventBus
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.prompter import Prompter
from reflex_mcphost.core.types import Consumer, Fqn

__all__ = ["DEFAULT_CONSENT_TIMEOUT_S", "ConsentPolicy"]

DEFAULT_CONSENT_TIMEOUT_S = 120.0
_log = get_logger(__name__)


class _UnevaluableError(Exception):
    """The policy cannot decide; ``str`` is the recorded reason."""


def _no_prompter() -> Prompter | None:
    return None


def _deny(source: Any, reason: str) -> ConsentOutcome:
    return ConsentOutcome(allowed=False, source=source, reason=reason)


class ConsentPolicy:
    """Rules, prompting and previews behind one ``evaluate`` (API §4.7).

    ``prompter`` returns the current prompter (the Host's ``set_prompter`` takes effect on
    the next evaluation); ``tool_lookup`` resolves a tool's current entry for programmatic
    tool grants (the Host passes ``Catalog.get_tool``). Until ``attach`` gives it a
    ``ConsentStore`` every evaluation denies with ``source="error"`` (fail closed).
    """

    def __init__(
        self,
        *,
        events: EventBus,
        prompter: Callable[[], Prompter | None] = _no_prompter,
        tool_lookup: Callable[[Fqn], ToolEntry] | None = None,
        timeout_s: float = DEFAULT_CONSENT_TIMEOUT_S,
        secret_arg_pattern: str = DEFAULT_SECRET_ARG_PATTERN,
    ) -> None:
        self._events = events
        self._prompter = prompter
        self._tool_lookup = tool_lookup
        self._timeout_s = timeout_s
        self._previews = PreviewRegistry(secret_arg_pattern)
        self._store: ConsentStore | None = None

    async def attach(
        self,
        store: ConsentStore,
        *,
        timeout_s: float | None = None,
        secret_arg_pattern: str | None = None,
    ) -> None:
        """Load persistent rules (Q-C1) and start deciding with ``store``. Raises
        ``ConsentStoreError`` if the rules cannot be read."""
        await store.load()
        if timeout_s is not None:
            self._timeout_s = timeout_s
        if secret_arg_pattern is not None:
            self._previews.secret_arg_pattern = secret_arg_pattern
        self._store = store

    @property
    def store(self) -> ConsentStore | None:
        return self._store

    def _require_store(self) -> ConsentStore:
        if self._store is None:
            raise RuntimeError("the consent store is not attached (Host.start() has not run)")
        return self._store

    # ------------------------------------------------------------------ management

    def rules(self, *, include_session: bool = True) -> list[ConsentRule]:
        """Persistent and session rules, newest first (REQ-CNS-013; sync)."""
        if self._store is None:
            return []
        return self._store.rules(include_session=include_session)

    async def grant(
        self,
        *,
        effect: Effect,
        operation: OperationKind,
        target: ConsentTarget,
        consumer: Consumer | None = None,
        lifetime: Lifetime = "always",
    ) -> ConsentRule:
        """Set a rule programmatically, origin ``programmatic`` (REQ-CNS-014).

        A tool target binds the tool's current definition hash (REQ-CNS-007), so the tool
        must be in the catalog (``UnknownEntryError`` otherwise).
        """
        store = self._require_store()
        definition_hash = None
        if target.fqn is not None:
            if self._tool_lookup is None:
                raise RuntimeError("no catalog to resolve the tool's definition hash")
            definition_hash = self._tool_lookup(target.fqn).definition_hash
        return await store.grant(
            effect=effect,
            operation=operation,
            target=target,
            consumer=consumer,
            lifetime=lifetime,
            definition_hash=definition_hash,
        )

    async def update(
        self,
        rule_id: str,
        *,
        effect: Effect | None = None,
        lifetime: Lifetime | None = None,
    ) -> ConsentRule:
        """Edit a rule; the next evaluation sees it (REQ-CNS-013)."""
        return await self._require_store().update(rule_id, effect=effect, lifetime=lifetime)

    async def revoke(self, rule_id: str) -> None:
        """Delete a rule; the next evaluation no longer sees it (REQ-CNS-013)."""
        await self._require_store().revoke(rule_id)

    # ------------------------------------------------------------------ previews

    def register_preview(self, target: ConsentTarget, hook: ArgumentPreviewHook) -> None:
        """Render previews for tools under ``target`` with ``hook`` (REQ-CNS-011)."""
        self._previews.register(target, hook)

    async def preview(self, tool: ToolEntry, arguments: Mapping[str, Any]) -> ArgumentPreview:
        """The hook's or the redacted default preview of ``arguments`` (REQ-CNS-011/012)."""
        return await self._previews.render(tool, arguments)

    async def tool_request(
        self,
        tool: ToolEntry,
        arguments: Mapping[str, Any],
        *,
        consumer: Consumer,
        server_name: str,
        attribution: Attribution = "exact",
    ) -> ConsentRequest:
        """Build the ``ConsentRequest`` of a ``tools/call`` (used by the Invoker)."""
        return ConsentRequest(
            operation=OperationKind.TOOL_CALL,
            consumer=consumer,
            server_id=tool.server_id,
            server_name=server_name,
            tool=tool,
            preview=await self.preview(tool, arguments),
            attribution=attribution,
        )

    # ------------------------------------------------------------------ evaluation

    async def evaluate(self, request: ConsentRequest) -> ConsentOutcome:
        """Decide ``request`` (steps 0-6). Never raises except on cancellation."""
        log = ContextAdapter(
            _log,
            server_id=request.server_id,
            fqn=str(request.fqn) if request.fqn is not None else None,
            request_id=request.id,
        )
        try:
            store = self._require_store()
            if store.is_poisoned(request.server_id):
                raise _UnevaluableError("a stored consent rule for this server is corrupt")
            tool = request.tool
            result = store.index.evaluate(
                operation=request.operation,
                server_id=request.server_id,
                fqn=tool.fqn if tool is not None else None,
                consumer=request.consumer,
                definition_hash=tool.definition_hash if tool is not None else None,
                attribution=request.attribution,
            )
            if result.to_suspend:
                await store.suspend(result.to_suspend)
                log.warning(
                    "tool definition changed; suspended %d consent rule(s)", len(result.to_suspend)
                )
            if result.rule is not None:
                allowed = result.effect == "allow"
                return ConsentOutcome(
                    allowed=allowed,
                    source="rule",
                    rule_id=result.rule.id,
                    reason=None if allowed else "denied by a stored rule",
                )
            if result.definition_changed and not request.definition_changed:
                request = request.model_copy(update={"definition_changed": True})
        except _UnevaluableError as exc:
            log.error("consent denied: %s", exc)
            return _deny("error", str(exc))
        except Exception as exc:
            log.error("consent evaluation failed (%s); denying", type(exc).__name__)
            return _deny("error", f"consent policy unavailable ({type(exc).__name__})")
        return await self._ask(request, store, log)

    async def _ask(
        self, request: ConsentRequest, store: ConsentStore, log: ContextAdapter
    ) -> ConsentOutcome:
        prompter = self._prompter()
        if prompter is None:
            return _deny("no_prompter", "no consent prompter attached")
        self._events.publish(
            ConsentRequested(
                request_id=request.id,
                operation=request.operation,
                consumer=request.consumer,
                server_id=request.server_id,
                fqn=request.fqn,
            )
        )
        outcome = _deny("error", "consent prompt cancelled")
        try:
            try:
                with anyio.fail_after(self._timeout_s):
                    answer: object = await prompter.ask_consent(request)
            except TimeoutError:
                outcome = _deny(
                    "timeout", f"consent prompt not answered within {self._timeout_s:g} s"
                )
            except Exception as exc:
                log.warning("consent prompter failed (%s); denying", type(exc).__name__)
                outcome = _deny("error", f"consent prompter failed ({type(exc).__name__})")
            else:
                outcome = await self._apply(request, answer, store, log)
        finally:
            self._events.publish(
                ConsentResolved(
                    request_id=request.id,
                    operation=request.operation,
                    consumer=request.consumer,
                    server_id=request.server_id,
                    fqn=request.fqn,
                    outcome=outcome,
                )
            )
        return outcome

    async def _apply(
        self, request: ConsentRequest, answer: object, store: ConsentStore, log: ContextAdapter
    ) -> ConsentOutcome:
        """Step 6: map the answer to an outcome and, if it lasts, to a rule."""
        if not isinstance(answer, ConsentAnswer):
            return _deny(
                "error", f"consent prompter returned {type(answer).__name__}, not a ConsentAnswer"
            )
        decision = answer.decision
        once = decision is ConsentDecision.ALLOW_ONCE or (
            decision is ConsentDecision.DENY and answer.deny_lifetime == "once"
        )
        allowed = decision is not ConsentDecision.DENY
        if request.attribution == "unattributed" and not once:
            return _deny("prompt", "only allow_once or deny once apply to unattributed requests")
        edited = (
            answer.edited_sampling
            if allowed and request.operation is OperationKind.SAMPLING
            else None
        )
        if once:
            return ConsentOutcome(allowed=allowed, source="prompt", edited_sampling=edited)
        lifetime: Lifetime = (
            "session"
            if decision is ConsentDecision.ALLOW_SESSION
            or (decision is ConsentDecision.DENY and answer.deny_lifetime == "session")
            else "always"
        )
        target = self._answer_target(request, answer.target_kind)
        try:
            rule = await store.grant(
                effect="allow" if allowed else "deny",
                operation=request.operation,
                target=target,
                consumer=request.consumer,
                lifetime=lifetime,
                origin="prompt",
                definition_hash=request.tool.definition_hash if request.tool is not None else None,
            )
        except Exception as exc:
            log.error(
                "consent answer applied once: the rule could not be stored (%s)",
                type(exc).__name__,
            )
            return ConsentOutcome(
                allowed=allowed,
                source="prompt",
                reason=f"answer applied once: the rule could not be stored ({type(exc).__name__})",
                edited_sampling=edited,
            )
        return ConsentOutcome(
            allowed=allowed, source="prompt", rule_id=rule.id, edited_sampling=edited
        )

    @staticmethod
    def _answer_target(request: ConsentRequest, kind: str) -> ConsentTarget:
        if kind == "global":
            return ConsentTarget.global_()
        if kind == "tool" and request.tool is not None:
            return ConsentTarget.tool(request.tool.fqn)
        # sampling has no tool target: its narrowest scope is the Server (API §4.14)
        return ConsentTarget.server(request.server_id)
