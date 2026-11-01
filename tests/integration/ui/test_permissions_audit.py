"""Permissions and Audit views against a real Host, modern stdio, headless prompter
(F6-07; REQ-UI-006). Handlers run on a ``Tab`` with the Host served by ``HostRuntime``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import actions, models
from reflex_mcphost.ui.state import HostState
from tests.integration.test_host import MODERN_TABLE, Setup, setup
from tests.integration.test_invoker import running_host
from tests.integration.ui.rig import Tab, watched
from tests.unit.ui.rig import published

pytestmark = pytest.mark.anyio
__all__ = ["setup"]  # re-exported fixture
HUMAN = Consumer.human()
BOT = Consumer.agent("bot")
SENTINEL = "audit-secret-sentinel-0607"


@asynccontextmanager
async def ui_host(setup: Setup) -> AsyncIterator[tuple[Host, Tab]]:
    async with running_host(setup, {"srv": MODERN_TABLE}, allow=False) as host:
        with published(host):
            yield host, Tab()


async def _call(host: Host, consumer: Consumer = HUMAN, text: str = "x") -> InvocationOutcome:
    result = await host.invoker.call("srv::echo", {"text": text}, consumer=consumer)
    return result.outcome


async def test_rules_edit_and_revoke_apply_without_restart(setup: Setup) -> None:
    """REQ-CNS-013, REQ-UI-006: a programmatic allow is a row; Edit allow → deny makes the
    next call denied; Revoke makes the next call denied (no rule, no prompter); an
    ``allow_session`` rule is listed with lifetime ``session``."""
    async with ui_host(setup) as (host, tab):
        await host.consent.grant(
            effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server("srv")
        )
        tab.sync(HostState.load_rules)
        [row] = tab.state.rules
        assert (row["effect"], row["lifetime"], row["consumer"]) == ("allow", "always", None)
        assert await _call(host) is InvocationOutcome.success
        await tab.call(HostState.update_rule, row["id"], "deny", "always")
        assert tab.state.rules[0]["effect"] == "deny"
        assert await _call(host) is InvocationOutcome.denied
        await tab.call(HostState.revoke_rule, row["id"])
        assert tab.state.rules == []
        assert await _call(host) is InvocationOutcome.denied  # no rule, headless: denied
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.tool("srv::echo"),
            consumer=HUMAN,
            lifetime="session",
        )
        tab.sync(HostState.load_rules)
        [session] = tab.state.rules
        assert (session["lifetime"], session["consumer"], session["target"]) == (
            "session",
            "human:ui",
            "tool srv::echo",
        )
        assert await _call(host) is InvocationOutcome.success


async def test_audit_filters_paging_and_refresh(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-INV-011, REQ-INV-009, REQ-UI-006: each filter narrows the rows; newest first;
    "Load more" follows the cursor; the view refreshes on ``invocation.finished``; a
    secret-like argument value appears in no row (only its hash)."""
    monkeypatch.setattr(models, "AUDIT_PAGE_SIZE", 2)
    async with ui_host(setup) as (host, tab):
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("srv"),
            consumer=HUMAN,
        )
        assert await _call(host, HUMAN, SENTINEL) is InvocationOutcome.success
        assert await _call(host, BOT) is InvocationOutcome.denied
        assert await _call(host, HUMAN, "third") is InvocationOutcome.success
        await tab.call(HostState.load_audit)
        assert len(tab.state.audit) == 2
        assert tab.state.audit[0]["timestamp"] >= tab.state.audit[1]["timestamp"]
        assert tab.state.audit[0]["consumer"] == "human:ui"  # the newest call
        assert tab.state.audit_cursor != ""
        await tab.call(HostState.load_more_audit)
        assert len(tab.state.audit) == 3
        assert tab.state.audit_cursor == ""
        assert SENTINEL not in json.dumps(tab.state.audit)
        assert all(r["args_hash"] for r in tab.state.audit)  # the hash is shown instead
        await tab.call(HostState.set_audit_filter, "consumer", "agent:bot")
        assert [(r["consumer"], r["outcome"]) for r in tab.state.audit] == [("agent:bot", "denied")]
        await tab.call(HostState.set_audit_filter, "consumer", "")
        await tab.call(HostState.set_audit_filter, "outcome", "success")
        assert {r["outcome"] for r in tab.state.audit} == {"success"}
        await tab.call(HostState.set_audit_filter, "outcome", models.ANY)
        await tab.call(HostState.set_audit_filter, "server_id", "other")
        assert tab.state.audit == []
        await tab.call(HostState.set_audit_filter, "server_id", "srv")
        await tab.call(HostState.set_audit_filter, "fqn", "srv::echo")
        await tab.call(HostState.set_audit_filter, "operation", "tool_call")
        await tab.call(HostState.set_audit_filter, "since", "2999-01-01T00:00:00")
        assert tab.state.audit == []
        await tab.call(HostState.set_audit_filter, "since", "")
        await tab.call(HostState.set_audit_filter, "until", "2000-01-01T00:00:00")
        assert tab.state.audit == []
        await tab.call(HostState.set_audit_filter, "until", "")
        assert len(tab.state.audit) == 2  # first page again

        async def on_finished() -> None:
            await actions.refresh_audit(host, dict(tab.state.audit_filters), tab.commit)

        async with watched(host, tab, on_finished=on_finished):
            await tab.call(HostState.set_audit_filter, "consumer", "agent:late")
            assert tab.state.audit == []
            assert await _call(host, Consumer.agent("late")) is InvocationOutcome.denied
            await tab.until(lambda s: len(s.audit) == 1)
        assert tab.state.audit[0]["consumer"] == "agent:late"
