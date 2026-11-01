"""``ConsentPolicy``: evaluation steps 0-6, prompting and fail-closed paths (F3-03)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import anyio
import pytest
from mcp_types import CreateMessageRequestParams, SamplingMessage, TextContent
from sqlmodel import select

from reflex_mcphost.core.consent.models import ConsentRequest, ConsentTarget
from reflex_mcphost.core.consent.outcome import ConsentOutcome, OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore, UnknownRuleError
from reflex_mcphost.core.db_models import ConsentRuleRow
from reflex_mcphost.core.errors import UnknownEntryError
from reflex_mcphost.core.events import ConsentRequested, ConsentResolved, EventBus
from reflex_mcphost.core.prompter import DenyAllPrompter
from reflex_mcphost.core.types import Consumer
from tests.unit.core.consent.conftest import Env, FakePrompter, answer
from tests.unit.core.consent.factories import sampling_request, tool_entry, tool_request

pytestmark = pytest.mark.anyio
BOT = Consumer.agent("bot")
HUMAN = Consumer.human()
ECHO = tool_entry("srv::echo")
OTHER_TOOL = tool_entry("srv::other")


def db_rules(env: Env) -> list[ConsentRuleRow]:
    with env.db.session() as s:
        return list(s.exec(select(ConsentRuleRow)).all())


async def test_headless_denies_without_rule(env: Env) -> None:
    """REQ-CNS-010: no prompter attached → deny unless a stored allow matches."""
    outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert outcome == ConsentOutcome(
        allowed=False, source="no_prompter", reason="no consent prompter attached"
    )
    env.tools[str(ECHO.fqn)] = ECHO
    rule = await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.tool(ECHO.fqn),
        consumer=BOT,
    )
    assert rule.origin == "programmatic"
    assert rule.definition_hash == ECHO.definition_hash
    outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source, outcome.rule_id) == (True, "rule", rule.id)


async def test_prompt_shows_request_and_emits_events(env: Env) -> None:
    """REQ-CNS-001, REQ-CNS-002: unmatched operations ask the Consumer; events bracket it."""
    env.prompter = prompter = FakePrompter(answer("allow_once"))
    request = tool_request(ECHO, consumer=BOT)
    outcome, events = await env.collect(lambda: env.policy.evaluate(request))
    assert outcome == ConsentOutcome(allowed=True, source="prompt")
    assert prompter.requests == [request]
    shown = prompter.requests[0]
    assert shown.tool is not None
    assert (str(shown.fqn), shown.server_name, shown.tool.description) == (
        "srv::echo",
        "Fixture Server",
        "Echo a value",
    )
    assert [type(e) for e in events] == [ConsentRequested, ConsentResolved]
    requested, resolved = events
    assert isinstance(requested, ConsentRequested)
    assert isinstance(resolved, ConsentResolved)
    assert (requested.request_id, requested.operation, requested.consumer) == (
        request.id,
        OperationKind.TOOL_CALL,
        BOT,
    )
    assert str(requested.fqn) == "srv::echo"
    assert resolved.outcome == outcome
    assert "arguments" not in resolved.model_dump()


async def test_allow_once_stores_nothing(env: Env) -> None:
    """REQ-CNS-004: ``allow_once`` allows this invocation only and stores nothing."""
    env.prompter = prompter = FakePrompter(answer("allow_once"))
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).allowed
    assert env.policy.rules() == []
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).source == "prompt"
    assert len(prompter.requests) == 2


async def test_allow_session_in_memory(env: Env) -> None:
    """REQ-CNS-005: ``allow_session`` allows until the session ends, never persisted."""
    env.prompter = prompter = FakePrompter(answer("allow_session"))
    first = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (first.allowed, first.source) == (True, "prompt")
    [rule] = env.policy.rules()
    assert (rule.lifetime, rule.origin, rule.id) == ("session", "prompt", first.rule_id)
    second = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (second.allowed, second.source, second.rule_id) == (True, "rule", rule.id)
    assert len(prompter.requests) == 1
    assert db_rules(env) == []
    policy = await env.restart()
    assert policy.rules() == []


async def test_allow_always_persists(env: Env) -> None:
    """REQ-CNS-006: ``allow_always`` is persisted and applies across restarts."""
    env.prompter = FakePrompter(answer("allow_always"))
    await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    env.prompter = None
    policy = await env.restart()
    outcome = await policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (True, "rule")
    assert policy.rules(include_session=False)[0].definition_hash == ECHO.definition_hash


@pytest.mark.parametrize(
    ("lifetime", "stored", "persisted"),
    [("once", False, False), ("session", True, False), ("always", True, True)],
)
async def test_deny_lifetimes(env: Env, lifetime: str, stored: bool, persisted: bool) -> None:
    """REQ-CNS-006: ``deny`` is once (default), session or persistent per ``deny_lifetime``."""
    env.prompter = prompter = FakePrompter(answer("deny", deny_lifetime=lifetime))
    outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (False, "prompt")
    assert bool(env.policy.rules()) is stored
    assert bool(db_rules(env)) is persisted
    again = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert again.allowed is False
    assert again.source == ("rule" if stored else "prompt")
    assert len(prompter.requests) == (1 if stored else 2)


@pytest.mark.parametrize("target_kind", ["server", "global"])
async def test_target_kind_widens_rule(env: Env, target_kind: str) -> None:
    """REQ-CNS-003, REQ-CNS-007: a server/global answer covers other tools, incl. new ones."""
    env.prompter = prompter = FakePrompter(answer("allow_session", target_kind=target_kind))
    await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    [rule] = env.policy.rules()
    assert rule.target.kind == target_kind
    assert rule.definition_hash is None
    later = await env.policy.evaluate(tool_request(OTHER_TOOL, consumer=BOT))
    assert (later.allowed, later.source) == (True, "rule")
    elsewhere = await env.policy.evaluate(tool_request(tool_entry("x::t"), consumer=BOT))
    assert elsewhere.source == ("rule" if target_kind == "global" else "prompt")
    assert len(prompter.requests) == (1 if target_kind == "global" else 2)


async def test_human_answer_scopes_rule_to_requesting_agent(env: Env) -> None:
    """REQ-CNS-015: a human approving an agent's call grants it to that agent only."""
    env.prompter = prompter = FakePrompter(answer("allow_always"))
    await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    [rule] = env.policy.rules()
    assert rule.consumer == BOT
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).source == "rule"
    other = await env.policy.evaluate(tool_request(ECHO, consumer=Consumer.agent("other")))
    assert other.source == "prompt"
    human = await env.policy.evaluate(tool_request(ECHO, consumer=HUMAN))
    assert human.source == "prompt"
    assert len(prompter.requests) == 3


