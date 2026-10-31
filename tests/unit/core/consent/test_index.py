"""In-memory ``RuleIndex``: evaluation steps 0-3 (F3-01, API §4.7, DD-005)."""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from reflex_mcphost.core.consent.index import IndexEvaluation, RuleIndex
from reflex_mcphost.core.consent.models import ConsentRule, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.types import Consumer, Fqn
from tests.unit.core.consent.factories import rule, sha, tool_entry

FQN = Fqn.parse("srv::echo")
HASH = tool_entry("srv::echo").definition_hash
ME = Consumer.agent("bot")
OTHER = Consumer.agent("other")

consumers = st.sampled_from([None, ME, OTHER, Consumer.human()])
targets = st.sampled_from(
    [
        ConsentTarget.tool("srv::echo"),
        ConsentTarget.server("srv"),
        ConsentTarget.global_(),
        ConsentTarget.tool("srv::other"),
        ConsentTarget.server("elsewhere"),
    ]
)
effects = st.sampled_from(["allow", "deny"])
rules_st = st.builds(lambda e, t, c: rule(e, target=t, consumer=c), effects, targets, consumers)


def evaluate(
    index: RuleIndex, consumer: Consumer = ME, *, attribution: str = "exact"
) -> IndexEvaluation:
    return index.evaluate(
        operation=OperationKind.TOOL_CALL,
        server_id="srv",
        fqn=FQN,
        consumer=consumer,
        definition_hash=HASH,
        attribution=attribution,  # type: ignore[arg-type]
    )


def matching(index: RuleIndex, consumer: Consumer = ME) -> list[ConsentRule]:
    return [
        r
        for r in index.rules()
        if r.operation is OperationKind.TOOL_CALL
        and (r.consumer is None or r.consumer == consumer)
        and (
            r.target.kind == "global"
            or (r.target.kind == "server" and r.target.server_id == "srv")
            or (r.target.kind == "tool" and r.target.fqn == FQN)
        )
    ]


@given(st.lists(rules_st, max_size=12))
def test_reference_model(rules: list[ConsentRule]) -> None:
    """REQ-CNS-003, REQ-CNS-015: only the top rank decides; deny wins within it."""
    index = RuleIndex(rules)
    found = matching(index)
    result = evaluate(index)
    if not found:
        assert result.effect is None
        assert result.rule is None
        return
    top = max(r.rank for r in found)
    expected = "deny" if any(r.effect == "deny" for r in found if r.rank == top) else "allow"
    assert result.effect == expected
    assert result.rule is not None
    assert result.rule.rank == top
    assert result.rule.effect == expected


@given(st.lists(rules_st, min_size=1, max_size=12), st.data())
def test_lower_ranked_rule_never_changes_outcome(
    rules: list[ConsentRule], data: st.DataObject
) -> None:
    """REQ-CNS-003: adding a lower-ranked rule never changes the outcome."""
    index = RuleIndex(rules)
    before = evaluate(index)
    if before.rule is None:
        index.put(rule("allow", target=ConsentTarget.tool("srv::echo"), consumer=ME))
        before = evaluate(index)
    assert before.rule is not None
    existing = {r.key for r in index.rules()}
    candidates = [
        candidate
        for effect in ("allow", "deny")
        for target in (ConsentTarget.server("srv"), ConsentTarget.global_())
        for consumer in (None, ME)
        if (candidate := rule(effect, target=target, consumer=consumer)).rank < before.rule.rank
        and candidate.key not in existing
    ]
    if not candidates:
        return
    index.put(data.draw(st.sampled_from(candidates)))
    after = evaluate(index)
    assert after.effect == before.effect
    assert after.rule == before.rule


@given(st.lists(rules_st, max_size=12), st.sampled_from([None, ME]))
def test_top_rank_deny_always_wins(rules: list[ConsentRule], consumer: Consumer | None) -> None:
    """REQ-CNS-003: a deny at the highest rank present always wins over any allow."""
    index = RuleIndex(rules)
    deny = rule("deny", target=ConsentTarget.tool("srv::echo"), consumer=consumer)
    index.put(deny)
    assume(all(r.rank <= deny.rank for r in matching(index)))
    result = evaluate(index)
    assert result.effect == "deny"


@given(st.lists(st.builds(lambda e, t: rule(e, target=t, consumer=OTHER), effects, targets)))
def test_other_consumers_rule_never_matches(rules: list[ConsentRule]) -> None:
    """REQ-CNS-015: a rule scoped to another Consumer never matches."""
    assert evaluate(RuleIndex(rules)).effect is None


