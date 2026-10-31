"""Consent value types (F3-01, API §4.7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reflex_mcphost.core.consent.models import (
    ArgumentPreview,
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentRule,
    ConsentTarget,
)
from reflex_mcphost.core.consent.outcome import ConsentOutcome, OperationKind
from reflex_mcphost.core.types import Consumer, Fqn
from tests.unit.core.consent.factories import rule, sha, tool_entry, tool_request


def test_target_constructors() -> None:
    """REQ-CNS-003: the three target kinds and their specificity tool > server > global."""
    tool = ConsentTarget.tool("srv::a::b")
    assert tool.kind == "tool"
    assert tool.fqn == Fqn(server_id="srv", name="a::b")
    assert tool.server_id == "srv"
    server = ConsentTarget.server("srv")
    assert (server.kind, server.server_id, server.fqn) == ("server", "srv", None)
    glob = ConsentTarget.global_()
    assert (glob.kind, glob.server_id, glob.fqn) == ("global", None, None)
    assert tool.specificity > server.specificity > glob.specificity


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "tool"},
        {"kind": "tool", "server_id": "other", "fqn": Fqn(server_id="srv", name="x")},
        {"kind": "server"},
        {"kind": "server", "server_id": "srv", "fqn": Fqn(server_id="srv", name="x")},
        {"kind": "global", "server_id": "srv"},
    ],
)
def test_target_rejects_inconsistent_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConsentTarget(**fields)  # type: ignore[arg-type]


def test_tool_target_invalid_for_sampling() -> None:
    """REQ-CNS-001: sampling consent is scoped to a Server, never to a tool."""
    with pytest.raises(ValueError, match="sampling"):
        rule(operation=OperationKind.SAMPLING, target=ConsentTarget.tool("srv::echo"))
    assert rule(operation=OperationKind.SAMPLING, target=ConsentTarget.server("srv")).target


def test_rule_definition_hash_only_for_tool_targets() -> None:
    """REQ-CNS-007: tool rules carry the definition hash at grant time; others none."""
    with pytest.raises(ValidationError):
        ConsentRule(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.tool("srv::echo"),
        )
    with pytest.raises(ValidationError):
        ConsentRule(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("srv"),
            definition_hash=sha("x"),
        )


def test_rule_ids_frozen_and_ranked() -> None:
    """REQ-CNS-003, REQ-CNS-015: rank = (target specificity, consumer specificity)."""
    r = rule(consumer=Consumer.agent("bot"))
    assert r.id.startswith("rule_")
    assert len(r.id) == 31
    assert r.rank == (3, 2)
    assert rule(target=ConsentTarget.global_()).rank == (1, 1)
    with pytest.raises(ValidationError):
        r.effect = "deny"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ConsentRule.model_validate({**r.model_dump(), "id": "rule_bad"})


def test_rule_key_identifies_target_and_consumer() -> None:
    a = rule(consumer=Consumer.agent("bot"))
    b = rule("deny", consumer=Consumer.agent("bot"))
    c = rule(consumer=None)
    assert a.key == b.key != c.key
    assert c.key[-2:] == ("", "")


def test_request_validation() -> None:
    """REQ-HSH-047: an unattributed request asks the human Consumer."""
    request = tool_request()
    assert request.id.startswith("req_")
    assert request.fqn == Fqn.parse("srv::echo")
    with pytest.raises(ValidationError):
        ConsentRequest(
            operation=OperationKind.TOOL_CALL,
            consumer=Consumer.human(),
            server_id="srv",
            server_name="S",
        )
    with pytest.raises(ValidationError):
        ConsentRequest(
            operation=OperationKind.TOOL_CALL,
            consumer=Consumer.human(),
            server_id="other",
            server_name="S",
            tool=tool_entry(),
        )
    with pytest.raises(ValidationError):
        ConsentRequest(
            operation=OperationKind.SAMPLING,
            consumer=Consumer.human(),
            server_id="srv",
            server_name="S",
            tool=tool_entry(),
        )
    with pytest.raises(ValidationError):
        tool_request(consumer=Consumer.agent("bot"), attribution="unattributed")
    assert tool_request(consumer=Consumer.human(), attribution="unattributed").attribution


def test_answer_and_outcome_defaults() -> None:
    answer = ConsentAnswer(decision=ConsentDecision.DENY)
    assert (answer.target_kind, answer.deny_lifetime, answer.edited_sampling) == (
        "tool",
        "once",
        None,
    )
    outcome = ConsentOutcome(allowed=False, source="no_prompter")
    assert outcome.rule_id is None
    assert outcome.reason is None
    assert ArgumentPreview(body="{}").format == "json"
    assert ArgumentPreview(body="{}").redacted_fields == ()