async def test_more_specific_deny_beats_broad_allow(env: Env) -> None:
    """REQ-CNS-003: tool > server > global; Consumer-specific > any; deny wins at top rank."""
    env.tools[str(ECHO.fqn)] = ECHO
    await env.policy.grant(
        effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.global_()
    )
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).allowed
    await env.policy.grant(
        effect="deny", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server("srv")
    )
    assert not (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).allowed
    await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.server("srv"),
        consumer=BOT,
    )
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).allowed
    assert not (await env.policy.evaluate(tool_request(ECHO, consumer=HUMAN))).allowed


async def test_store_unavailable_denies(env_sync: Env) -> None:
    """REQ-CNS-008: no store attached (or a failing index) → deny with source ``error``."""
    outcome = await env_sync.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (False, "error")
    assert outcome.reason == "consent policy unavailable (RuntimeError)"
    assert env_sync.policy.rules() == []


async def test_store_raising_denies(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-CNS-008: an exception while evaluating stored rules denies (fail closed)."""
    env.prompter = FakePrompter(answer("allow_once"))
    store = env.policy.store
    assert store is not None

    def broken(**_: Any) -> Any:
        raise sqlite3.DatabaseError("corrupt")

    monkeypatch.setattr(store.index, "evaluate", broken)
    outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (False, "error")
    assert outcome.reason == "consent policy unavailable (DatabaseError)"


async def test_corrupt_rule_denies_its_server(env_sync: Env) -> None:
    """REQ-CNS-008: a corrupt stored rule makes its Server's operations deny."""
    raw = sqlite3.connect(env_sync.db.path)
    with raw:
        raw.execute(
            "INSERT INTO consent_rule (rule_id, effect, operation, target_kind, server_id, fqn,"
            " consumer_kind, consumer_name, origin, created_at_us, updated_at_us) VALUES"
            " ('rule_01JAAAAAAAAAAAAAAAAAAAAAAA', 'deny', 'tool_call', 'server', 'srv', '',"
            " 'agent', 'bad name', 'prompt', 1, 1)"
        )
    raw.close()
    await env_sync.attach()
    env_sync.prompter = FakePrompter(answer("allow_once"))
    outcome = await env_sync.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (False, "error")
    assert outcome.reason == "a stored consent rule for this server is corrupt"
    elsewhere = await env_sync.policy.evaluate(tool_request(tool_entry("x::t"), consumer=BOT))
    assert elsewhere.allowed


@pytest.mark.parametrize(
    ("prompter", "source", "reason"),
    [
        (
            FakePrompter(error=RuntimeError("secret-in-msg")),
            "error",
            "consent prompter failed (RuntimeError)",
        ),
        (
            FakePrompter("not an answer"),
            "error",
            "consent prompter returned str, not a ConsentAnswer",
        ),
        (FakePrompter(hang=True), "timeout", "consent prompt not answered within 0.05 s"),
    ],
)
async def test_prompter_faults_deny(
    env_sync: Env, prompter: FakePrompter, source: str, reason: str
) -> None:
    """REQ-CNS-008, REQ-CNS-009: prompter raising, misbehaving or hanging → deny."""
    await env_sync.attach(timeout_s=0.05)
    env_sync.prompter = prompter
    outcome, events = await env_sync.collect(
        lambda: env_sync.policy.evaluate(tool_request(ECHO, consumer=BOT))
    )
    assert outcome == ConsentOutcome(allowed=False, source=source, reason=reason)  # type: ignore[arg-type]
    assert "secret-in-msg" not in (outcome.reason or "")
    assert isinstance(events[-1], ConsentResolved)
    assert events[-1].outcome == outcome
    assert env_sync.policy.rules() == []


async def test_deny_all_prompter(env: Env) -> None:
    """REQ-CNS-010: ``DenyAllPrompter`` denies every prompt explicitly."""
    env.prompter = DenyAllPrompter()
    outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source) == (False, "prompt")
    assert env.policy.rules() == []


