"""Host-side handler era matrix (F4-05; REQ-HSH-045): every handler — elicitation,
sampling, roots, logging — on a modern and a legacy Server, stdio and Streamable HTTP, with
one Host configuration. Modern input requests arrive as MRTR ``inputRequests``; legacy ones
as server-initiated requests; both reach the same handlers."""

from __future__ import annotations

import json

import anyio
import pytest

from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.prompter import ElicitationResponse
from tests.integration.handlers.helpers import (
    FakeProvider,
    HandlerPrompter,
    handler_host,
    legacy_stdio_table,
    modern_stdio_table,
)
from tests.integration.test_host import TIMEOUT, Setup, setup
from tests.integration.test_invoker import BOT, http_table

pytestmark = pytest.mark.anyio
__all__ = ["setup"]
EXTRA = '\nroots = ["file:///matrix"]\nlog_level = "debug"'


@pytest.fixture(params=["modern-stdio", "modern-http", "legacy-stdio", "legacy-http"])
def cell(request: pytest.FixtureRequest) -> tuple[str, str]:
    era, transport = str(request.param).split("-")
    if era == "legacy":
        request.applymarker(pytest.mark.interop)
        if transport == "http":
            return era, http_table(request.getfixturevalue("legacy_http"))
        return era, legacy_stdio_table(request.getfixturevalue("legacy_stdio"))
    if transport == "http":
        return era, http_table(request.getfixturevalue("modern_http"))
    return era, modern_stdio_table("--logging")


async def test_every_handler_on_both_eras(setup: Setup, cell: tuple[str, str]) -> None:
    """REQ-HSH-045, REQ-HSH-010, REQ-HSH-003, REQ-HSH-020, REQ-HSH-030: elicitation,
    sampling, roots and logging work through the same handlers on both eras and both
    transports, and the era is the expected one."""
    era, table = cell
    prompter = HandlerPrompter([ElicitationResponse(action="accept", content={"name": "Ada"})])
    async with handler_host(
        setup, {"srv": table + EXTRA}, prompter=prompter, provider=FakeProvider("Hi!")
    ) as host:
        await host.consent.grant(
            effect="allow", operation=OperationKind.SAMPLING, target=ConsentTarget.server("srv")
        )
        assert host.connections.status("srv").era == era
        named = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
        sampled = await host.invoker.call("srv::sample", {}, consumer=BOT)
        roots = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
        logged = await host.invoker.call("srv::log", {"data": "matrix"}, consumer=BOT)
        assert logged.ok, logged.error
        with anyio.fail_after(TIMEOUT):
            while not [e for e in host.connections.logs("srv") if e.text == "matrix"]:  # noqa: ASYNC110 - bounded poll
                await anyio.sleep(0.02)
    assert (named.text, sampled.text) == ("hello Ada", "Hi!")
    assert json.loads(roots.text) == ["file:///matrix"]
