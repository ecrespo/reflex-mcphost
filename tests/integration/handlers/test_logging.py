"""Server logging against the modern fixture Server (F4-04; legacy ``setLevel`` is in
``test_legacy_requests.py``). *Logging is deprecated in the pinned spec; compatibility.*"""

from __future__ import annotations

import pytest

from reflex_mcphost.core.errors import CapabilityNotSupportedError, UnknownServerError
from reflex_mcphost.core.events import ServerLog, ServerStateChanged
from reflex_mcphost.core.host import Host
from tests.integration.handlers.helpers import handler_host, modern_stdio_table, recording
from tests.integration.test_host import Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = pytest.mark.anyio
__all__ = ["setup"]


def _server_lines(host: Host) -> list[str]:
    return [e.text for e in host.connections.logs("srv") if e.logger == "fixture"]


async def test_set_log_level_delivers_notifications(setup: Setup) -> None:
    """REQ-HSH-030, REQ-HSH-031 (modern): no level is sent by default, so `log` emits
    nothing; after set_log_level("debug") the next call's notification lands in logs() with
    its level and logger and as a server.log event — without a state change."""
    table = modern_stdio_table("--logging")
    async with handler_host(setup, {"srv": table}) as host:
        silent = await host.invoker.call("srv::log", {"data": "before"}, consumer=BOT)
        assert silent.ok, silent.error
        assert _server_lines(host) == []
        async with recording(host) as events:
            await host.connections.set_log_level("srv", "debug")
            loud = await host.invoker.call("srv::log", {"data": {"n": 1}}, consumer=BOT)
        assert loud.ok, loud.error
        (entry,) = [e for e in host.connections.logs("srv") if e.logger == "fixture"]
        assert (entry.level, entry.text) == ("info", '{"n": 1}')
    logs = [e for e in events if isinstance(e, ServerLog) and e.entry.logger == "fixture"]
    assert [e.entry for e in logs] == [entry]
    assert [e for e in events if isinstance(e, ServerStateChanged)] == []


async def test_definition_log_level_applies_at_connect(setup: Setup) -> None:
    """REQ-HSH-031: a definition log_level is sent from the first request (modern Client
    log_level)."""
    table = modern_stdio_table("--logging", log_level="info")
    async with handler_host(setup, {"srv": table}) as host:
        await host.invoker.call("srv::log", {"data": "hello"}, consumer=BOT)
        assert _server_lines(host) == ["hello"]


async def test_server_without_logging_is_not_supported(setup: Setup) -> None:
    """REQ-HSH-031 (API §4.5): a Server that did not declare logging →
    CapabilityNotSupportedError; an invalid level → ValueError; unknown ids →
    UnknownServerError."""
    async with handler_host(setup, {"srv": modern_stdio_table()}) as host:
        with pytest.raises(CapabilityNotSupportedError):
            await host.connections.set_log_level("srv", "debug")
        with pytest.raises(ValueError, match="log level"):
            await host.connections.set_log_level("srv", "loud")  # type: ignore[arg-type]
        with pytest.raises(UnknownServerError):
            await host.connections.set_log_level("ghost", "debug")
