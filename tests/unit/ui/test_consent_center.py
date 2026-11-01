"""``consent_center`` and the HostState answer handlers (F6-03; API §8, REQ-UI-005)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest

from reflex_mcphost.core.config.models import ConfigOrigin, SourceKind
from reflex_mcphost.core.consent.models import ConsentDecision, ConsentRequest
from reflex_mcphost.core.prompter import TrustRequest
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.components.consent_center import consent_center
from reflex_mcphost.ui.prompter import PromptRegistry
from reflex_mcphost.ui.state import HostState
from tests.unit.core.consent.factories import tool_request

pytestmark = pytest.mark.anyio


def test_consent_center_is_a_non_blocking_side_panel_of_plain_text() -> None:
    """REQ-UI-005, Art. 5: the panel is a non-modal drawer (never a page-blocking dialog)
    rendered from ``HostState.cards``; Server-provided text goes through text components —
    no Markdown, no raw HTML."""
    rendered = str(consent_center())
    assert "Drawer" in rendered
    assert "modal:false" in rendered.replace(" ", "")
    assert "cards" in rendered
    for forbidden in ("Markdown", "dangerouslySetInnerHTML", "AlertDialog", "DialogRoot"):
        assert forbidden not in rendered
    assert 'data-testid":"consent-center' in rendered.replace(" ", "") or (
        "consent-center" in rendered
    )


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> PromptRegistry:
    """A fresh process-wide registry for the handlers under test."""
    fresh = PromptRegistry()
    monkeypatch.setattr(prompter_mod, "PROMPTS", fresh)
    return fresh


@asynccontextmanager
async def asking(registry: PromptRegistry, kind: str, request: object) -> AsyncIterator[list]:
    answers: list[object] = []
    async with anyio.create_task_group() as tg:

        async def ask() -> None:
            answers.append(await registry.ask(kind, request.id, request))  # type: ignore[arg-type, attr-defined]

        tg.start_soon(ask)
        with anyio.fail_after(5):
            while not registry.pending():
                await anyio.lowlevel.checkpoint()
        yield answers
        tg.cancel_scope.cancel()


async def test_answer_consent_resolves_the_prompt(registry: PromptRegistry) -> None:
    """REQ-CNS-006, REQ-UI-005: a card answer reaches core as the mapped ConsentAnswer."""
    request = tool_request()
    state = HostState(_reflex_internal_init=True)
    async with asking(registry, "consent", request) as answers:
        HostState.answer_consent.fn(state, request.id, "allow_always", "server", "once")
        with anyio.fail_after(5):
            while not answers:
                await anyio.lowlevel.checkpoint()
    [answer] = answers
    assert answer.decision is ConsentDecision.ALLOW_ALWAYS  # type: ignore[attr-defined]
    assert answer.target_kind == "server"  # type: ignore[attr-defined]
    assert state.prompt_error == ""


async def test_answer_consent_rejects_options_an_unattributed_card_lacks(
    registry: PromptRegistry,
) -> None:
    """REQ-HSH-047: ``allow_always`` on an unattributed card is rejected by the handler (an
    error is shown) and never reaches core; the prompt stays pending."""
    request: ConsentRequest = tool_request(consumer=Consumer.human(), attribution="unattributed")
    state = HostState(_reflex_internal_init=True)
    async with asking(registry, "consent", request) as answers:
        HostState.answer_consent.fn(state, request.id, "allow_always", "tool", "once")
        assert state.prompt_error != ""
        assert registry.get(request.id) is not None
        HostState.answer_consent.fn(state, "missing", "allow_once", "tool", "once")
        assert answers == []


async def test_answer_trust_resolves_the_prompt(registry: PromptRegistry) -> None:
    """REQ-REG-011 (trust UI): Trust / Don't trust answer ``confirm_trust``."""
    origin = ConfigOrigin(kind=SourceKind.MCP_JSON_PROJECT, path=Path("/p"), precedence=3)
    request = TrustRequest(id="trust_9", server_id="proj", origin=origin, command="x")
    state = HostState(_reflex_internal_init=True)
    async with asking(registry, "trust", request) as answers:
        HostState.answer_trust.fn(state, request.id, False)
        with anyio.fail_after(5):
            while not answers:
                await anyio.lowlevel.checkpoint()
    assert answers == [False]
