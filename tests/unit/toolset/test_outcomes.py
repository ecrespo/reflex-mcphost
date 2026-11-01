"""``ToolCallOutcome`` per invocation outcome (F5-02; API §4.12 text table, delta D3)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.toolset import outcome_of
from tests.unit.toolset.fakes import Rig, result, tool

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
ARG_SECRET = "toolset-secret-arg-0001"  # noqa: S105 - test sentinel

#: A realistic result per outcome: the error messages are the Invoker's own wording.
CASES: dict[InvocationOutcome, dict[str, Any]] = {
    Out.success: {"text": "hello"},
    Out.tool_error: {"text": "tool failed", "error": "the tool reported an error"},
    Out.protocol_error: {"error": "structuredContent does not match the output schema"},
    Out.timeout: {"error": "the tool call timed out after 30 s"},
    Out.cancelled: {"error": "the invocation was cancelled"},
    Out.denied: {"error": "no prompter and no matching rule"},
    Out.invalid_arguments: {"error": "arguments do not match the tool's input schema"},
    Out.not_found: {"error": "no such tool: srv::echo"},
    Out.server_unavailable: {"error": "server srv is reconnecting"},
    Out.audit_unavailable: {"error": "the audit record could not be written"},
    Out.connection_lost: {"error": "the connection to srv was lost"},
}


def test_every_outcome_has_a_case() -> None:
    """REQ-TLS-005: the table below covers all 11 InvocationOutcome values."""
    assert set(CASES) == set(InvocationOutcome)


@pytest.mark.parametrize("outcome", list(InvocationOutcome))
def test_outcome_mapping(outcome: InvocationOutcome) -> None:
    """REQ-TLS-005, REQ-NFR-010: is_error iff the outcome is not success; the text follows
    the D3 table (exact denial prefix); result and structured content are carried."""
    invocation = result(outcome, **CASES[outcome])
    mapped = outcome_of(invocation)
    assert mapped.is_error is (outcome is not Out.success)
    assert mapped.result == invocation
    assert mapped.structured_content == invocation.structured_content
    message = CASES[outcome].get("error")
    match outcome:
        case Out.success:
            assert mapped.text == "hello"
        case Out.tool_error:
            assert mapped.text == "tool failed"
        case Out.denied:
            assert mapped.text == "Permission denied by the host: no prompter and no matching rule"
        case _:
            assert mapped.text == f"Tool call failed ({outcome.value}): {message}"
    assert ARG_SECRET not in mapped.text


def test_success_without_text_uses_structured_json() -> None:
    """REQ-TLS-005: a success carrying only structuredContent → its compact JSON."""
    mapped = outcome_of(result(Out.success, structured={"value": 42, "ok": "é"}))
    assert mapped.text == '{"value":42,"ok":"é"}'
    assert json.loads(mapped.text) == {"value": 42, "ok": "é"}
    assert mapped.structured_content == {"value": 42, "ok": "é"}
    assert outcome_of(result(Out.success)).text == ""


def test_text_wins_over_structured() -> None:
    """REQ-TLS-005: when both are present the text blocks are what the agent sees."""
    mapped = outcome_of(result(Out.success, text="42", structured={"value": 42}))
    assert (mapped.text, mapped.structured_content) == ("42", {"value": 42})


def test_empty_tool_error_has_a_default_text() -> None:
    """REQ-TLS-005: an isError result without text blocks still tells the agent."""
    assert outcome_of(result(Out.tool_error)).text == "The tool reported an error"


def test_missing_error_object_still_yields_text() -> None:
    """REQ-TLS-005: defensive — an error outcome without an InvocationError never raises."""
    assert outcome_of(result(Out.denied)).text.startswith("Permission denied by the host: ")
    assert outcome_of(result(Out.timeout)).text.startswith("Tool call failed (timeout): ")


@pytest.mark.parametrize("outcome", list(InvocationOutcome))
async def test_call_never_raises_and_logs_no_arguments(
    outcome: InvocationOutcome, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-TLS-005, REQ-NFR-010: Toolset.call returns an outcome for every invocation
    outcome; a seeded secret-like argument value reaches the Invoker but no outcome text,
    no repr and no log record (Art. 5)."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    rig.invoker.reply = result(outcome, **CASES[outcome])
    caplog.set_level(logging.DEBUG, logger="reflex_mcphost")
    mapped = await rig.toolset().call("srv__echo", {"text": ARG_SECRET})
    assert rig.invoker.calls[0][1] == {"text": ARG_SECRET}
    assert mapped.is_error is (outcome is not Out.success)
    assert ARG_SECRET not in mapped.text
    assert ARG_SECRET not in repr(mapped)
    assert ARG_SECRET not in caplog.text
    assert any("srv__echo" in r.getMessage() for r in caplog.records)
