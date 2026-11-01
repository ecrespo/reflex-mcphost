"""``ui.install``: the Host lifespan task, ``HostRuntime`` and guard wiring (F6-01; DD-011).

A fake Host records which task runs each lifecycle step; Reflex's own lifespan context
(``App._run_lifespan_tasks``) is driven without a server.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest
import reflex as rx

from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.ui.install import GuardTransformer, HostRuntime, install
from reflex_mcphost.ui.prompter import ReflexPrompter
from tests.unit.ui.rig import lifespan, new_app

pytestmark = pytest.mark.anyio
install_mod = importlib.import_module("reflex_mcphost.ui.install")  # `install` is shadowed
ORIGIN = "http://127.0.0.1:8123"
REAL_STATE_MANAGER_MODE = install_mod._state_manager_mode  # before conftest's autouse stub
MEMORY_SETTING = "state_manager_mode=rx.constants.StateManagerMode.MEMORY"


class FakeHost:
    """Records ``(step, task)`` for set_prompter / start / aclose."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.steps: list[tuple[str, object]] = []
        self.prompter: object = None
        self.mcp_apps: bool | None = None  # F7-03: install() may enable MCP Apps

    def set_mcp_apps(self, enabled: bool) -> None:
        self.mcp_apps = enabled

    def set_prompter(self, prompter: object) -> None:
        self.steps.append(("set_prompter", asyncio.current_task()))
        self.prompter = prompter

    async def __aenter__(self) -> FakeHost:
        self.steps.append(("start", asyncio.current_task()))
        if self.fail:
            raise MCPHostError("cannot open the Host database")
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.steps.append(("aclose", asyncio.current_task()))


@pytest.fixture(autouse=True)
def _loopback_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_mod, "_reflex_settings", lambda: (ORIGIN, (ORIGIN,)))
    monkeypatch.delenv("REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE", raising=False)


def _factory(host: FakeHost) -> Any:
    return lambda: host


async def test_start_and_aclose_run_in_one_task_after_the_prompter_is_set() -> None:
    """REQ-UI-007, REQ-CON-009: the lifespan task binds the process-wide ReflexPrompter,
    then enters ``async with host`` before serving and exits it at shutdown — start and
    aclose in the same task (anyio cancel-scope rule, DD-011)."""
    host = FakeHost()
    app = new_app()
    install(app, _factory(host))
    async with lifespan(app):
        assert [s for s, _ in host.steps] == ["set_prompter", "start"]
        assert HostRuntime.host is host
    assert [s for s, _ in host.steps] == ["set_prompter", "start", "aclose"]
    assert host.steps[1][1] is host.steps[2][1]
    assert isinstance(host.prompter, ReflexPrompter)


async def test_host_runtime_raises_before_start_up_and_after_shutdown() -> None:
    """REQ-UI-007, DD-011: ``HostRuntime.host`` is only available while the lifespan runs;
    ``phase`` names the banner state."""
    host = FakeHost()
    app = new_app()
    install(app, _factory(host))
    with pytest.raises(RuntimeError):
        _ = HostRuntime.host
    async with lifespan(app):
        assert HostRuntime.phase == "running"
    assert HostRuntime.phase == "stopped"
    with pytest.raises(RuntimeError):
        _ = HostRuntime.host


async def test_start_failure_propagates_out_of_the_lifespan() -> None:
    """REQ-UI-007, DD-011 consequences: a Host.start() failure leaves the lifespan start-up
    (so the backend exits non-zero) and no Host is published."""
    app = new_app()
    install(app, _factory(FakeHost(fail=True)))
    with pytest.raises(MCPHostError):
        async with lifespan(app):
            pytest.fail("served although Host.start() failed")
    with pytest.raises(RuntimeError):
        _ = HostRuntime.host


def test_install_is_idempotent_per_app_and_refuses_another_factory() -> None:
    """REQ-UI-007, DD-011: one lifespan task and one guard per ``rx.App``; a second call
    with a different factory raises ``RuntimeError``."""
    host = FakeHost()
    factory = _factory(host)
    app = new_app()
    install(app, factory)
    install(app, factory)
    ours = [t for t in app.get_lifespan_tasks() if t.__name__ == "reflex_mcphost_host"]
    assert len(ours) == 1
    assert isinstance(app.api_transformer, GuardTransformer)
    with pytest.raises(RuntimeError, match="different host_factory"):
        install(app, _factory(FakeHost()))


