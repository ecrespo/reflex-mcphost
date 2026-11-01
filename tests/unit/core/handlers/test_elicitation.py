"""``ElicitationHandler`` core and both era adapters with a fake prompter (F4-01)."""

from __future__ import annotations

from typing import Any

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.handlers.base import Refusal
from reflex_mcphost.core.handlers.elicitation import ElicitationHandler
from reflex_mcphost.core.mrtr import ELICITATION, InputContext, InputHandlerError, PausableDeadline
from reflex_mcphost.core.prompter import ElicitationRequest, ElicitationResponse
from tests.unit.core.handlers.fakes import ScriptedPrompter, ctx, legacy_context

pytestmark = pytest.mark.anyio

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 2}, "age": {"type": "integer"}},
    "required": ["name"],
}
BAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"nested": {"type": "object", "properties": {}}},
}


def form(
    schema: dict[str, Any] = SCHEMA, message: str = "Who are you?"
) -> types.ElicitRequestFormParams:
    return types.ElicitRequestFormParams(message=message, requested_schema=schema)


def url(target: str) -> types.ElicitRequestURLParams:
    return types.ElicitRequestURLParams(message="Sign in", url=target, elicitation_id="e1")


def handler(prompter: Any, *, timeout_s: float = 5.0) -> ElicitationHandler:
    return ElicitationHandler(
        lambda: prompter, lambda server_id: f"name of {server_id}", timeout_s=timeout_s
    )


ACCEPT = ElicitationResponse(action="accept", content={"name": "Ada"})


async def test_form_accept_returns_validated_content() -> None:
    """REQ-HSH-010, REQ-HSH-011: a form request shows the Server's identity, message and
    schema; accept returns the content."""
    prompter = ScriptedPrompter([ACCEPT])
    result = await handler(prompter).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})
    (shown,) = prompter.elicited
    assert (shown.server_id, shown.server_name, shown.message) == (
        "srv",
        "name of srv",
        "Who are you?",
    )
    assert (shown.mode, shown.requested_schema, shown.url) == ("form", SCHEMA, None)
    assert shown.errors == {}
    assert shown.attribution == "exact"


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_decline_and_cancel_carry_no_content(action: Any) -> None:
    """REQ-HSH-011: decline / cancel are returned as that action without content."""
    prompter = ScriptedPrompter([ElicitationResponse(action=action, content={"name": "x"})])
    result = await handler(prompter).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action=action)


async def test_invalid_content_reprompts_with_errors() -> None:
    """REQ-HSH-013: content that fails the requested schema re-prompts with field errors and
    nothing is answered until it validates."""
    prompter = ScriptedPrompter(
        [ElicitationResponse(action="accept", content={"name": "A", "age": "x"}), ACCEPT]
    )
    result = await handler(prompter).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})
    first, second = prompter.elicited
    assert first.errors == {}
    assert set(second.errors) == {"name", "age"}
    assert second.id == first.id


async def test_missing_required_field_is_a_field_error() -> None:
    """REQ-HSH-013: a missing required property is reported on that field."""
    prompter = ScriptedPrompter([ElicitationResponse(action="accept", content={}), ACCEPT])
    await handler(prompter).handle(ctx(ELICITATION), form())
    assert set(prompter.elicited[1].errors) == {"name"}


async def test_endless_invalid_content_is_cancelled() -> None:
    """REQ-HSH-013 (fail closed): a prompter that never submits valid content ends in
    cancel after the re-prompt cap; nothing invalid is ever returned."""
    prompter = ScriptedPrompter([ElicitationResponse(action="accept", content={"name": "A"})])
    result = await handler(prompter).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action="cancel")
    assert len(prompter.elicited) == ElicitationHandler.MAX_ATTEMPTS


async def test_bad_schema_never_reaches_the_prompter() -> None:
    """REQ-HSH-012: a schema outside the allowed subset is refused without rendering."""
    prompter = ScriptedPrompter([ACCEPT])
    result = await handler(prompter).handle(ctx(ELICITATION), form(BAD_SCHEMA))
    assert isinstance(result, Refusal)
    assert result.reason == "invalid_elicitation_schema"
    assert prompter.elicited == []


async def test_bad_schema_modern_adapter_raises_input_handler_error() -> None:
    """REQ-HSH-012 (modern, delta D3): the router slot raises
    InputHandlerError("elicitation/create", "invalid_elicitation_schema")."""
    prompter = ScriptedPrompter([ACCEPT])
    slot = handler(prompter).as_input_handler()
    with pytest.raises(InputHandlerError) as info:
        await slot(ctx(ELICITATION), types.ElicitRequest(params=form(BAD_SCHEMA)))
    assert (info.value.method, info.value.reason) == (ELICITATION, "invalid_elicitation_schema")
    assert prompter.elicited == []


