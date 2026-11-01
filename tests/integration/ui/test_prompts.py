"""Consent and trust prompts end to end through the UI layer (F6-03; Flow D, both eras).

A real Host with the real ``ReflexPrompter`` over a fresh ``PromptRegistry`` (installed as
the process-wide one for the state handlers); two ``HostState`` instances stand in for two
tabs, each fed by its own watcher (``watch_host``, commit = plain assignment in place of
``async with self``); prompts are answered through the HostState handlers — no browser.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.errors import ServerUnavailableError
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.prompter import PromptRegistry, ReflexPrompter
from reflex_mcphost.ui.state import HostState, WatcherRegistry, watch_host
from tests.conftest import MODERN
from tests.integration.test_invoker import records, running_host
from tests.integration.toolset.rig import HELPER, TIMEOUT, Setup, era_server, setup

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures
HUMAN = Consumer.human()
SEEDED = "trust-env-secret-0201"


class Runtime:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.phase = "running"


class Tab:
    """One tab: a HostState fed by its own watcher."""

    def __init__(self) -> None:
        self.state = HostState(_reflex_internal_init=True)
        self._changed = anyio.Event()

    async def commit(self, fields: dict[str, Any]) -> None:
        for name, value in fields.items():
            setattr(self.state, name, value)
        self._changed.set()
        self._changed = anyio.Event()

    async def until(self, predicate: Callable[[HostState], bool]) -> None:
        with anyio.fail_after(TIMEOUT):
            while not predicate(self.state):
                await self._changed.wait()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[PromptRegistry]:
    registry = PromptRegistry()
    monkeypatch.setattr(prompter_mod, "PROMPTS", registry)
    yield registry
    assert registry.pending() == []


@asynccontextmanager
async def two_tabs(host: Host, prompts: PromptRegistry) -> AsyncIterator[tuple[Tab, Tab]]:
    tabs = (Tab(), Tab())
    watchers, runtime = WatcherRegistry(), Runtime(host)
    async with anyio.create_task_group() as tg:
        for i, tab in enumerate(tabs):
            tg.start_soon(
                lambda tab=tab, i=i: watch_host(
                    f"tab-{i:06d}", tab.commit, runtime=runtime, watchers=watchers, prompts=prompts
                )
            )
        for tab in tabs:
            await tab.until(lambda s: bool(s.servers))
        yield tabs
        tg.cancel_scope.cancel()


async def _both(tabs: tuple[Tab, Tab], predicate: Callable[[HostState], bool]) -> None:
    for tab in tabs:
        await tab.until(predicate)


def _has_card(state: HostState) -> bool:
    return len(state.cards) == 1


def _no_card(state: HostState) -> bool:
    return state.cards == []


@asynccontextmanager
async def ui_host(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry, **kwargs: Any
) -> AsyncIterator[tuple[Host, str]]:
    server_id, table = era_server
    prompter = ReflexPrompter(prompts)
    async with running_host(
        setup, {server_id: table}, prompter=prompter, allow=False, **kwargs
    ) as host:
        yield host, server_id


async def test_allow_once_runs_the_call_and_clears_both_tabs(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-UI-005, REQ-CNS-002, REQ-CNS-004 (both eras): a ``human:ui`` call with no rule
    shows one card in both tabs naming ``human:ui``; ``allow_once`` in one tab runs the
    tool, the card leaves both tabs and exactly one audit record is written."""
    async with ui_host(setup, era_server, prompts) as (host, server_id):
        fqn = f"{server_id}::echo"
        async with two_tabs(host, prompts) as tabs, anyio.create_task_group() as tg:
            results: list[Any] = []

            async def call() -> None:
                results.append(await host.invoker.call(fqn, {"text": "hi"}, consumer=HUMAN))

            tg.start_soon(call)
            await _both(tabs, _has_card)
            card = tabs[0].state.cards[0]
            assert tabs[1].state.cards[0]["id"] == card["id"]
            assert (card["asking"], card["fqn"], card["server_id"]) == ("human:ui", fqn, server_id)
            HostState.answer_consent.fn(tabs[0].state, card["id"], "allow_once", "tool", "once")
            await _both(tabs, _no_card)
        assert results[0].ok, results[0].error
        assert results[0].outcome is InvocationOutcome.success
        assert len(await records(host, fqn=Fqn.parse(fqn))) == 1
        assert host.consent.rules() == []  # allow_once stores nothing


