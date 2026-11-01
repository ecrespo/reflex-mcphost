"""Elicitation, sampling-consent and sampling-review cards (F6-06; API §4.10, REQ-UI-005)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.consent.models import ArgumentPreview, ConsentDecision, ConsentRequest
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingReview,
)
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import models
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.components.consent_center import consent_center
from reflex_mcphost.ui.prompter import (
    PendingPrompt,
    PromptAnswerError,
    PromptRegistry,
    review_verdict,
    sampling_answer,
)
from reflex_mcphost.ui.state import HostState
from tests.unit.ui.rig import redirect_target

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 1, tzinfo=UTC)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 2}, "age": {"type": "integer"}},
    "required": ["name"],
}
URL = "https://auth.example.com/connect?x=1"


def _form(**extra: object) -> ElicitationRequest:
    return ElicitationRequest(
        id="req_form",
        server_id="srv",
        server_name="Fixture",
        message="<b>Tell me</b>",
        requested_schema=SCHEMA,
        **extra,  # type: ignore[arg-type]
    )


def _url() -> ElicitationRequest:
    return ElicitationRequest(
        id="req_url", server_id="srv", server_name="Fixture", message="Sign in", mode="url", url=URL
    )


def _sampling(attribution: str = "exact") -> ConsentRequest:
    params = types.CreateMessageRequestParams(
        messages=[
            types.SamplingMessage(role="user", content=types.TextContent(type="text", text="hi")),
            types.SamplingMessage(
                role="user",
                content=types.ImageContent(type="image", data="AA==", mime_type="image/png"),
            ),
        ],
        system_prompt="Be brief",
        max_tokens=8,
        model_preferences=types.ModelPreferences(
            hints=[types.ModelHint(name="claude")], speed_priority=0.5
        ),
    )
    return ConsentRequest(
        id="req_sample",
        operation=OperationKind.SAMPLING,
        consumer=Consumer.human(),
        server_id="srv",
        server_name="Fixture",
        preview=ArgumentPreview(format="text", body=""),
        sampling=SamplingReview(server_id="srv", params=params),
        attribution=attribution,  # type: ignore[arg-type]
    )


def _review() -> SamplingResponseReview:
    result = types.CreateMessageResult(
        role="assistant", content=types.TextContent(type="text", text="Hi!"), model="fake"
    )
    return SamplingResponseReview(request_id="req_review", server_id="srv", result=result)


def _cards(*prompts: tuple[str, object]) -> list[models.Row]:
    return models.prompt_cards(
        PendingPrompt(kind, getattr(p, "id", "req_review"), p, NOW)  # type: ignore[arg-type]
        for kind, p in prompts
    )


def test_cards_for_every_remaining_prompt_kind() -> None:
    """REQ-UI-005, REQ-HSH-002, REQ-HSH-004, REQ-HSH-010, REQ-HSH-015, REQ-HSH-047: the
    form card carries the Server, message and fields (with re-prompt errors); the url card
    the full URL; the sampling consent card messages, system prompt, model preferences and
    max tokens; the review card the completion; unattributed requests are flagged."""
    form, url, sample, review = _cards(
        ("elicitation_form", _form(errors={"name": "too short"}, attribution="unattributed")),
        ("elicitation_url", _url()),
        ("consent", _sampling()),
        ("sampling_review", _review()),
    )
    assert (form["server_name"], form["message"]) == ("Fixture", "<b>Tell me</b>")
    assert [f["name"] for f in form["fields"]] == ["name", "age"]
    assert form["errors"] == {"name": "too short"}
    assert form["unattributed"] is True
    assert url["url"] == URL
    assert url["unattributed"] is False
    assert sample["is_sampling"] is True
    assert next(m["text"] for m in sample["messages"]) == "hi"
    assert sample["messages"][1]["kind"] == "image"
    assert sample["messages"][0]["field"] == "message_0"
    assert (sample["system_prompt"], sample["max_tokens"]) == ("Be brief", 8)
    assert sample["model_hints"] == ["claude"]
    assert sample["priorities"] == "speed 0.5"
    assert sample["targets"] == ["server", "global"]
    assert (review["completion"]["text"], review["model"], review["editable"]) == (
        "Hi!",
        "fake",
        True,
    )
    [unattributed] = _cards(("consent", _sampling("unattributed")))
    assert unattributed["answers"] == ["allow_once", "deny"]


def test_sampling_answer_edits_text_messages_only() -> None:
    """REQ-HSH-002: Approve maps like consent; Edit replaces a text message and sends the
    edited request as ``edited_sampling``; a rejection carries no edit; non-text messages
    cannot be edited."""
    request = _sampling()
    plain = sampling_answer(request, "allow_once", "server", "once", {"message_0": "hi"})
    assert plain.decision is ConsentDecision.ALLOW_ONCE
    assert plain.edited_sampling is None
    edited = sampling_answer(request, "allow_once", "server", "once", {"message_0": "edited"})
    assert edited.edited_sampling is not None
    assert edited.edited_sampling.messages[0].content.text == "edited"  # type: ignore[union-attr]
    assert edited.edited_sampling.max_tokens == 8
    rejected = sampling_answer(request, "deny", "server", "once", {"message_0": "edited"})
    assert (rejected.decision, rejected.edited_sampling) == (ConsentDecision.DENY, None)
    with pytest.raises(PromptAnswerError):
        sampling_answer(request, "allow_once", "server", "once", {"message_1": "x"})
    with pytest.raises(PromptAnswerError):
        sampling_answer(request, "allow_always", "tool", "once")


def test_review_verdicts() -> None:
    """REQ-HSH-004: Approve, Edit (new completion text → ``edited_result``) and Reject."""
    review = _review()
    assert review_verdict(review, True).edited_result is None
    assert review_verdict(review, True, "Hi!").edited_result is None
    edited = review_verdict(review, True, "Hello")
    assert edited.approved
    assert edited.edited_result is not None
    assert edited.edited_result.content.text == "Hello"  # type: ignore[union-attr]
    assert review_verdict(review, False, "Hello").approved is False


def test_elicitation_answer_validates_before_anything_is_sent() -> None:
    """REQ-HSH-010, REQ-HSH-011, REQ-HSH-013: accepted content is coerced and validated
    against the requested schema; invalid content returns field errors and no response;
    decline / cancel carry no content."""
    request = _form()
    response, errors = models.elicitation_answer(request, "accept", {"name": "A"})
    assert response is None
    assert set(errors) == {"name"}  # minLength, checked by the bounded core validator
    response, errors = models.elicitation_answer(request, "accept", {"name": "Ada", "age": "36"})
    assert errors == {}
    assert response == ElicitationResponse(action="accept", content={"name": "Ada", "age": 36})
    assert models.elicitation_answer(request, "accept", {})[1] == {"name": "this field is required"}
    assert models.elicitation_answer(request, "decline") == (
        ElicitationResponse(action="decline"),
        {},
    )
    assert models.elicitation_answer(_url(), "accept") == (ElicitationResponse(action="accept"), {})
    with pytest.raises(ValueError, match="unknown"):
        models.elicitation_answer(request, "maybe")


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> PromptRegistry:
    fresh = PromptRegistry()
    monkeypatch.setattr(prompter_mod, "PROMPTS", fresh)
    return fresh


@asynccontextmanager
async def asking(
    registry: PromptRegistry, kind: str, request_id: str, payload: object
) -> AsyncIterator[list[object]]:
    answers: list[object] = []
    async with anyio.create_task_group() as tg:

        async def ask() -> None:
            answers.append(await registry.ask(kind, request_id, payload))  # type: ignore[arg-type]

        tg.start_soon(ask)
        with anyio.fail_after(5):
            while not registry.pending():
                await anyio.lowlevel.checkpoint()
        yield answers
        with anyio.fail_after(5):
            while registry.pending() and answers == []:
                await anyio.lowlevel.checkpoint()
        tg.cancel_scope.cancel()


async def test_form_handler_keeps_errors_on_the_card(registry: PromptRegistry) -> None:
    """REQ-HSH-013, REQ-UI-005: an invalid submit leaves the card pending with the field
    error and resolves nothing; a valid submit answers ``accept`` with the content."""
    state = HostState(_reflex_internal_init=True)
    async with asking(registry, "elicitation_form", "req_form", _form()) as answers:
        HostState.submit_elicitation.fn(state, "req_form", {"name": "A"})
        assert "name" in state.elicitation_errors["req_form"]
        assert registry.get("req_form") is not None
        HostState.submit_elicitation.fn(state, "req_form", {"name": "Ada"})
    assert answers == [ElicitationResponse(action="accept", content={"name": "Ada"})]
    assert state.elicitation_errors == {}


async def test_url_handler_redirects_only_on_accept(registry: PromptRegistry) -> None:
    """REQ-HSH-015: Open-and-accept answers ``accept`` and opens the full URL with an
    external redirect; Decline answers ``decline`` and opens nothing."""
    state = HostState(_reflex_internal_init=True)
    async with asking(registry, "elicitation_url", "req_url", _url()) as answers:
        spec = HostState.answer_elicitation.fn(state, "req_url", "accept")
    assert redirect_target(spec) == (URL, True)
    assert answers == [ElicitationResponse(action="accept")]
    async with asking(registry, "elicitation_url", "req_url", _url()) as answers:
        assert HostState.answer_elicitation.fn(state, "req_url", "decline") is None
    assert answers == [ElicitationResponse(action="decline")]
    assert HostState.answer_elicitation.fn(state, "gone", "accept") is None
    assert state.prompt_error == "This prompt is no longer pending."


async def test_sampling_and_review_handlers(registry: PromptRegistry) -> None:
    """REQ-HSH-002, REQ-HSH-004: the edited-approve handler answers once with the edited
    request; the review handlers answer Approve / Edit / Reject."""
    state = HostState(_reflex_internal_init=True)
    HostState.toggle_edit.fn(state, "req_sample")
    assert state.editing == ["req_sample"]
    async with asking(registry, "consent", "req_sample", _sampling()) as answers:
        HostState.approve_edited_sampling.fn(state, "req_sample", {"message_0": "edited"})
    [answer] = answers
    assert answer.edited_sampling.messages[0].content.text == "edited"  # type: ignore[attr-defined]
    assert state.editing == []
    async with asking(registry, "sampling_review", "req_review", _review()) as answers:
        HostState.submit_review_edit.fn(state, "req_review", {"completion": "Hello"})
    assert answers[0].edited_result.content.text == "Hello"  # type: ignore[attr-defined]
    async with asking(registry, "sampling_review", "req_review", _review()) as answers:
        HostState.answer_review.fn(state, "req_review", False)
    assert answers[0].approved is False  # type: ignore[attr-defined]
    async with asking(registry, "consent", "req_sample", _sampling()) as answers:
        HostState.approve_edited_sampling.fn(state, "req_sample", {"message_1": "x"})
        assert state.prompt_error == "message '1' cannot be edited"
        HostState.answer_card.fn(state, "req_sample", "deny")
    assert answers[0].decision is ConsentDecision.DENY  # type: ignore[attr-defined]


def test_consent_center_renders_every_card_kind_as_plain_text() -> None:
    """REQ-UI-005, Art. 5: the side panel renders elicitation form / url, sampling
    consent (labelled deprecated) and review cards with their answers; no Markdown or
    raw HTML."""
    rendered = str(consent_center())
    for needle in (
        "elicitation-form",
        "elicitation-url",
        "elicitation-open",
        "elicitation-decline",
        "elicitation-cancel",
        "sampling-consent",
        "deprecated, compatibility",
        "sampling-edit",
        "review-card",
        "unattributed",
    ):
        assert needle in rendered, needle
    for forbidden in ("Markdown", "dangerouslySetInnerHTML"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("url", "openable"),
    [
        ("javascript:alert(1)", False),
        ("data:text/html;base64,PGI+", False),
        ("file:///etc/hosts", False),
        ("https:///no-host", False),
        ("http://[::1", False),  # urlsplit raises ValueError
        (None, False),
        ("http://example.com/x", True),
        ("HTTPS://auth.example.com/connect?x=1", True),
    ],
)
def test_is_openable_url(url: object, openable: bool) -> None:
    """REQ-HSH-015, Art. 5 (second layer after core): only http(s) URLs with a host may be
    opened; the card never carries any other URL and marks it refused."""
    assert models.is_openable_url(url) is openable
    request = _url().model_copy(update={"url": url})
    [card] = _cards(("elicitation_url", request))
    assert card["url"] == (url if openable else "")
    assert card["url_refused"] is (not openable)


async def test_non_http_url_is_declined_without_a_redirect(registry: PromptRegistry) -> None:
    """REQ-HSH-015: accepting a url elicitation whose URL is not http(s) emits no redirect
    event, answers ``decline`` and shows a fixed error that does not echo the URL."""
    state = HostState(_reflex_internal_init=True)
    bad = _url().model_copy(update={"url": "javascript:alert(1)"})
    async with asking(registry, "elicitation_url", "req_url", bad) as answers:
        assert HostState.answer_elicitation.fn(state, "req_url", "accept") is None
    assert answers == [ElicitationResponse(action="decline")]
    assert state.prompt_error != ""
    assert "javascript" not in state.prompt_error
