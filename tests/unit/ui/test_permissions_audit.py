"""``permissions_table`` / ``audit_table`` and their actions with a fake Host (F6-07;
API §8, REQ-UI-006)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from reflex_mcphost.core.audit import AuditPage, AuditQuery, AuditRecord
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import actions, models
from reflex_mcphost.ui.components.audit_table import audit_table
from reflex_mcphost.ui.components.permissions_table import permissions_table
from reflex_mcphost.ui.state import HostState
from tests.unit.core.consent.factories import rule
from tests.unit.ui.rig import published

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_audit_query_parses_every_filter() -> None:
    """REQ-INV-011: time range, Server, FQN, Consumer, outcome and operation filters map
    to an ``AuditQuery``; empty / ``any`` values are unset; a bad value is reported."""
    query, problem = models.audit_query(
        {
            "since": "2026-11-01T00:00:00",
            "until": "2026-11-02T00:00:00+00:00",
            "server_id": " srv ",
            "fqn": "srv::echo",
            "consumer": "human:ui",
            "outcome": "denied",
            "operation": "any",
        },
        cursor="c1",
    )
    assert problem == ""
    assert query == AuditQuery(
        since=NOW,
        until=datetime(2026, 11, 2, tzinfo=UTC),
        server_id="srv",
        fqn=Fqn.parse("srv::echo"),
        consumer=Consumer.human(),
        outcome=InvocationOutcome.denied,
        cursor="c1",
    )
    assert models.audit_query({}) == (AuditQuery(), "")
    assert models.audit_query({"fqn": "no-separator"}) == (None, "invalid fqn filter")
    assert models.audit_query({"outcome": "great"}) == (None, "invalid outcome filter")
    assert models.audit_query({"since": "yesterday"}) == (None, "invalid since filter")


class FakeConsent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.stored = [rule("allow", target=ConsentTarget.server("srv"))]

    def rules(self) -> list[Any]:
        return list(self.stored)

    async def update(self, rule_id: str, *, effect: str, lifetime: str) -> None:
        self.calls.append(("update", (rule_id, effect, lifetime)))

    async def revoke(self, rule_id: str) -> None:
        self.calls.append(("revoke", rule_id))
        if rule_id == "missing":
            raise LookupError(rule_id)
        self.stored = [r for r in self.stored if r.id != rule_id]


def _record(i: int) -> AuditRecord:
    return AuditRecord(
        invocation_id=f"inv_{i}",
        operation=OperationKind.TOOL_CALL,
        timestamp=NOW,
        consumer=Consumer.human(),
        server_id="srv",
        fqn=Fqn.parse("srv::echo"),
        args_hash=f"sha256:{i:02d}",
        decision_source="rule",
        rule_id=None,
        duration_ms=1,
        outcome=InvocationOutcome.success,
        error_code=None,
        input_rounds=0,
        attribution="exact",
    )


class FakeAudit:
    def __init__(self) -> None:
        self.queries: list[AuditQuery] = []

    async def query(self, query: AuditQuery) -> AuditPage:
        self.queries.append(query)
        if query.cursor is None:
            return AuditPage(records=(_record(2), _record(1)), next_cursor="next")
        if query.cursor == "boom":
            raise MCPHostError("audit unavailable")
        return AuditPage(records=(_record(0),), next_cursor=None)


class FakeHost:
    def __init__(self) -> None:
        self.consent = FakeConsent()
        self.audit = FakeAudit()


async def test_rule_actions_call_update_and_revoke() -> None:
    """REQ-CNS-013, REQ-UI-006: Edit calls ``consent.update`` (effect, lifetime) and Revoke
    ``consent.revoke`` with the row's id; invalid choices and errors are notices."""
    host = FakeHost()
    rule_id = host.consent.stored[0].id
    assert [r["id"] for r in actions.rule_view(host)] == [rule_id]
    assert await actions.update_rule(host, rule_id, "deny", "session") == (
        f"Rule {rule_id}: deny, session"
    )
    assert "failed" in await actions.update_rule(host, rule_id, "maybe", "always")
    assert await actions.revoke_rule(host, rule_id) == f"Rule {rule_id}: revoked"
    assert "failed" in await actions.revoke_rule(host, "missing")
    assert host.consent.calls == [
        ("update", (rule_id, "deny", "session")),
        ("revoke", rule_id),
        ("revoke", "missing"),
    ]