async def test_write_through_failure_applies_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-CNS-008 (DD-005): a failed persist applies the answer once and says so."""
    env.prompter = prompter = FakePrompter(answer("allow_always"))

    async def broken(*_: Any) -> Any:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(env.db, "run", broken)
    with caplog.at_level(logging.ERROR, logger="reflex_mcphost"):
        outcome = await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    assert (outcome.allowed, outcome.source, outcome.rule_id) == (True, "prompt", None)
    assert outcome.reason == "answer applied once: the rule could not be stored (ConsentStoreError)"
    assert env.policy.rules() == []
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).source == "prompt"
    assert len(prompter.requests) == 2


@pytest.mark.parametrize(
    ("decision", "kwargs", "allowed"),
    [
        ("allow_once", {}, True),
        ("deny", {}, False),
        ("allow_session", {}, False),
        ("allow_always", {"target_kind": "global"}, False),
        ("deny", {"deny_lifetime": "always"}, False),
    ],
)
async def test_unattributed_answers(
    env: Env, decision: str, kwargs: dict[str, Any], allowed: bool
) -> None:
    """REQ-HSH-047: only allow_once / deny once apply; anything else denies; no rule kept."""
    env.prompter = prompter = FakePrompter(answer(decision, **kwargs))
    request = sampling_request(consumer=HUMAN, attribution="unattributed")
    outcome = await env.policy.evaluate(request)
    assert (outcome.allowed, outcome.source) == (allowed, "prompt")
    assert prompter.requests[0].attribution == "unattributed"
    assert env.policy.rules() == []


async def test_unattributed_respects_stored_deny_and_ignores_allow(env: Env) -> None:
    """REQ-HSH-047: stored deny rules (any Consumer) apply; allow rules never do."""
    sampling = OperationKind.SAMPLING
    await env.policy.grant(
        effect="allow", operation=sampling, target=ConsentTarget.server("srv"), consumer=HUMAN
    )
    request = sampling_request(consumer=HUMAN, attribution="unattributed")
    assert (await env.policy.evaluate(request)).source == "no_prompter"
    assert (await env.policy.evaluate(sampling_request(consumer=HUMAN))).source == "rule"
    deny = await env.policy.grant(
        effect="deny", operation=sampling, target=ConsentTarget.server("srv"), consumer=BOT
    )
    outcome = await env.policy.evaluate(request)
    assert (outcome.allowed, outcome.source, outcome.rule_id) == (False, "rule", deny.id)


async def test_sampling_answers_scope_to_server_and_pass_edits(env: Env) -> None:
    """REQ-CNS-001: sampling rules target the Server; edited params flow to the outcome."""
    edited = CreateMessageRequestParams(
        messages=[SamplingMessage(role="user", content=TextContent(type="text", text="hi"))],
        max_tokens=10,
    )
    env.prompter = FakePrompter(answer("allow_session", edited_sampling=edited))
    outcome = await env.policy.evaluate(sampling_request(consumer=BOT))
    assert outcome.allowed
    assert outcome.edited_sampling == edited
    [rule] = env.policy.rules()
    assert (rule.operation, rule.target, rule.consumer) == (
        OperationKind.SAMPLING,
        ConsentTarget.server("srv"),
        BOT,
    )
    env.prompter = FakePrompter(answer("deny", edited_sampling=edited))
    denied = await env.policy.evaluate(sampling_request(consumer=Consumer.agent("x")))
    assert denied.edited_sampling is None


async def test_cancelled_prompt_resolves_and_reraises(env: Env) -> None:
    """REQ-CNS-002: a cancelled evaluation still publishes ``consent.resolved`` (deny)."""
    env.prompter = FakePrompter(hang=True)
    async with env.events.subscribe() as sub:
        with anyio.move_on_after(0.05):
            await env.policy.evaluate(tool_request(ECHO, consumer=BOT))
            pytest.fail("not cancelled")
        events = []
        with anyio.move_on_after(0.05):
            async for event in sub:
                events.append(event)
    assert isinstance(events[-1], ConsentResolved)
    assert events[-1].outcome.allowed is False
    assert events[-1].outcome.reason == "consent prompt cancelled"


async def test_grant_update_revoke_live(env: Env) -> None:
    """REQ-CNS-013, REQ-CNS-014: programmatic rules are listed, edited and revoked live."""
    with pytest.raises(UnknownEntryError):
        await env.policy.grant(
            effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.tool(ECHO.fqn)
        )
    rule = await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.server("srv"),
        consumer=BOT,
        lifetime="session",
    )
    assert env.policy.rules() == [rule]
    assert env.policy.rules(include_session=False) == []
    updated = await env.policy.update(rule.id, effect="deny", lifetime="always")
    assert (updated.effect, updated.lifetime) == ("deny", "always")
    assert not (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).allowed
    await env.policy.revoke(rule.id)
    assert (await env.policy.evaluate(tool_request(ECHO, consumer=BOT))).source == "no_prompter"
    with pytest.raises(UnknownRuleError):
        await env.policy.revoke(rule.id)


async def test_management_before_attach_raises() -> None:
    policy = ConsentPolicy(events=EventBus())
    with pytest.raises(RuntimeError):
        await policy.revoke("rule_01JAAAAAAAAAAAAAAAAAAAAAAA")
    assert policy.store is None


async def test_tool_request_builds_redacted_preview(env_sync: Env) -> None:
    """REQ-CNS-002, REQ-CNS-012: the request carries the redacted preview, never raw secrets."""
    await env_sync.attach(pattern=r"(?i)^pin$")
    tool = tool_entry("srv::echo")
    request = await env_sync.policy.tool_request(
        tool, {"pin": "9876", "token": "t"}, consumer=BOT, server_name="Fixture Server"
    )
    assert isinstance(request, ConsentRequest)
    assert "9876" not in request.preview.body
    assert '"token": "t"' in request.preview.body
    assert request.preview.redacted_fields == ("pin",)
    env_sync.policy.register_preview(
        ConsentTarget.tool("srv::echo"),
        lambda _t, _a: request.preview.model_copy(update={"body": "custom"}),
    )
    assert (await env_sync.policy.preview(tool, {})).body == "custom"


async def test_store_property_and_reattach(env: Env) -> None:
    assert isinstance(env.policy.store, ConsentStore)
