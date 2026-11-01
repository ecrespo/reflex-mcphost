"""MCP App view models: which invocations render a View and the frame's notices (F7-04;
API §7 "Rendering", delta D2; REQ-APP-001/011/013/014/015)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from reflex_mcphost.core.apps.csp import build_csp
from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.consent.outcome import ConsentOutcome
from reflex_mcphost.core.invoker import InvocationResult
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import models
from tests.unit.core.consent.factories import tool_entry

NOW = datetime(2026, 11, 1, tzinfo=UTC)
RENDERED = {"success", "tool_error", "cancelled", "timeout", "connection_lost"}
CONSENTS: dict[str, ConsentOutcome | None] = {
    "allowed": ConsentOutcome(allowed=True, source="prompt"),
    "denied": ConsentOutcome(allowed=False, source="prompt"),
    "none": None,
}
CONSUMERS = {
    "human:ui": Consumer.human(),
    "human:other": Consumer.human("other"),
    "agent:helper": Consumer.agent("helper"),
    "app:srv": Consumer.app("srv"),
}


def ui_tool(uri: str | None = "ui://view") -> ToolEntry:
    return tool_entry("srv::view").model_copy(update={"ui_resource_uri": uri})


def result(
    outcome: InvocationOutcome, consent: ConsentOutcome | None, consumer: Consumer
) -> InvocationResult:
    return InvocationResult(
        id="inv-1",
        fqn=Fqn.parse("srv::view"),
        consumer=consumer,
        outcome=outcome,
        consent=consent,
        started_at=NOW,
        duration_ms=1,
    )


@pytest.mark.parametrize("outcome", list(InvocationOutcome))
@pytest.mark.parametrize("consent", list(CONSENTS))
@pytest.mark.parametrize("consumer", list(CONSUMERS))
def test_should_render_table(outcome: InvocationOutcome, consent: str, consumer: str) -> None:
    """REQ-APP-001 (delta D2): only a ``human:ui`` invocation of a tool with a UI resource
    whose consent allowed it and whose outcome is one of the five rendered outcomes renders
    a View; agents, Apps, denied or unevaluated consent and other outcomes never do."""
    who = CONSUMERS[consumer]
    rendered = models.should_render(result(outcome, CONSENTS[consent], who), ui_tool(), who)
    expected = consumer == "human:ui" and consent == "allowed" and outcome.value in RENDERED
    assert rendered is expected


def test_should_render_needs_a_ui_resource_and_the_same_consumer() -> None:
    """REQ-APP-001: a tool without ``ui_resource_uri`` renders nothing; the result's own
    Consumer must be ``human:ui`` too (not only the caller's claim)."""
    allowed = CONSENTS["allowed"]
    human = Consumer.human()
    assert not models.should_render(
        result(InvocationOutcome.success, allowed, human), ui_tool(None), human
    )
    agent_result = result(InvocationOutcome.success, allowed, Consumer.agent("helper"))
    assert not models.should_render(agent_result, ui_tool(), human)


def test_disabled_and_not_rendered_notices() -> None:
    """REQ-APP-013, DD-012: the disabled notice names its reason; each not-rendered reason
    is named in its notice; rows are JSON."""
    disabled = models.app_view_row(status="disabled", reason="remote_mode")
    assert disabled["status"] == "disabled"
    assert disabled["notices"] == ["MCP Apps are disabled: remote_mode"]
    for reason in (
        "missing_content",
        "mime_mismatch",
        "invalid_encoding",
        "too_large",
        "read_failed",
    ):
        row = models.app_view_row(status="not_rendered", reason=reason)
        [notice] = row["notices"]
        assert reason in notice
        assert row["src"] == ""
        json.dumps(row)


def test_rendered_row_external_and_dropped_notices_are_plain_text() -> None:
    """REQ-APP-011, REQ-APP-014: declared external domains and dropped CSP entries are
    listed in notices as plain text (a ``<b>`` in an entry appears literally); the row
    carries the proxy URL, origin, clamped height and border."""
    csp = build_csp(
        {"connectDomains": ["https://api.example.com"], "resourceDomains": ["<b>bold</b>", "*"]}
    )
    row = models.app_view_row(
        status="rendered",
        view_id="v1",
        src="http://localhost:9/proxy?view=v1",
        origin="http://localhost:9",
        csp=csp,
        border=False,
    )
    assert (row["view_id"], row["src"], row["origin"]) == (
        "v1",
        "http://localhost:9/proxy?view=v1",
        "http://localhost:9",
    )
    assert row["border"] is False
    assert models.APP_MIN_HEIGHT <= row["height"] <= models.APP_MAX_HEIGHT
    external, dropped = row["notices"]
    assert "https://api.example.com" in external
    assert "<b>bold</b>" in dropped
    assert "resourceDomains: *" in dropped
    assert row["link"] == ""
    json.dumps(row)


def test_rendered_row_without_external_domains_has_no_notice() -> None:
    """REQ-APP-011: the default policy (nothing declared) shows no notice."""
    row = models.app_view_row(
        status="rendered", view_id="v", src="s", origin="o", csp=build_csp(None)
    )
    assert row["notices"] == []


def test_link_confirmation_text_shows_the_full_url() -> None:
    """REQ-APP-015: a pending ``ui/open-link`` shows the full URL as plain text."""
    url = "https://example.com/a?b=<i>c</i>#frag"
    row = models.app_view_row(
        status="rendered", view_id="v", src="s", origin="o", csp=build_csp(None), link=url
    )
    assert row["link"] == url
    assert "new browser tab" in row["link_text"]


def test_height_is_clamped() -> None:
    """API §7 ``size-changed``: the frame height stays within 80…4,000 px."""
    for asked, shown in ((10, 80), (500, 500), (10**6, 4000)):
        row = models.app_view_row(
            status="rendered", view_id="v", src="s", origin="o", csp=build_csp(None), height=asked
        )
        assert row["height"] == shown


@pytest.mark.parametrize(
    ("url", "openable"),
    [
        ("https://example.com/" + "a" * 2000, True),
        ("https://example.com/" + "a" * 2100, False),
        ("https://exa mple.com/", False),
        ("https://example.com/\nx", False),
    ],
)
def test_is_openable_url_length_and_whitespace(url: str, openable: bool) -> None:
    """REQ-APP-015 (API §7 ``ui/open-link``): at most 2,048 characters, no whitespace or
    control characters."""
    assert models.is_openable_url(url) is openable