async def test_state_permissions_and_audit_handlers() -> None:
    """REQ-UI-006, REQ-INV-011, REQ-INV-009: the views load rules and the newest audit
    page; a filter change re-queries the first page; "Load more" follows the cursor;
    rows carry the argument hash; failures are notices."""
    host = FakeHost()
    state = HostState(_reflex_internal_init=True)
    with published(host):
        HostState.load_rules.fn(state)
        rule_id = state.rules[0]["id"]
        await HostState.update_rule.fn(state, rule_id, "deny", "always")
        await HostState.revoke_rule.fn(state, rule_id)
        assert state.rules == []
        assert state.permissions_notice == f"Rule {rule_id}: revoked"
        await HostState.load_audit.fn(state)
        assert [r["id"] for r in state.audit] == ["inv_2", "inv_1"]
        assert state.audit[0]["args_hash"] == "sha256:02"
        assert state.audit_cursor == "next"
        await HostState.load_more_audit.fn(state)
        assert [r["id"] for r in state.audit] == ["inv_2", "inv_1", "inv_0"]
        assert state.audit_cursor == ""
        await HostState.set_audit_filter.fn(state, "server_id", "srv")
        await HostState.set_audit_filter.fn(state, "bogus", "x")
        assert host.audit.queries[-1] == AuditQuery(server_id="srv")
        assert len(host.audit.queries) == 3
        await HostState.set_audit_filter.fn(state, "fqn", "bad")
        assert state.audit_notice == "invalid fqn filter"
        await HostState.set_audit_filter.fn(state, "fqn", "")
        state.audit_cursor = "boom"
        await HostState.load_more_audit.fn(state)
        assert "failed" in state.audit_notice
    HostState.load_rules.fn(state)
    assert state.permissions_notice == "The Host is not running."
    state.rules = [{"id": f"rule_{i}", "target": "global"} for i in range(250)]
    assert (len(state.rules_shown), state.rules_more) == (200, 50)
    HostState.set_rule_filter.fn(state, "rule_249")
    assert [r["id"] for r in state.rules_shown] == ["rule_249"]


async def test_audit_refresh_runs_only_for_an_opened_view() -> None:
    """REQ-UI-006 (refreshed on ``invocation.finished``): the watcher hook re-queries the
    first page with the tab's filters, and nothing for a tab that never opened the view."""
    host, committed = FakeHost(), []

    async def commit(fields: dict[str, Any]) -> None:
        committed.append(fields)

    await actions.refresh_audit(host, None, commit)
    assert committed == []
    assert host.audit.queries == []
    await actions.refresh_audit(host, {"outcome": "success"}, commit)
    assert host.audit.queries == [AuditQuery(outcome=InvocationOutcome.success)]
    assert [r["id"] for r in committed[0]["audit"]] == ["inv_2", "inv_1"]


def test_tables_render_columns_and_actions() -> None:
    """REQ-UI-006: Permissions shows effect, operation, target, Consumer, lifetime,
    origin, suspended, created / updated with Edit and Revoke; Audit shows the REQ-INV-011
    filters, "Load more" and the argument hash — never arguments."""
    rules = str(permissions_table())
    for needle in ("permissions-table", "rule-effect", "rule-lifetime", "rule-revoke", "suspended"):
        assert needle in rules, needle
    audit = str(audit_table())
    for needle in ("audit-table", "Since", "Until", "Server", "Consumer", "Outcome", "Load more"):
        assert needle in audit, needle
    assert "args_hash" in audit
    assert '["arguments"]' not in audit
    for rendered in (rules, audit):
        assert "dangerouslySetInnerHTML" not in rendered
