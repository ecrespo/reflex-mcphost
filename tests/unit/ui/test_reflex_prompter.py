"""``ReflexPrompter`` and the consent / trust card answers (F6-03; API §4.10, §8)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp_types import CreateMessageResult, TextContent

from reflex_mcphost.core.config.models import ConfigOrigin, SourceKind
from reflex_mcphost.core.consent.models import ArgumentPreview, ConsentAnswer, ConsentDecision
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import models
from reflex_mcphost.ui.prompter import (
    PromptAnswerError,
    PromptRegistry,
    ReflexPrompter,
    consent_answer,
)
from tests.unit.core.consent.factories import tool_entry, tool_request

pytestmark = pytest.mark.anyio
PROJECT = ConfigOrigin(kind=SourceKind.MCP_JSON_PROJECT, path=Path("/p/.mcp.json"), precedence=3)


async def _answered(
    registry: PromptRegistry, call: Callable[[], Awaitable[Any]], answer: object
) -> tuple[Any, Any]:
    """Run ``call`` and answer the prompt it registers with ``answer``."""
    result: list[Any] = []
    async with registry.subscribe() as changes, anyio.create_task_group() as tg:

        async def run() -> None:
            result.append(await call())

        tg.start_soon(run)
        added = await changes.__anext__()
        prompt = registry.get(added.request_id or "")
        assert registry.resolve(added.request_id or "", answer)
    assert registry.pending() == []
    return prompt, result[0]


async def test_every_prompter_method_goes_through_the_registry() -> None:
    """REQ-UI-005, API §4.10/§8: each Prompter method registers a pending prompt of its
    kind with the core request as payload and returns the answer; it declares elicitation
    support."""
    registry = PromptRegistry()
    prompter = ReflexPrompter(registry)
    assert prompter.supports_elicitation is True
    request = tool_request()
    allow = ConsentAnswer(decision=ConsentDecision.ALLOW_ONCE)
    prompt, answer = await _answered(registry, lambda: prompter.ask_consent(request), allow)
    assert (prompt.kind, prompt.request_id, prompt.payload) == ("consent", request.id, request)
    assert answer == allow

    result = CreateMessageResult(
        role="assistant", content=TextContent(type="text", text="hi"), model="m"
    )
    review = SamplingResponseReview(request_id="smp_1", server_id="srv", result=result)
    verdict = SamplingVerdict(approved=True)
    prompt, got = await _answered(
        registry, lambda: prompter.review_sampling_response(review), verdict
    )
    assert (prompt.kind, got) == ("sampling_review", verdict)

    for mode, kind in (("form", "elicitation_form"), ("url", "elicitation_url")):
        el = ElicitationRequest(
            id=f"el_{mode}",
            server_id="srv",
            server_name="S",
            message="m",
            mode=mode,
            url="https://example.com/x" if mode == "url" else None,
        )
        reply = ElicitationResponse(action="decline")
        prompt, got = await _answered(registry, lambda el=el: prompter.elicit(el), reply)
        assert (prompt.kind, got) == (kind, reply)

    trust = TrustRequest(id="trust_1", server_id="proj", origin=PROJECT, command="python")
    prompt, trusted = await _answered(registry, lambda: prompter.confirm_trust(trust), True)
    assert (prompt.kind, trusted) == ("trust", True)
    _, not_trusted = await _answered(registry, lambda: prompter.confirm_trust(trust), "yes")
    assert not_trusted is False  # anything but True is "don't trust"


async def test_a_wrong_answer_type_fails_closed() -> None:
    """Art. 3, API §4.10: an answer of the wrong type raises, which core treats as deny."""
    registry = PromptRegistry()
    prompter = ReflexPrompter(registry)

    async def ask() -> object:
        try:
            return await prompter.ask_consent(tool_request())
        except PromptAnswerError as exc:
            return exc

    _, outcome = await _answered(registry, ask, "allow")
    assert isinstance(outcome, PromptAnswerError)


@pytest.mark.parametrize(
    ("choice", "target", "deny_lifetime", "expected"),
    [
        ("allow_once", "tool", "once", ConsentAnswer(decision=ConsentDecision.ALLOW_ONCE)),
        (
            "allow_session",
            "server",
            "once",
            ConsentAnswer(decision=ConsentDecision.ALLOW_SESSION, target_kind="server"),
        ),
        (
            "allow_always",
            "global",
            "once",
            ConsentAnswer(decision=ConsentDecision.ALLOW_ALWAYS, target_kind="global"),
        ),
        (
            "deny",
            "tool",
            "always",
            ConsentAnswer(decision=ConsentDecision.DENY, deny_lifetime="always"),
        ),
        (
            "deny",
            "server",
            "session",
            ConsentAnswer(
                decision=ConsentDecision.DENY, target_kind="server", deny_lifetime="session"
            ),
        ),
    ],
)
def test_card_answers_map_to_the_exact_consent_answer(
    choice: str, target: str, deny_lifetime: str, expected: ConsentAnswer
) -> None:
    """REQ-CNS-004, REQ-CNS-005, REQ-CNS-006 (answers from the UI): a card answer maps to the
    exact ``ConsentAnswer`` — decision, target and deny lifetime."""
    assert consent_answer(tool_request(), choice, target, deny_lifetime) == expected


@pytest.mark.parametrize(
    ("choice", "target", "deny_lifetime"),
    [
        ("allow_session", "tool", "once"),
        ("allow_always", "tool", "once"),
        ("deny", "tool", "session"),
        ("deny", "server", "always"),
        ("allow_once", "server", "once"),
    ],
)
def test_unattributed_requests_accept_only_allow_once_or_deny_once(
    choice: str, target: str, deny_lifetime: str
) -> None:
    """REQ-HSH-047 (UI options): for an unattributed request the handler rejects every
    answer but ``allow_once`` / ``deny`` (once) before it reaches core."""
    request = tool_request(consumer=Consumer.human(), attribution="unattributed")
    with pytest.raises(PromptAnswerError):
        consent_answer(request, choice, target, deny_lifetime)
    assert consent_answer(request, "allow_once", "tool", "once").decision == "allow_once"
    assert consent_answer(request, "deny", "tool", "once").deny_lifetime == "once"


@pytest.mark.parametrize(
    ("choice", "target", "deny_lifetime"),
    [("maybe", "tool", "once"), ("deny", "everything", "once"), ("deny", "tool", "forever")],
)
def test_unknown_answers_are_rejected(choice: str, target: str, deny_lifetime: str) -> None:
    """Art. 3: an answer outside the card's options never reaches core."""
    with pytest.raises(PromptAnswerError):
        consent_answer(tool_request(), choice, target, deny_lifetime)


