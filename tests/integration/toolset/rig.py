"""Shared fixtures for Toolset and adapter integration tests (Phase 5, both eras).

``era_server`` yields ``(server_id, toml_table)`` for the modern and the legacy stdio
fixture Server (ids ``modern`` / ``legacy``, so aliases read ``<id>__echo``). Helpers wait
on Host events, never on sleeps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.events import CatalogChanged
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.types import Consumer
from tests.integration.test_host import MODERN_TABLE, TIMEOUT, Setup, setup
from tests.integration.test_invoker import FakePrompter, records, running_host, stdio_table

__all__ = [
    "HELPER",
    "TIMEOUT",
    "FakePrompter",
    "Setup",
    "add_tool",
    "allow_helper",
    "era_server",
    "host_for",
    "records",
    "setup",
]

HELPER = Consumer.agent("helper")


@pytest.fixture(params=["modern", "legacy"])
def era_server(request: pytest.FixtureRequest) -> tuple[str, str]:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return "legacy", stdio_table(params)
    return "modern", MODERN_TABLE


@asynccontextmanager
async def host_for(
    setup: Setup, era_server: tuple[str, str], *, prompter: Any = None
) -> AsyncIterator[tuple[Host, str]]:
    """A started Host with the era's Server ready and no consent rule."""
    server_id, table = era_server
    async with running_host(setup, {server_id: table}, prompter=prompter, allow=False) as host:
        yield host, server_id


async def allow_helper(host: Host, server_id: str) -> None:
    """A tool-call allow rule for ``agent:helper`` only (REQ-CNS-015)."""
    await host.consent.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.server(server_id),
        consumer=HELPER,
    )


async def add_tool(host: Host, server_id: str, name: str) -> None:
    """Ask the fixture Server to register ``name`` and wait for ``catalog.changed``."""
    async with host.events.subscribe() as events:
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.tool(f"{server_id}::add_tool"),
            consumer=Consumer.agent("fixture"),
        )
        result = await host.invoker.call(
            f"{server_id}::add_tool", {"name": name}, consumer=Consumer.agent("fixture")
        )
        assert result.ok, result.error
        with anyio.fail_after(TIMEOUT):
            async for event in events:
                if (
                    isinstance(event, CatalogChanged)
                    and event.server_id == server_id
                    and name in {t.name for t in host.catalog.tools(server_id)}
                ):
                    break