def test_guard_is_appended_after_existing_transformers() -> None:
    """REQ-NFR-011: the embedding app's transformers are kept and the guard is applied
    last, i.e. outermost (it also covers anything those transformers mount)."""

    def mine(inner: Any) -> Any:
        return inner

    single = new_app(api_transformer=mine)
    install(single, _factory(FakeHost()))
    first, guard = single.api_transformer
    assert first is mine
    assert isinstance(guard, GuardTransformer)

    several = new_app(api_transformer=[mine, mine])
    install(several, _factory(FakeHost()))
    assert list(several.api_transformer)[:2] == [mine, mine]
    assert isinstance(list(several.api_transformer)[2], GuardTransformer)


async def test_second_concurrent_lifespan_is_refused() -> None:
    """REQ-UI-007, DD-011: one Host per backend process — a second app's lifespan cannot
    start while another Host is published."""
    first, second = new_app(), new_app()
    install(first, _factory(FakeHost()))
    other = FakeHost()
    install(second, _factory(other))
    async with lifespan(first):
        with pytest.raises(RuntimeError, match="already"):
            async with lifespan(second):
                pass
    assert other.steps == []


# ------------------------------------------------------------------ state manager (F8-12, D4)


@pytest.mark.parametrize("mode", ["disk", "redis"])
async def test_a_state_manager_other_than_memory_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """REQ-UI-007 (API §8, DD-016, delta D4): with Reflex's ``DISK`` (or a ``REDIS``) state
    manager the lifespan fails at start-up with a ``RuntimeError`` naming the setting to
    use; no Host is built and nothing is published (fail closed)."""
    monkeypatch.setattr(install_mod, "_state_manager_mode", lambda: mode)
    built: list[FakeHost] = []

    def factory() -> FakeHost:
        built.append(FakeHost())
        return built[-1]

    app = new_app()
    install(app, factory)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match=MEMORY_SETTING.replace(".", r"\.")) as raised:
        async with lifespan(app):
            pytest.fail("served with tab state outside memory")
    assert repr(mode) in str(raised.value)
    assert built == []
    assert HostRuntime.phase == "stopped"


async def test_the_memory_state_manager_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-UI-007 (API §8, delta D4): ``MEMORY`` passes the start-up check."""
    monkeypatch.setattr(install_mod, "_state_manager_mode", lambda: "memory")
    host = FakeHost()
    app = new_app()
    install(app, _factory(host))
    async with lifespan(app):
        assert HostRuntime.host is host


class _Config:
    def __init__(self, mode: object, explicit: bool) -> None:
        self.state_manager_mode = mode
        self._non_default_attributes = {"state_manager_mode"} if explicit else set()


@pytest.mark.parametrize(
    ("mode", "explicit", "redis_url", "expected"),
    [
        (rx.constants.StateManagerMode.DISK, False, None, "disk"),  # Reflex's default
        (rx.constants.StateManagerMode.MEMORY, True, None, "memory"),
        (rx.constants.StateManagerMode.REDIS, True, None, "redis"),
        (rx.constants.StateManagerMode.DISK, False, "redis://127.0.0.1", "redis"),  # inferred
        (rx.constants.StateManagerMode.MEMORY, True, "redis://127.0.0.1", "memory"),
    ],
)
def test_the_mode_is_the_one_reflex_will_create(
    monkeypatch: pytest.MonkeyPatch,
    mode: object,
    explicit: bool,
    redis_url: str | None,
    expected: str,
) -> None:
    """REQ-UI-007 (delta D4): the checked mode follows ``StateManager.create``: an unset mode
    with a Redis URL configured becomes ``redis``; an explicit mode is used as set."""
    import reflex.config
    from reflex.utils import prerequisites

    monkeypatch.setattr(reflex.config, "get_config", lambda: _Config(mode, explicit))
    monkeypatch.setattr(prerequisites, "parse_redis_url", lambda: redis_url)
    assert REAL_STATE_MANAGER_MODE() == expected