@given(st.lists(rules_st, max_size=12))
def test_unattributed_sees_only_deny_rules(rules: list[ConsentRule]) -> None:
    """REQ-HSH-047: unattributed requests ignore allow rules; any matching deny applies."""
    index = RuleIndex(rules)
    human = Consumer.human()
    result = evaluate(index, human, attribution="unattributed")
    targets_match = [
        r
        for r in index.rules()
        if r.effect == "deny"
        and (
            r.target.kind == "global"
            or (r.target.kind == "server" and r.target.server_id == "srv")
            or (r.target.kind == "tool" and r.target.fqn == FQN)
        )
    ]
    if targets_match:
        assert result.effect == "deny"
        assert result.rule is not None
        assert result.rule.effect == "deny"
    else:
        assert result.effect is None


def test_unattributed_ignores_only_allow() -> None:
    """REQ-HSH-047: a session allow for the human does not apply when unattributed."""
    index = RuleIndex([rule(consumer=Consumer.human(), lifetime="session")])
    assert evaluate(index, Consumer.human()).effect == "allow"
    assert evaluate(index, Consumer.human(), attribution="unattributed").effect is None


def test_stale_tool_rule_reported_for_suspension() -> None:
    """REQ-CNS-007: a tool rule whose hash differs is skipped and reported (step 3)."""
    stale = rule(consumer=ME, definition_hash=sha("old"))
    server = rule(target=ConsentTarget.server("srv"), consumer=ME)
    index = RuleIndex([stale])
    result = evaluate(index)
    assert result.effect is None
    assert result.to_suspend == (stale,)
    assert result.definition_changed
    index.put(server)
    result = evaluate(index)
    assert result.effect == "allow"
    assert result.rule == server
    assert result.to_suspend == (stale,)
    assert result.definition_changed


def test_stale_rule_of_other_consumer_not_reported() -> None:
    index = RuleIndex([rule(consumer=OTHER, definition_hash=sha("old"))])
    result = evaluate(index)
    assert result.to_suspend == ()
    assert not result.definition_changed


def test_suspended_rules_are_skipped() -> None:
    """REQ-CNS-007: suspended rules take no part in evaluation."""
    index = RuleIndex([rule("deny", consumer=ME, suspended=True)])
    result = evaluate(index)
    assert result.effect is None
    assert result.to_suspend == ()


def test_unknown_current_hash_skips_tool_rules_without_suspending() -> None:
    index = RuleIndex([rule(consumer=ME)])
    result = index.evaluate(
        operation=OperationKind.TOOL_CALL,
        server_id="srv",
        fqn=FQN,
        consumer=ME,
        definition_hash=None,
    )
    assert result.effect is None
    assert result.to_suspend == ()


def test_operation_must_match() -> None:
    index = RuleIndex([rule(target=ConsentTarget.server("srv"))])
    result = index.evaluate(
        operation=OperationKind.SAMPLING,
        server_id="srv",
        fqn=None,
        consumer=ME,
        definition_hash=None,
    )
    assert result.effect is None
    sampling = rule(operation=OperationKind.SAMPLING, target=ConsentTarget.server("srv"))
    index.put(sampling)
    result = index.evaluate(
        operation=OperationKind.SAMPLING,
        server_id="srv",
        fqn=None,
        consumer=ME,
        definition_hash=None,
    )
    assert result.rule == sampling


def test_put_replaces_same_key_and_same_id() -> None:
    """One rule per key (DM §2.2): a new rule for the same key replaces the old one."""
    first = rule(consumer=ME)
    index = RuleIndex([first])
    second = rule("deny", consumer=ME, lifetime="session")
    assert index.put(second) == first
    assert index.rules() == [second]
    assert evaluate(index).effect == "deny"
    moved = second.model_copy(
        update={"target": ConsentTarget.server("srv"), "definition_hash": None}
    )
    assert index.put(moved) is None
    assert index.rules() == [moved]
    assert index.get(moved.id) == moved
    assert index.find(second.key) is None
    assert index.find(moved.key) == moved


def test_remove_and_listing() -> None:
    """REQ-CNS-013: rules are listed newest first; session rules on request."""
    persistent = rule(consumer=ME)
    session = rule(target=ConsentTarget.server("srv"), lifetime="session")
    index = RuleIndex([persistent, session])
    assert len(index) == 2
    assert index.rules() == [session, persistent]
    assert index.rules(include_session=False) == [persistent]
    assert index.remove(session.id) == session
    assert index.remove(session.id) is None
    assert index.rules() == [persistent]
    assert evaluate(index).rule == persistent
    index.clear()
    assert len(index) == 0
