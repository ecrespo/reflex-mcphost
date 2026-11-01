"""The Host lifespan task with a real Host (F6-01; DD-011, REQ-CON-009).

The modern stdio fixture is an own Server of a real Host built by ``install()``'s factory;
Reflex's lifespan context is driven without a server.
"""

from __future__ import annotations

import importlib

import pytest

from reflex_mcphost.ui.install import HostRuntime, install
from reflex_mcphost.ui.prompter import ReflexPrompter
from tests.integration.test_host import MODERN_TABLE, TIMEOUT, Setup, _pid, setup
from tests.process import pid_alive
from tests.unit.ui.rig import lifespan, new_app

pytestmark = pytest.mark.anyio
install_mod = importlib.import_module("reflex_mcphost.ui.install")  # `install` is shadowed
__all__ = ["setup"]  # re-exported fixture
ORIGIN = "http://127.0.0.1:8124"


async def test_shutdown_stops_the_servers_of_the_lifespan_host(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-CON-009, REQ-UI-007: the Host built by the factory runs while Reflex serves
    (with the ReflexPrompter bound) and, once the lifespan context exits, its Server's
    process (the fixture's ``pid`` tool) is gone."""
    monkeypatch.setattr(install_mod, "_reflex_settings", lambda: (ORIGIN, (ORIGIN,)))
    monkeypatch.delenv("REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE", raising=False)
    config = setup.config({"own": MODERN_TABLE})
    app = new_app()
    install(app, lambda: setup.host(config))
    async with lifespan(app):
        host = HostRuntime.host
        assert isinstance(host.prompter, ReflexPrompter)
        await host.connections.wait_ready("own", timeout=TIMEOUT)
        pid = await _pid(host, "own")
        assert pid_alive(pid)
    assert not pid_alive(pid)
    with pytest.raises(RuntimeError):
        _ = HostRuntime.host
