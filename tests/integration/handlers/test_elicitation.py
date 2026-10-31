"""Elicitation against the modern fixture Server, stdio and Streamable HTTP (F4-01; the
legacy path is in ``test_legacy_requests.py``)."""

from __future__ import annotations

import json

import pytest

from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.mrtr import ELICITATION, InputHandlerError
from reflex_mcphost.core.prompter import ElicitationResponse
from tests.integration.handlers.helpers import HandlerPrompter, handler_host, modern_table
from tests.integration.test_host import Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["modern_table", "setup"]  # re-exported fixtures


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (ElicitationResponse(action="accept", content={"name": "Ada"}), "hello Ada"),
        (ElicitationResponse(action="decline"), "no name (decline)"),
        (ElicitationResponse(action="cancel"), "no name (cancel)"),
    ],
)
async def test_ask_name_actions_reach_the_server(
    setup: Setup, modern_table: str, answer: ElicitationResponse, text: str
) -> None:
    """REQ-HSH-010, REQ-HSH-011: accept (with content), decline and cancel reach the Server
    as the Consumer answered; the form shows the Server and its message."""
    prompter = HandlerPrompter([answer])
    async with handler_host(setup, {"srv": modern_table}, prompter=prompter) as host:
        result = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
    assert (result.outcome, result.text) == (Out.success, text)
    (shown,) = prompter.elicited
    assert (shown.server_id, shown.server_name, shown.message) == (
        "srv",
        "fixture-modern",
        "What is your name?",
    )


async def test_invalid_content_is_reprompted_not_sent(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-013: content failing the requested schema is re-prompted with field errors;
    the Server receives only the valid answer."""
    prompter = HandlerPrompter(
        [
            ElicitationResponse(action="accept", content={"name": "A", "age": -1}),
            ElicitationResponse(action="accept", content={"name": "Ada", "age": 36}),
        ]
    )
    async with handler_host(setup, {"srv": modern_table}, prompter=prompter) as host:
        result = await host.invoker.call("srv::elicit_form", {}, consumer=BOT)
        counts = await host.invoker.call("srv::call_counts", {}, consumer=BOT)
    assert result.ok, result.error
    assert json.loads(result.text) == {"action": "accept", "content": {"name": "Ada", "age": 36}}
    assert [set(r.errors) for r in prompter.elicited] == [set(), {"name", "age"}]
    assert json.loads(counts.text)["elicit_form"] == 2  # first leg + the one valid retry


async def test_bad_schema_is_input_handler_failed(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-012 (modern, delta D3): a schema outside the allowed subset ends the call
    protocol_error / input_handler_failed with details.reason=invalid_elicitation_schema; the
    prompter is never called and the call is not retried."""
    prompter = HandlerPrompter()
    async with handler_host(setup, {"srv": modern_table}, prompter=prompter) as host:
        result = await host.invoker.call("srv::elicit_bad_schema", {}, consumer=BOT)
        counts = await host.invoker.call("srv::call_counts", {}, consumer=BOT)
        record = await host.audit.get(result.id)
    assert result.outcome is Out.protocol_error
    assert result.error is not None
    assert result.error.code == "input_handler_failed"
    assert result.error.details == {"method": ELICITATION, "reason": "invalid_elicitation_schema"}
    assert (record.outcome, record.error_code) == (Out.protocol_error, "input_handler_failed")
    assert prompter.elicited == []
    assert json.loads(counts.text)["elicit_bad_schema"] == 1


async def test_bad_schema_in_a_read_raises(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-012 (API §4.6): the same schema inside a catalog read raises
    InputHandlerError."""
    prompter = HandlerPrompter()
    async with handler_host(setup, {"srv": modern_table}, prompter=prompter) as host:
        with pytest.raises(InputHandlerError) as info:
            await host.catalog.read_resource("srv", "fixture://bad_schema")
        assert info.value.reason == "invalid_elicitation_schema"
        answered = await host.catalog.read_resource("srv", "fixture://ask")
    assert prompter.elicited[0].server_id == "srv"
    assert answered.contents[0].text == "answered"  # type: ignore[union-attr]


async def test_url_mode_shows_the_full_url(setup: Setup, modern_table: str) -> None:
    """REQ-HSH-015: url mode shows the full URL and the Server receives the Consumer's
    action; a non-http(s) URL ends input_handler_failed / invalid_elicitation_url."""
    target = "https://auth.example.com/consent?flow=7&step=2"
    prompter = HandlerPrompter([ElicitationResponse(action="accept")])
    async with handler_host(setup, {"srv": modern_table}, prompter=prompter) as host:
        result = await host.invoker.call("srv::elicit_url", {"url": target}, consumer=BOT)
        bad = await host.invoker.call(
            "srv::elicit_url", {"url": "file:///etc/passwd"}, consumer=BOT
        )
    assert result.ok, result.error
    assert json.loads(result.text) == {"action": "accept", "content": None}
    (shown,) = prompter.elicited
    assert (shown.mode, shown.url) == ("url", target)
    assert bad.error is not None
    assert bad.error.details["reason"] == "invalid_elicitation_url"


async def test_no_eliciting_prompter_is_undeclared(setup: Setup) -> None:
    """REQ-HSH-014, REQ-HSH-044: without an eliciting prompter elicitation is not declared,
    so an MRTR elicitation is undeclared_input_request."""
    from tests.integration.handlers.helpers import modern_stdio_table

    prompter = HandlerPrompter(supports_elicitation=False)
    async with handler_host(setup, {"srv": modern_stdio_table()}, prompter=prompter) as host:
        result = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
    assert result.error is not None
    assert result.error.code == "undeclared_input_request"
    assert prompter.elicited == []