async def test_allow_always_stores_a_persistent_rule(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-CNS-006 (answers from the UI): ``allow_always`` with the tool target persists a
    tool rule for ``human:ui``; the next call is not asked."""
    async with ui_host(setup, era_server, prompts) as (host, server_id):
        fqn = f"{server_id}::echo"
        async with two_tabs(host, prompts) as tabs, anyio.create_task_group() as tg:
            tg.start_soon(lambda: host.invoker.call(fqn, {"text": "a"}, consumer=HUMAN))
            await _both(tabs, _has_card)
            card = tabs[1].state.cards[0]
            HostState.answer_consent.fn(tabs[1].state, card["id"], "allow_always", "tool", "once")
            await _both(tabs, _no_card)
        [rule] = host.consent.rules(include_session=False)
        assert (rule.effect, rule.lifetime, rule.target.kind) == ("allow", "always", "tool")
        assert rule.consumer == HUMAN
        again = await host.invoker.call(fqn, {"text": "b"}, consumer=HUMAN)
        assert again.ok
        assert prompts.pending() == []


async def test_unanswered_card_times_out_denied(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-CNS-009: with ``consent_timeout_s = 1`` an unanswered card disappears and the
    call ends ``denied``."""
    lines = ["consent_timeout_s = 1"]
    async with ui_host(setup, era_server, prompts, host_lines=lines) as (host, server_id):
        async with two_tabs(host, prompts) as tabs, anyio.create_task_group() as tg:
            results: list[Any] = []

            async def call() -> None:
                fqn = f"{server_id}::echo"
                results.append(await host.invoker.call(fqn, {"text": "x"}, consumer=HUMAN))

            tg.start_soon(call)
            await _both(tabs, _has_card)
            await _both(tabs, _no_card)
        assert results[0].outcome is InvocationOutcome.denied
        assert results[0].consent.source == "timeout"


async def test_toolset_call_card_names_the_agent(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-UI-005, REQ-CNS-002: a Toolset call for ``agent:helper`` shows a card naming
    ``agent:helper``; ``deny`` ends it denied."""
    async with ui_host(setup, era_server, prompts) as (host, server_id):
        toolset = host.toolset(HELPER)
        async with two_tabs(host, prompts) as tabs, anyio.create_task_group() as tg:
            outcomes: list[Any] = []

            async def call() -> None:
                outcomes.append(await toolset.call(f"{server_id}__echo", {"text": "x"}))

            tg.start_soon(call)
            await _both(tabs, _has_card)
            card = tabs[0].state.cards[0]
            assert card["asking"] == "agent:helper"
            HostState.answer_consent.fn(tabs[0].state, card["id"], "deny", "tool", "once")
            await _both(tabs, _no_card)
        assert outcomes[0].is_error
        [record] = await records(host, operation=OperationKind.TOOL_CALL)
        assert record.consumer == HELPER
        assert record.outcome is InvocationOutcome.denied


@pytest.fixture(params=["modern", "legacy"])
def project_command(request: pytest.FixtureRequest) -> list[str]:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return [params.command, *params.args]
    return [sys.executable, str(MODERN)]


@asynccontextmanager
async def project_host(
    setup: Setup, command: list[str], prompts: PromptRegistry
) -> AsyncIterator[Host]:
    setup.env["SEEDED_VALUE"] = SEEDED
    entry = {
        "command": command[0],
        "args": command[1:],
        "env": {"API_KEY": "${SEEDED_VALUE}", "NODE_OPTIONS": "--no-warnings"},
    }
    setup.files.install_at(
        setup.files.cwd / ".mcp.json", text=json.dumps({"mcpServers": {"proj": entry}})
    )
    host = setup.host(setup.config({}), prompter=ReflexPrompter(prompts))
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            yield host


@pytest.mark.parametrize("trusted", [True, False])
async def test_trust_card_shows_names_and_decides_the_connection(
    setup: Setup, project_command: list[str], prompts: PromptRegistry, trusted: bool
) -> None:
    """REQ-REG-011 (trust UI), REQ-REG-007 (both eras): a project ``.mcp.json`` stdio
    Server raises a trust card with env names only (the seeded value is in no view model)
    and the risky name flagged; Trust → ``ready``, Don't trust → ``failed`` with
    ``untrusted_project_server``."""
    async with (
        project_host(setup, project_command, prompts) as host,
        two_tabs(host, prompts) as tabs,
    ):
        await _both(tabs, _has_card)
        card = tabs[0].state.cards[0]
        assert card["kind"] == "trust"
        assert card["server_id"] == "proj"
        assert card["env"] == [
            {"name": "API_KEY", "risky": False},
            {"name": "NODE_OPTIONS", "risky": True},
        ]
        for tab in tabs:
            assert SEEDED not in json.dumps([tab.state.cards, tab.state.servers, tab.state.tools])
        HostState.answer_trust.fn(tabs[1].state, card["id"], trusted)
        await _both(tabs, _no_card)
        if trusted:
            await host.connections.wait_ready("proj", timeout=TIMEOUT)
        else:
            with pytest.raises(ServerUnavailableError):
                await host.connections.wait_ready("proj", timeout=TIMEOUT)
            assert host.connections.status("proj").reason == "untrusted_project_server"
            await tabs[0].until(
                lambda s: any(
                    r["id"] == "proj" and r["state"] == ServerState.FAILED.value for r in s.servers
                )
            )


async def test_disconnecting_while_asked_removes_the_card(
    setup: Setup, prompts: PromptRegistry
) -> None:
    """REQ-REG-011, API §4.10 (trust not time-bounded, delta D5): a pending trust card
    stays until the Server is disconnected, which cancels the wait and removes the card."""
    async with project_host(setup, [sys.executable, str(MODERN)], prompts) as host:
        async with two_tabs(host, prompts) as tabs:
            await _both(tabs, _has_card)
            await host.connections.disconnect("proj")
            await _both(tabs, _no_card)
        assert host.connections.status("proj").state is ServerState.DISCONNECTED
