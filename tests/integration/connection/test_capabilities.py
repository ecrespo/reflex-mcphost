"""Client capability declaration per Server and rebuilds (F4-05; DD-003, API §4.1)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.events import ServerStateChanged
from reflex_mcphost.core.host import Host
from tests.integration.handlers.helpers import (
    FakeProvider,
    HandlerPrompter,
    caps_of,
    handler_host,
    legacy_stdio_table,
    modern_stdio_table,
    recording,
)
from tests.integration.test_host import Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = pytest.mark.anyio
__all__ = ["setup"]
ROOTS = '\nroots = ["file:///data"]'


@pytest.fixture(params=["modern", "legacy"])
def era_table(request: pytest.FixtureRequest) -> str:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        return legacy_stdio_table(request.getfixturevalue("legacy_stdio"))
    return modern_stdio_table()


async def _caps(host: Host) -> set[str]:
    result = await host.invoker.call("srv::client_caps", {}, consumer=BOT)
    assert result.ok, result.error
    return caps_of(result.text)


@pytest.mark.parametrize(
    ("prompter", "provider", "roots", "expected"),
    [
        (None, None, False, set()),
        (HandlerPrompter(supports_elicitation=False), None, False, set()),
        (HandlerPrompter(), None, False, {"elicitation"}),
        (HandlerPrompter(), FakeProvider(), False, {"elicitation", "sampling"}),
        (HandlerPrompter(), FakeProvider(), True, {"elicitation", "sampling", "roots"}),
        (None, None, True, {"roots"}),
    ],
    ids=["nothing", "non-eliciting", "eliciting", "+provider", "+roots", "roots-only"],
)
async def test_declares_exactly_the_enabled_set(
    setup: Setup,
    era_table: str,
    prompter: object,
    provider: object,
    roots: bool,
    expected: set[str],
) -> None:
    """REQ-CON-003, REQ-HSH-006, REQ-HSH-014 (DD-003): the Server sees exactly the enabled
    capabilities — elicitation iff the prompter supports it, sampling iff a provider is set,
    roots iff roots are configured — on both eras."""
    table = era_table + (ROOTS if roots else "")
    async with handler_host(setup, {"srv": table}, prompter=prompter, provider=provider) as host:
        assert await _caps(host) == expected


async def test_modern_set_prompter_applies_on_next_request(setup: Setup) -> None:
    """REQ-CON-003, REQ-HSH-014 (API §4.1): on a modern Server set_prompter(None) takes
    effect on the next request (no elicitation declared) with no reconnect and no state
    event; setting an eliciting prompter again declares it again."""
    async with handler_host(
        setup, {"srv": modern_stdio_table()}, prompter=HandlerPrompter()
    ) as host:
        assert await _caps(host) == {"elicitation"}
        async with recording(host) as events:
            host.set_prompter(None)
            assert await _caps(host) == set()
            host.set_prompter(HandlerPrompter())
            assert await _caps(host) == {"elicitation"}
        assert host.connections.status("srv").state is ServerState.READY
    assert [e for e in events if isinstance(e, ServerStateChanged)] == []


@pytest.mark.interop
async def test_legacy_keeps_capabilities_until_reconnect(
    setup: Setup, legacy_stdio: object
) -> None:
    """REQ-CON-003 (API §4.1, DD-003): a legacy Server keeps the capabilities of its
    initialize until reconnect(); then it sees the new set."""
    table = legacy_stdio_table(legacy_stdio)  # type: ignore[arg-type]
    async with handler_host(setup, {"srv": table}, prompter=HandlerPrompter()) as host:
        assert await _caps(host) == {"elicitation"}
        host.set_prompter(None)
        assert await _caps(host) == {"elicitation"}
        await host.connections.reconnect("srv")
        await host.connections.wait_ready("srv", timeout=10)
        assert await _caps(host) == set()
