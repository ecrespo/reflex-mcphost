"""Roots against the modern fixture Server (F4-03; legacy ``list_changed`` delivery is in
``test_legacy_requests.py``). *Roots are deprecated in the pinned spec; compatibility.*"""

from __future__ import annotations

import json

import pytest

from reflex_mcphost.core.events import ServerStateChanged
from reflex_mcphost.core.invoker import InvocationOutcome
from tests.integration.handlers.helpers import (
    HandlerPrompter,
    handler_host,
    modern_stdio_table,
    modern_table,
    recording,
)
from tests.integration.test_host import Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["modern_table", "setup"]


async def test_configured_roots_are_served(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-020, REQ-HSH-022: a roots/list input request gets the Server's configured
    file:// roots."""
    table = modern_table + '\nroots = ["file:///home/me/work", "file:///srv/data"]'
    async with handler_host(setup, {"srv": table}, prompter=HandlerPrompter()) as host:
        result = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
    assert result.ok, result.error
    assert json.loads(result.text) == ["file:///home/me/work", "file:///srv/data"]


async def test_no_roots_by_default(setup: Setup) -> None:
    """REQ-HSH-020 (delta D8): with no configured roots none are exposed — the roots
    capability is not even declared, so the request is undeclared_input_request."""
    async with handler_host(
        setup, {"srv": modern_stdio_table()}, prompter=HandlerPrompter()
    ) as host:
        result = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
    assert result.error is not None
    assert result.error.code == "undeclared_input_request"


async def test_set_roots_applies_without_reconnect(setup: Setup) -> None:
    """REQ-HSH-021, REQ-HSH-022 (API §4.5 set_roots): set_roots persists the roots and a
    modern Server serves them on its next request — empty → non-empty rebuilds the Client in
    place (no state change); non-file:// roots raise ValueError."""
    async with handler_host(
        setup, {"srv": modern_stdio_table()}, prompter=HandlerPrompter()
    ) as host:
        async with recording(host) as events:
            await host.connections.set_roots("srv", ["file:///data/a"])
            first = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
            await host.connections.set_roots("srv", ["file:///data/b"])
            second = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
            with pytest.raises(ValueError, match="file://"):
                await host.connections.set_roots("srv", ["https://example.com"])
        states = [e for e in events if isinstance(e, ServerStateChanged)]
        assert host.registry.get("srv").roots == ("file:///data/b",)
    assert json.loads(first.text) == ["file:///data/a"]
    assert json.loads(second.text) == ["file:///data/b"]
    assert states == []
