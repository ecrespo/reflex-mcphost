"""Elicitation and sampling prompts end to end through the UI layer (F6-06; Flow D, both
eras).

A real Host with the real ``ReflexPrompter`` over a fresh ``PromptRegistry`` and a fake
``SamplingProvider``; one tab fed by its watcher; prompts are answered through the
``HostState`` handlers — no browser. Tool calls are allowed by a stored rule.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest

from reflex_mcphost.core.host import Host
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.prompter import PromptRegistry, ReflexPrompter
from reflex_mcphost.ui.state import HostState
from tests.integration.handlers.helpers import FakeProvider
from tests.integration.test_invoker import running_host
from tests.integration.toolset.rig import TIMEOUT, Setup, era_server, setup
from tests.integration.ui.rig import Tab, watched
from tests.unit.ui.rig import redirect_target

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures
HUMAN = Consumer.human()
URL = "https://auth.example.com/connect?x=1"


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[PromptRegistry]:
    registry = PromptRegistry()
    monkeypatch.setattr(prompter_mod, "PROMPTS", registry)
    yield registry
    assert registry.pending() == []


@asynccontextmanager
async def ui_host(
    setup: Setup,
    era_server: tuple[str, str],
    prompts: PromptRegistry,
    provider: FakeProvider | None = None,
    extra: str = "",
) -> AsyncIterator[tuple[Host, str, Tab]]:
    server_id, table = era_server
    async with (
        running_host(
            setup,
            {server_id: table + extra},
            prompter=ReflexPrompter(prompts),
            sampling_provider=provider,
        ) as host,
        watched(host, Tab(), prompts=prompts) as tab,
    ):
        yield host, server_id, tab


@asynccontextmanager
async def calling(
    host: Host, fqn: str, args: dict[str, Any] | None = None
) -> AsyncIterator[list[Any]]:
    """Run one ``human:ui`` call in the background; its result lands in the list."""
    results: list[Any] = []
    async with anyio.create_task_group() as tg:

        async def call() -> None:
            results.append(await host.invoker.call(fqn, args or {}, consumer=HUMAN))

        tg.start_soon(call)
        yield results
        with anyio.fail_after(TIMEOUT):
            while not results:
                await anyio.lowlevel.checkpoint()


def _card(tab: Tab, kind: str) -> dict[str, Any]:
    return next(c for c in tab.state.cards if c["kind"] == kind)


async def _until_card(tab: Tab, kind: str) -> dict[str, Any]:
    await tab.until(lambda s: any(c["kind"] == kind for c in s.cards))
    return _card(tab, kind)


def _form_tool(server_id: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """``(tool, invalid answer, valid answer)``: modern ``elicit_form`` (``name`` has
    ``minLength: 2``), legacy ``ask_name`` (``name`` required)."""
    if server_id == "modern":
        return "elicit_form", {"name": "A"}, {"name": "Ada", "age": "36", "subscribe": "on"}
    return "ask_name", {"name": ""}, {"name": "Ada"}


async def test_form_errors_stay_on_the_card_and_valid_content_is_sent(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-UI-005, REQ-HSH-010, REQ-HSH-013 (both eras): a form card shows the Server and
    message; an invalid submit keeps the same card with the field error and sends
    nothing; a valid submit reaches the Server and the tool result contains it."""
    async with ui_host(setup, era_server, prompts) as (host, server_id, tab):
        tool, invalid, valid = _form_tool(server_id)
        async with calling(host, f"{server_id}::{tool}") as results:
            card = await _until_card(tab, "elicitation_form")
            assert card["server_id"] == server_id
            assert card["message"] in ("Tell me about you", "What is your name?")
            assert "name" in [f["name"] for f in card["fields"]]
            tab.sync(HostState.submit_elicitation, card["id"], invalid)
            assert "name" in tab.state.elicitation_errors[card["id"]]
            assert [c["id"] for c in tab.state.cards] == [card["id"]]
            assert prompts.get(card["id"]) is not None
            assert results == []
            tab.sync(HostState.submit_elicitation, card["id"], valid)
        [result] = results
        assert result.ok, result.error
        if server_id == "modern":
            assert json.loads(result.text) == {
                "action": "accept",
                "content": {"name": "Ada", "age": 36, "subscribe": True},
            }
            counts = await host.invoker.call("modern::call_counts", {}, consumer=HUMAN)
            assert json.loads(counts.text)["elicit_form"] == 2  # first leg + the valid retry
        else:
            assert result.text == "hello Ada"
        await tab.until(lambda s: s.cards == [])


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_decline_and_cancel_reach_the_server(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry, action: str
) -> None:
    """REQ-HSH-011 (both eras): Decline / Cancel send that action, without content."""
    async with ui_host(setup, era_server, prompts) as (host, server_id, tab):
        tool, _, _ = _form_tool(server_id)
        async with calling(host, f"{server_id}::{tool}") as results:
            card = await _until_card(tab, "elicitation_form")
            assert tab.sync(HostState.answer_elicitation, card["id"], action) is None
        if server_id == "modern":
            assert json.loads(results[0].text) == {"action": action, "content": None}
        else:
            assert results[0].text == f"no name ({action})"


