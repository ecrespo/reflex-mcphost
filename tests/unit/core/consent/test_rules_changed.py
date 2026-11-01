"""``consent.rules_changed`` (F8-12; API §4.11, delta D11, REQ-UI-006).

``ConsentPolicy`` publishes one ``ConsentRulesChanged`` after every rule change it made
durable — a programmatic grant, a lasting prompt answer, an edit, a revoke, a suspension —
and after a session rule changed in memory; never after a failed write. The event carries
rule ids and the kind of change only: no argument values, no patterns.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from reflex_mcphost.core.consent.models import ArgumentPreview, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.store import ConsentStoreError, UnknownRuleError
from reflex_mcphost.core.events import (
    ConsentRequested,
    ConsentResolved,
    ConsentRulesChanged,
    HostEvent,
    host_event_adapter,
)
from reflex_mcphost.core.types import Consumer
from tests.unit.core.consent.conftest import Env, FakePrompter, answer
from tests.unit.core.consent.factories import tool_entry, tool_request

pytestmark = pytest.mark.anyio
BOT = Consumer.agent("bot")
ECHO = tool_entry("srv::echo")
SENTINEL = "rules-changed-arg-sentinel-0812"


def _changes(events: list[HostEvent]) -> list[ConsentRulesChanged]:
    return [e for e in events if isinstance(e, ConsentRulesChanged)]


async def _grant(env: Env, **kwargs: Any) -> Any:
    return await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.server("srv"),
        **kwargs,
    )


def _broken_db(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_: Any) -> Any:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(env.db, "run", broken)


async def test_grant_update_and_revoke_each_publish_one_change(env: Env) -> None:
    """REQ-UI-006, REQ-CNS-013, REQ-CNS-014 (delta D11): a programmatic grant, an edit and a
    revoke each publish exactly one ``consent.rules_changed`` naming the rule."""
    rule, events = await env.collect(lambda: _grant(env))
    assert _changes(events) == [ConsentRulesChanged(rule_ids=(rule.id,), change="granted")]
    _, events = await env.collect(lambda: env.policy.update(rule.id, effect="deny"))
    assert _changes(events) == [ConsentRulesChanged(rule_ids=(rule.id,), change="updated")]
    _, events = await env.collect(lambda: env.policy.revoke(rule.id))
    assert _changes(events) == [ConsentRulesChanged(rule_ids=(rule.id,), change="revoked")]


async def test_session_rules_publish_too(env: Env) -> None:
    """REQ-UI-006 (delta D11): a session rule lives in memory only; granting, editing and
    revoking it still publishes the change."""
    rule, events = await env.collect(lambda: _grant(env, lifetime="session"))
    assert rule.lifetime == "session"
    assert [c.change for c in _changes(events)] == ["granted"]
    _, events = await env.collect(lambda: env.policy.update(rule.id, lifetime="always"))
    assert [c.change for c in _changes(events)] == ["updated"]
    _, events = await env.collect(lambda: env.policy.revoke(rule.id))
    assert [c.change for c in _changes(events)] == ["revoked"]


@pytest.mark.parametrize("decision", ["allow_always", "allow_session"])
async def test_a_lasting_prompt_answer_publishes_granted_inside_the_prompt(
    env: Env, decision: str
) -> None:
    """REQ-UI-006, REQ-CNS-006 (delta D11): an ``allow_always`` / ``allow_session`` answer
    stores a rule and publishes ``granted`` between ``consent.requested`` and
    ``consent.resolved``; the event holds no argument value."""
    env.prompter = FakePrompter(answer(decision))
    request = tool_request(ECHO, consumer=BOT).model_copy(
        update={"preview": ArgumentPreview(body=f'{{"text": "{SENTINEL}"}}')}
    )
    outcome, events = await env.collect(lambda: env.policy.evaluate(request))
    assert outcome.rule_id is not None  # type: ignore[attr-defined]
    assert [type(e) for e in events] == [ConsentRequested, ConsentRulesChanged, ConsentResolved]
    change = _changes(events)[0]
    assert change == ConsentRulesChanged(rule_ids=(outcome.rule_id,), change="granted")  # type: ignore[attr-defined]
    assert SENTINEL not in host_event_adapter.dump_json(change).decode()


async def test_answers_that_store_nothing_publish_nothing(env: Env) -> None:
    """REQ-UI-006 (delta D11): ``allow_once`` and a once-only deny change no rule, so no
    ``consent.rules_changed`` is published."""
    env.prompter = FakePrompter(answer("allow_once"), answer("deny"))
    for _ in range(2):
        _, events = await env.collect(lambda: env.policy.evaluate(tool_request(ECHO, consumer=BOT)))
        assert _changes(events) == []
    assert env.policy.rules() == []


async def test_a_suspension_publishes_suspended_with_the_rule_ids(env: Env) -> None:
    """REQ-UI-006, REQ-CNS-007 (delta D11): a changed tool definition suspends its ``allow``
    tool rule; the suspension is published once (``suspended``, the rule's id) before the
    re-ask."""
    env.tools[str(ECHO.fqn)] = ECHO
    rule = await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.tool(ECHO.fqn),
        consumer=BOT,
    )
    changed = tool_entry("srv::echo", description="Echo, and also something else")
    env.prompter = FakePrompter(answer("allow_once"))
    _, events = await env.collect(lambda: env.policy.evaluate(tool_request(changed, consumer=BOT)))
    assert _changes(events) == [ConsentRulesChanged(rule_ids=(rule.id,), change="suspended")]
    assert isinstance(events[0], ConsentRulesChanged)  # published before the prompt


async def _no_change_when(
    env: Env, action: Callable[[], Awaitable[Any]], error: type[BaseException]
) -> None:
    async def run() -> None:
        with pytest.raises(error):
            await action()

    _, events = await env.collect(run)
    assert _changes(events) == []


async def test_failed_writes_publish_nothing(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-UI-006, REQ-CNS-008 (delta D11): when the store write fails — a grant, an edit,
    a revoke — or the rule is unknown, nothing is published."""
    rule = await _grant(env)
    await _no_change_when(env, lambda: env.policy.update("no-such-rule"), UnknownRuleError)
    await _no_change_when(env, lambda: env.policy.revoke("no-such-rule"), UnknownRuleError)
    _broken_db(env, monkeypatch)
    await _no_change_when(env, lambda: _grant(env, consumer=BOT), ConsentStoreError)
    await _no_change_when(env, lambda: env.policy.update(rule.id, effect="deny"), ConsentStoreError)
    await _no_change_when(env, lambda: env.policy.revoke(rule.id), ConsentStoreError)


async def test_a_failed_lasting_answer_publishes_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-UI-006, REQ-CNS-008 (DD-005, delta D11): an ``allow_always`` whose write-through
    fails applies once and stores no rule — no ``consent.rules_changed``."""
    env.prompter = FakePrompter(answer("allow_always"))
    _broken_db(env, monkeypatch)
    outcome, events = await env.collect(
        lambda: env.policy.evaluate(tool_request(ECHO, consumer=BOT))
    )
    assert outcome.rule_id is None  # type: ignore[attr-defined]
    assert _changes(events) == []


async def test_a_failed_suspension_publishes_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-UI-006, REQ-CNS-008 (delta D11): a suspension the store cannot persist denies
    (fail closed) and publishes no change."""
    env.tools[str(ECHO.fqn)] = ECHO
    await env.policy.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.tool(ECHO.fqn),
        consumer=BOT,
    )
    _broken_db(env, monkeypatch)
    changed = tool_entry("srv::echo", description="Echo, and also something else")
    outcome, events = await env.collect(
        lambda: env.policy.evaluate(tool_request(changed, consumer=BOT))
    )
    assert outcome.allowed is False  # type: ignore[attr-defined]
    assert _changes(events) == []


def test_the_event_carries_ids_and_the_change_only() -> None:
    """REQ-UI-006 (API §4.11, delta D11): the payload is ``type``, ``rule_ids`` and
    ``change`` ∈ granted / updated / revoked / suspended; it round-trips through the
    ``HostEvent`` union."""
    event = ConsentRulesChanged(rule_ids=("r1", "r2"), change="revoked")
    assert set(event.model_dump()) == {"type", "rule_ids", "change"}
    assert host_event_adapter.validate_json(host_event_adapter.dump_json(event)) == event
    with pytest.raises(ValueError, match="change"):
        ConsentRulesChanged(rule_ids=("r1",), change="deleted")  # type: ignore[arg-type]
