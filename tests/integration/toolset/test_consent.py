"""Toolset calls through consent, visibility and filters, both eras (F5-02; API §4.12)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.consent.models import ConsentDecision
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Fqn
from tests.integration.test_host import MODERN_TABLE
from tests.integration.toolset.rig import (
    HELPER,
    FakePrompter,
    Setup,
    allow_helper,
    era_server,
    host_for,
    records,
    setup,
)

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["era_server", "setup"]  # re-exported fixtures


async def test_headless_denial_is_a_tool_error(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-TLS-005, REQ-CNS-010: a headless Host without a rule → is_error with the
    denial text, no exception, exactly one audit record `denied`."""
    async with host_for(setup, era_server) as (host, server_id):
        outcome = await host.toolset(HELPER).call(f"{server_id}__echo", {"text": "x"})
        assert outcome.is_error
        assert outcome.text.startswith("Permission denied by the host: ")
        assert outcome.result.outcome is Out.denied
        (record,) = await records(host)
        assert (record.outcome, record.consumer) == (Out.denied, HELPER)


@pytest.mark.parametrize(
    ("decision", "outcome"),
    [(ConsentDecision.DENY, Out.denied), (ConsentDecision.ALLOW_ONCE, Out.success)],
)
async def test_prompter_decides(
    setup: Setup,
    era_server: tuple[str, str],
    decision: ConsentDecision,
    outcome: InvocationOutcome,
) -> None:
    """REQ-TLS-005, REQ-CNS-015: a prompter answering deny → the same denial tool error;
    allow → the tool result; the prompt names the Toolset's Consumer."""
    prompter = FakePrompter(decision=decision)
    async with host_for(setup, era_server, prompter=prompter) as (host, server_id):
        result = await host.toolset(HELPER).call(f"{server_id}__echo", {"text": "hi"})
        assert result.result.outcome is outcome
        assert result.is_error is (outcome is Out.denied)
        if outcome is Out.denied:
            assert result.text.startswith("Permission denied by the host: ")
        else:
            assert result.text == "hi"
        (asked,) = prompter.asked
        assert asked.consumer == HELPER
        (record,) = await records(host)
        assert record.outcome is outcome


async def test_invalid_arguments_never_prompt(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-TLS-005, REQ-INV-002: arguments failing the input schema → is_error, and the
    prompter is never asked."""
    prompter = FakePrompter()
    async with host_for(setup, era_server, prompter=prompter) as (host, server_id):
        outcome = await host.toolset(HELPER).call(f"{server_id}__echo", {"text": 5})
        assert outcome.is_error
        assert outcome.text.startswith("Tool call failed (invalid_arguments): ")
        assert prompter.asked == []


async def test_tool_gone_after_listing_raises_key_error(
    setup: Setup, era_server: tuple[str, str]
) -> None:
    """REQ-TLS-005, REQ-TLS-004: a tool whose Server stopped after list() is not callable:
    KeyError (the adapters turn it into a tool error), nothing reaches the Invoker."""
    async with host_for(setup, era_server) as (host, server_id):
        await allow_helper(host, server_id)
        toolset = host.toolset(HELPER)
        alias = f"{server_id}__echo"
        assert alias in {s.name for s in toolset.list()}
        await host.connections.disconnect(server_id)
        with pytest.raises(KeyError):
            await toolset.call(alias, {"text": "x"})
        assert await records(host) == []


async def test_app_only_tool_is_never_exposed(setup: Setup) -> None:
    """REQ-APP-008: the modern fixture's app-only tool (`_meta.ui.visibility: ["app"]`) is
    absent from every listing and its candidate alias raises KeyError on call; a tool
    with default visibility is listed."""
    async with host_for(setup, ("modern", MODERN_TABLE)) as (host, _):
        assert host.catalog.get_tool("modern::ui_app").visibility == ("app",)
        for toolset in (host.toolset(HELPER), host.toolset(HELPER, include=["modern::ui_*"])):
            names = {s.name for s in toolset.list()}
            assert "modern__ui_app" not in names
            assert "modern__ui_legacy" in names
            with pytest.raises(KeyError):
                await toolset.call("modern__ui_app", {})
        assert await records(host) == []


async def test_filters_on_a_real_host(setup: Setup) -> None:
    """REQ-TLS-003: include/exclude select tools of a real Host; excluded tools are not
    callable."""
    async with host_for(setup, ("modern", MODERN_TABLE)) as (host, _):
        toolset = host.toolset(HELPER, include=["modern::echo", "modern::slow"])
        assert [s.fqn for s in toolset.list()] == [
            Fqn.parse("modern::echo"),
            Fqn.parse("modern::slow"),
        ]
        excluded = host.toolset(HELPER, exclude=["modern::echo"])
        assert "modern__echo" not in {s.name for s in excluded.list()}
        with pytest.raises(KeyError):
            await excluded.call("modern__echo", {"text": "x"})
        assert {s.name for s in host.toolset(HELPER, include=["other"]).list()} == set()