@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_url_elicitation_opens_only_on_accept(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry, action: str
) -> None:
    """REQ-HSH-015 (both eras): the url card shows the full URL; accept answers ``accept``
    with an external redirect to it; decline answers ``decline`` and redirects nowhere."""
    async with ui_host(setup, era_server, prompts) as (host, server_id, tab):
        async with calling(host, f"{server_id}::elicit_url", {"url": URL}) as results:
            card = await _until_card(tab, "elicitation_url")
            assert card["url"] == URL
            spec = tab.sync(HostState.answer_elicitation, card["id"], action)
        if action == "accept":
            assert redirect_target(spec) == (URL, True)
        else:
            assert spec is None
        text = results[0].text
        assert (json.loads(text)["action"] if server_id == "modern" else text) == action


async def test_sampling_consent_edit_reaches_the_provider(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-UI-005, REQ-HSH-002 (both eras): the sampling consent card shows the messages
    and max tokens; an edited message is what the provider receives."""
    provider = FakeProvider("Hi Ada!")
    async with ui_host(setup, era_server, prompts, provider) as (host, server_id, tab):
        async with calling(host, f"{server_id}::sample") as results:
            card = await _until_card(tab, "consent")
            assert card["is_sampling"] is True
            assert card["messages"][0]["text"] == ("Greet Ada" if server_id == "modern" else "hi")
            assert card["max_tokens"] == (32 if server_id == "modern" else 8)
            assert card["system_prompt"] == ""
            tab.sync(HostState.toggle_edit, card["id"])
            edits = {card["messages"][0]["field"]: "Edited by the user"}
            tab.sync(HostState.approve_edited_sampling, card["id"], edits)
        assert results[0].text == "Hi Ada!"
        [(params, sent_to)] = provider.calls
        assert params.messages[0].content.text == "Edited by the user"  # type: ignore[union-attr]
        assert sent_to == server_id


async def test_review_card_reject_refuses_per_era(
    setup: Setup, era_server: tuple[str, str], prompts: PromptRegistry
) -> None:
    """REQ-HSH-004 (both eras): with ``review_sampling_responses = true`` the review card
    shows the completion; Reject refuses it — legacy: the Server gets ``-1`` and the call
    ends ``denied`` / ``sampling_denied``; modern: ``denied`` / ``sampling_denied``."""
    provider = FakeProvider("Hi Ada!")
    extra = "\nreview_sampling_responses = true"
    async with ui_host(setup, era_server, prompts, provider, extra) as (host, server_id, tab):
        async with calling(host, f"{server_id}::sample") as results:
            consent = await _until_card(tab, "consent")
            tab.sync(HostState.answer_card, consent["id"], "allow_once")
            review = await _until_card(tab, "sampling_review")
            assert review["completion"]["text"] == "Hi Ada!"
            tab.sync(HostState.answer_review, review["id"], False)
        [result] = results
        # modern: the input request fails denied / sampling_denied; legacy: the Server is
        # answered -1 and the attributed denial overrides the call's outcome (REQ-HSH-005)
        assert result.outcome is InvocationOutcome.denied
        assert result.error is not None
        assert result.error.code == "sampling_denied"
        assert provider.calls  # the completion was produced, then refused on review


@pytest.mark.interop
async def test_unattributed_legacy_request_offers_two_answers(
    setup: Setup, legacy_stdio: Any, prompts: PromptRegistry
) -> None:
    """REQ-HSH-047: a legacy sampling request with two calls in flight is unattributed:
    its card carries the badge and offers only ``allow_once`` and ``deny``."""
    from tests.integration.test_invoker import stdio_table

    era = ("legacy", stdio_table(legacy_stdio))
    async with ui_host(setup, era, prompts, FakeProvider("Hi!")) as (host, _, tab):
        busy = host.invoker.start("legacy::slow", {"seconds": 30}, consumer=Consumer.agent("bot"))
        supervisor = host.connections.supervisor("legacy")
        with anyio.fail_after(TIMEOUT):
            while len(supervisor.inflight) < 1:  # noqa: ASYNC110 - bounded poll of a counter
                await anyio.sleep(0.01)
        async with calling(host, "legacy::sample") as results:
            card = await _until_card(tab, "consent")
            assert card["unattributed"] is True
            assert card["answers"] == ["allow_once", "deny"]
            assert card["asking"] == "unattributed"
            tab.sync(HostState.answer_card, card["id"], "deny")
        await busy.cancel()
        assert results[0].text == "sampling error -1: Sampling request rejected by host"