async def test_bad_schema_legacy_adapter_returns_invalid_params() -> None:
    """REQ-HSH-012 (legacy): the SDK callback returns ErrorData(-32602, "Unsupported
    elicitation schema") and the prompter is not called."""
    prompter = ScriptedPrompter([ACCEPT])
    seen: list[tuple[str, str]] = []

    def attribute(method: str, key: str) -> InputContext:
        seen.append((method, key))
        return ctx(method)

    callback = handler(prompter).as_callback(attribute)
    result = await callback(legacy_context(9), form(BAD_SCHEMA))
    assert result == types.ErrorData(
        code=types.INVALID_PARAMS, message="Unsupported elicitation schema"
    )
    assert prompter.elicited == []
    assert seen == [(ELICITATION, "9")]


async def test_legacy_adapter_returns_the_result() -> None:
    """REQ-HSH-045: the legacy callback runs the same core and returns its ElicitResult;
    an unattributed request is shown as such (REQ-HSH-047, delta D4)."""
    prompter = ScriptedPrompter([ACCEPT])
    callback = handler(prompter).as_callback(
        lambda method, key: ctx(method, consumer=None, attribution="unattributed")
    )
    result = await callback(legacy_context(), form())
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})
    assert prompter.elicited[0].attribution == "unattributed"


async def test_modern_adapter_returns_the_result() -> None:
    """REQ-HSH-010, REQ-HSH-011: the router slot answers the input request."""
    slot = handler(ScriptedPrompter([ACCEPT])).as_input_handler()
    result = await slot(ctx(ELICITATION), types.ElicitRequest(params=form()))
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})


async def test_prompter_raising_is_cancel() -> None:
    """REQ-HSH-011 (API §4.10, fail closed): a prompter exception answers cancel."""
    prompter = ScriptedPrompter([RuntimeError("ui crashed")])
    assert await handler(prompter).handle(ctx(ELICITATION), form()) == types.ElicitResult(
        action="cancel"
    )


async def test_prompter_hanging_is_cancel() -> None:
    """REQ-HSH-011 (delta D8): elicit() is bounded by consent_timeout_s; a timeout answers
    cancel."""
    prompter = ScriptedPrompter(["hang"])
    with anyio.fail_after(3):
        result = await handler(prompter, timeout_s=0.05).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action="cancel")


@pytest.mark.parametrize("prompter", [None, ScriptedPrompter([ACCEPT], supports_elicitation=False)])
async def test_no_eliciting_prompter_is_cancel(prompter: Any) -> None:
    """REQ-HSH-014 (fail closed): without an eliciting prompter the answer is cancel."""
    assert await handler(prompter).handle(ctx(ELICITATION), form()) == types.ElicitResult(
        action="cancel"
    )


async def test_prompter_returning_garbage_is_cancel() -> None:
    """API §4.10 (fail closed): a non-ElicitationResponse answer is cancel."""

    async def garbage(request: ElicitationRequest) -> Any:
        return {"action": "accept"}

    result = await handler(ScriptedPrompter([garbage])).handle(ctx(ELICITATION), form())
    assert result == types.ElicitResult(action="cancel")


async def test_prompter_wait_pauses_the_deadline() -> None:
    """DD-004: the wait on elicit() runs inside ctx.prompter_wait(), so the invocation's
    budget does not run meanwhile."""
    deadline = PausableDeadline(0.2)

    async def slow(request: ElicitationRequest) -> ElicitationResponse:
        await anyio.sleep(0.4)
        return ACCEPT

    with deadline.running():
        result = await handler(ScriptedPrompter([slow])).handle(
            ctx(ELICITATION, deadline=deadline), form()
        )
    assert result == types.ElicitResult(action="accept", content={"name": "Ada"})
    assert not deadline.expired
    assert deadline.paused_s >= 0.3


async def test_url_mode_passes_the_full_url() -> None:
    """REQ-HSH-015: url mode shows the full URL and returns the Consumer's action (the UI
    opens it on accept, delta D8)."""
    target = "https://auth.example.com/connect?session=abc&step=2#frag"
    prompter = ScriptedPrompter([ElicitationResponse(action="accept")])
    result = await handler(prompter).handle(ctx(ELICITATION), url(target))
    assert result == types.ElicitResult(action="accept")
    (shown,) = prompter.elicited
    assert (shown.mode, shown.url, shown.requested_schema) == ("url", target, None)


@pytest.mark.parametrize(
    "target", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.example", "https:///nohost"]
)
async def test_url_mode_rejects_other_schemes(target: str) -> None:
    """REQ-HSH-015 (API §4.8 step 6): only http(s) URLs with a host are shown; anything
    else is refused with reason invalid_elicitation_url (legacy: -32602)."""
    prompter = ScriptedPrompter([ElicitationResponse(action="accept")])
    h = handler(prompter)
    result = await h.handle(ctx(ELICITATION), url(target))
    assert isinstance(result, Refusal)
    assert result.reason == "invalid_elicitation_url"
    legacy = await h.as_callback(lambda m, k: ctx(m))(legacy_context(), url(target))
    assert isinstance(legacy, types.ErrorData)
    assert legacy.code == types.INVALID_PARAMS
    assert prompter.elicited == []