async def _cards(registry: PromptRegistry, kind: Any, request_id: str, payload: object) -> Any:
    async with anyio.create_task_group() as tg:
        tg.start_soon(registry.ask, kind, request_id, payload)
        with anyio.fail_after(5):
            while not registry.pending():
                await anyio.lowlevel.checkpoint()
        cards = models.prompt_cards(registry.pending())
        tg.cancel_scope.cancel()
    return cards


async def test_consent_cards_show_the_request_and_its_options() -> None:
    """REQ-UI-005, REQ-CNS-002, REQ-CNS-007, REQ-HSH-047: a consent card names the asking
    Consumer, the FQN, the Server, the tool description (verbatim, as text), the argument
    preview and the definition-changed flag; it offers four answers and three targets with
    the REQ-CNS-007 statement for server/global; an unattributed card offers only
    ``allow_once`` and ``deny`` (once) and says it is unattributed."""
    tool = tool_entry("srv::echo", description="<script>x</script> **echo**")
    request = tool_request(tool, consumer=Consumer.agent("helper")).model_copy(
        update={
            "definition_changed": True,
            "preview": ArgumentPreview(body='{"a": "[redacted]"}', redacted_fields=("a",)),
        }
    )
    [card] = await _cards(PromptRegistry(), "consent", request.id, request)
    assert card["id"] == request.id
    assert card["kind"] == "consent"
    assert card["asking"] == "agent:helper"
    assert card["fqn"] == "srv::echo"
    assert card["server_id"] == "srv"
    assert card["server_name"] == "Fixture Server"
    assert card["description"] == "<script>x</script> **echo**"
    assert card["preview"] == '{"a": "[redacted]"}'
    assert card["redacted_fields"] == ["a"]
    assert card["definition_changed"] is True
    assert card["answers"] == ["allow_once", "allow_session", "allow_always", "deny"]
    assert card["targets"] == ["tool", "server", "global"]
    assert card["deny_lifetimes"] == ["once", "session", "always"]
    assert "added later" in card["scope_notice"]["server"]
    assert "added later" in card["scope_notice"]["global"]
    json.dumps(card)

    unattributed = tool_request(consumer=Consumer.human(), attribution="unattributed")
    [card] = await _cards(PromptRegistry(), "consent", unattributed.id, unattributed)
    assert card["answers"] == ["allow_once", "deny"]
    assert card["deny_lifetimes"] == ["once"]
    assert card["targets"] == ["tool"]
    assert card["unattributed"] is True
    assert card["asking"] == "unattributed"


async def test_trust_cards_show_names_never_values() -> None:
    """REQ-REG-011 (trust UI), REQ-REG-007: a trust card shows command, resolved command,
    args, cwd and env names with the risky ones flagged (stdio), or the URL template,
    destination and header names (HTTP)."""
    stdio = TrustRequest(
        id="trust_1",
        server_id="proj",
        origin=PROJECT,
        command="node",
        resolved_command="/usr/bin/node",
        args=("server.js",),
        cwd=Path("/p"),
        env_keys=("API_KEY", "NODE_OPTIONS"),
        risky_env_keys=("NODE_OPTIONS",),
    )
    [card] = await _cards(PromptRegistry(), "trust", stdio.id, stdio)
    assert card["kind"] == "trust"
    assert card["command"] == "node"
    assert card["resolved_command"] == "/usr/bin/node"
    assert card["args"] == ["server.js"]
    assert card["cwd"] == str(Path("/p"))
    assert card["env"] == [
        {"name": "API_KEY", "risky": False},
        {"name": "NODE_OPTIONS", "risky": True},
    ]
    assert card["origin_path"] == "/p/.mcp.json"
    http = TrustRequest(
        id="trust_2",
        server_id="web",
        origin=PROJECT,
        transport="http",
        destination="https://mcp.example.com",
        url_template="https://mcp.example.com/${PATH_PART}",
        header_keys=("Authorization",),
    )
    [card] = await _cards(PromptRegistry(), "trust", http.id, http)
    assert card["destination"] == "https://mcp.example.com"
    assert card["url_template"] == "https://mcp.example.com/${PATH_PART}"
    assert card["header_keys"] == ["Authorization"]
    json.dumps(card)


async def test_other_prompt_kinds_get_a_minimal_card() -> None:
    """REQ-UI-005: sampling-review and elicitation prompts are listed (their full cards are
    F6-06) with their kind and Server."""
    el = ElicitationRequest(id="el_1", server_id="srv", server_name="S", message="<b>hi</b>")
    [card] = await _cards(PromptRegistry(), "elicitation_form", el.id, el)
    assert (card["kind"], card["server_id"], card["id"]) == ("elicitation_form", "srv", "el_1")
    json.dumps(card)
