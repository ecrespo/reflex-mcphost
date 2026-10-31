"""``wait_ready`` resolves only once the Catalog is loaded (API §4.1, §4.5; Flow B).

The fixture Servers answer ``tools/list`` after ``--list-delay`` seconds, so a ``wait_ready``
that resolved on the bare ``ready`` transition would reliably see an empty catalog.
"""

from __future__ import annotations

import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.types import Consumer
from tests.conftest import MODERN
from tests.integration.test_host import PY, TIMEOUT, Setup, _toml_server, setup, started

pytestmark = pytest.mark.anyio
BOT = Consumer.agent("bot")
LIST_DELAY = ["--list-delay", "0.5"]
__all__ = ["setup"]  # re-exported fixture


@pytest.fixture(params=["modern", "legacy"])
def slow_list_table(request: pytest.FixtureRequest) -> str:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return _toml_server([params.command, *params.args, *LIST_DELAY])
    return _toml_server([PY, str(MODERN), *LIST_DELAY])


async def test_wait_ready_waits_for_the_initial_catalog_refresh(
    setup: Setup, slow_list_table: str
) -> None:
    """REQ-CON-001, REQ-CAT-001, REQ-INV-001 — API §4.5: once ``wait_ready`` returns, the
    initial full refresh of the current connection is done, so the catalog already holds
    the Server's tools and the very next ``invoker.call`` succeeds (API §4.1 usage)."""
    host = setup.host(setup.config({"a": slow_list_table}))
    async with started(host):
        status = await host.connections.wait_ready("a", timeout=TIMEOUT)
        assert status.state is ServerState.READY
        names = {t.name for t in host.catalog.tools("a") if not t.stale}
        assert "echo" in names
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("a"),
            consumer=BOT,
        )
        result = await host.invoker.call("a::echo", {"text": "hi"}, consumer=BOT)
        assert result.outcome is InvocationOutcome.success, result.error
        assert result.text == "hi"
