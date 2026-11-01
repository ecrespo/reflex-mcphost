"""``Host.toolset()`` against the fixture Servers, both eras (F5-01; API §4.1, §4.12)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn
from tests.integration.toolset.rig import (
    HELPER,
    Setup,
    add_tool,
    allow_helper,
    era_server,
    host_for,
    records,
    setup,
)

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures


async def test_listing_reflects_the_live_catalog(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-TLS-001, REQ-TLS-002, REQ-TLS-004: the listing holds `<id>__echo` with the
    Server's input schema; after a tool is added at runtime (catalog.changed) the next
    list() of the same Toolset contains it and its alias resolves back."""
    async with host_for(setup, era_server) as (host, server_id):
        toolset = host.toolset(HELPER)
        specs = {s.name: s for s in toolset.list()}
        echo = specs[f"{server_id}__echo"]
        assert echo.fqn == Fqn(server_id=server_id, name="echo")
        assert echo.description.startswith(f"[{server_id}] ")
        assert echo.input_schema == host.catalog.get_tool(echo.fqn).input_schema
        assert f"{server_id}__fresh_tool" not in specs
        await add_tool(host, server_id, "fresh_tool")
        names = {s.name for s in toolset.list()}
        assert f"{server_id}__fresh_tool" in names
        assert toolset.resolve(f"{server_id}__fresh_tool") == Fqn(
            server_id=server_id, name="fresh_tool"
        )


async def test_call_is_consented_and_audited_per_consumer(
    setup: Setup, era_server: tuple[str, str]
) -> None:
    """REQ-TLS-001, REQ-CNS-015, REQ-CNS-010: a Toolset for agent:helper with an allow
    rule gets the echoed text and exactly one audit record attributed to agent:helper; a
    Toolset for agent:other (no rule, headless) is denied as a tool error, not raised."""
    async with host_for(setup, era_server) as (host, server_id):
        await allow_helper(host, server_id)
        alias = f"{server_id}__echo"
        outcome = await host.toolset(HELPER).call(alias, {"text": "héllo"})
        assert (outcome.is_error, outcome.text) == (False, "héllo")
        assert outcome.result.consumer == HELPER
        (record,) = await records(host, consumer=HELPER)
        assert (record.invocation_id, record.outcome) == (
            outcome.result.id,
            InvocationOutcome.success,
        )
        other = Consumer.agent("other")
        denied = await host.toolset(other).call(alias, {"text": "x"})
        assert denied.is_error
        assert denied.result.outcome is InvocationOutcome.denied
        assert denied.text.startswith("Permission denied by the host: ")
        (record,) = await records(host, consumer=other)
        assert record.outcome is InvocationOutcome.denied


async def test_toolset_after_close_raises(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-TLS-001, API §4.12: a Toolset outliving its Host raises HostClosedError on call."""
    from reflex_mcphost.core.errors import HostClosedError

    async with host_for(setup, era_server) as (host, server_id):
        toolset = host.toolset(HELPER)
    with pytest.raises(HostClosedError):
        await toolset.call(f"{server_id}__echo", {"text": "x"})
