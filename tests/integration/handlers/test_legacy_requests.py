"""Legacy server-initiated requests through the Host handlers (F4-05; REQ-HSH-045/047),
legacy fixture Server over stdio and Streamable HTTP (marker ``interop``)."""

from __future__ import annotations

import json

import anyio
import pytest

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.consent.models import ConsentDecision, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.prompter import ElicitationResponse
from reflex_mcphost.core.types import Consumer
from tests.integration.handlers.helpers import (
    FakeProvider,
    HandlerPrompter,
    handler_host,
    legacy_table,
)
from tests.integration.test_host import TIMEOUT, Setup, setup
from tests.integration.test_invoker import BOT

pytestmark = [pytest.mark.anyio, pytest.mark.interop]
Out = InvocationOutcome
NAME = ElicitationResponse(action="accept", content={"name": "Ada"})
__all__ = ["legacy_table", "setup"]


async def _grant_sampling(host: Host) -> None:
    await host.consent.grant(
        effect="allow", operation=OperationKind.SAMPLING, target=ConsentTarget.server("srv")
    )


async def _sampling_rows(host: Host) -> list[tuple[Out, str | None, str, Consumer]]:
    page = await host.audit.query(AuditQuery(operation=OperationKind.SAMPLING))
    return [(r.outcome, r.error_code, r.attribution, r.consumer) for r in page.records]


async def _with_busy_call(host: Host, fqn: str) -> str:
    """Run ``fqn`` while another call is in flight on the same Server (→ unattributed)."""
    supervisor = host.connections.supervisor("srv")
    busy = host.invoker.start("srv::slow", {"seconds": 30}, consumer=BOT)
    with anyio.fail_after(TIMEOUT):
        while len(supervisor.inflight) < 1:  # noqa: ASYNC110 - bounded poll of a counter
            await anyio.sleep(0.01)
        result = await host.invoker.call(fqn, {}, consumer=BOT)
    await busy.cancel()
    assert result.ok, result.error
    return result.text


async def test_single_call_is_attributed(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-045, REQ-HSH-001, REQ-HSH-003, REQ-HSH-010: with one call in flight the
    server-initiated elicitation and sampling run through the same handlers, attributed to
    that call (stored sampling allow applies, audit `exact` with the call's Consumer)."""
    prompter = HandlerPrompter([NAME])
    provider = FakeProvider("Hi!")
    async with handler_host(
        setup, {"srv": legacy_table}, prompter=prompter, provider=provider
    ) as host:
        await _grant_sampling(host)
        named = await host.invoker.call("srv::ask_name", {}, consumer=BOT)
        sampled = await host.invoker.call("srv::sample", {}, consumer=BOT)
        rows = await _sampling_rows(host)
    assert (named.outcome, named.text) == (Out.success, "hello Ada")
    assert prompter.elicited[0].attribution == "exact"
    assert (sampled.outcome, sampled.text) == (Out.success, "Hi!")
    assert rows == [(Out.success, None, "exact", BOT)]
    assert prompter.sampling_asks() == []


async def test_concurrent_calls_are_unattributed(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-047: with two calls in flight a sampling request is unattributed: the stored
    allow is ignored, the human Consumer is asked once-only, the audit says unattributed;
    an elicitation is shown as unattributed."""
    prompter = HandlerPrompter([NAME])
    async with handler_host(
        setup, {"srv": legacy_table}, prompter=prompter, provider=FakeProvider("Hi!")
    ) as host:
        await _grant_sampling(host)
        assert await _with_busy_call(host, "srv::sample") == "Hi!"
        assert await _with_busy_call(host, "srv::ask_name") == "hello Ada"
        rows = await _sampling_rows(host)
    (asked,) = prompter.sampling_asks()
    assert (asked.attribution, asked.consumer) == ("unattributed", Consumer.human())
    assert rows == [(Out.success, None, "unattributed", Consumer.human())]
    assert prompter.elicited[0].attribution == "unattributed"


async def test_unattributed_without_prompter_is_denied(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-047, REQ-HSH-005: an unattributed sampling request with no prompter is
    denied (the Server receives the JSON-RPC error) and audited unattributed."""
    async with handler_host(setup, {"srv": legacy_table}, provider=FakeProvider()) as host:
        await _grant_sampling(host)
        text = await _with_busy_call(host, "srv::sample")
        rows = await _sampling_rows(host)
    assert text == "sampling error -1: Sampling request rejected by host"
    assert rows == [(Out.denied, "sampling_denied", "unattributed", Consumer.human())]


async def test_legacy_sampling_deny_overrides_the_outcome(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-005 (TD Flow C note): an attributed sampling denial ends the tool call
    denied / sampling_denied whatever CallToolResult the Server returns."""
    prompter = HandlerPrompter(decision=ConsentDecision.DENY)
    async with handler_host(
        setup, {"srv": legacy_table}, prompter=prompter, provider=FakeProvider()
    ) as host:
        result = await host.invoker.call("srv::sample", {}, consumer=BOT)
        record = await host.audit.get(result.id)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "sampling_denied"
    assert (record.outcome, record.error_code) == (Out.denied, "sampling_denied")


async def test_attributed_prompter_wait_not_counted(setup: Setup, legacy_table: str) -> None:
    """DD-004 (legacy, REQ-HSH-045): an attributed prompter wait pauses the call's
    deadline, so a slow human does not time the tool out."""
    prompter = HandlerPrompter([NAME], wait_s=1.0)
    async with handler_host(setup, {"srv": legacy_table}, prompter=prompter) as host:
        result = await host.invoker.call("srv::ask_name", {}, consumer=BOT, timeout=0.6)
    assert (result.outcome, result.text) == (Out.success, "hello Ada")


async def test_bad_schema_and_url_are_jsonrpc_errors(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-012 (legacy), REQ-HSH-015: a bad schema is answered with JSON-RPC -32602 and
    nobody is prompted; a url-mode elicitation shows the full URL."""
    prompter = HandlerPrompter([ElicitationResponse(action="decline")])
    target = "https://auth.example.com/connect?x=1"
    async with handler_host(setup, {"srv": legacy_table}, prompter=prompter) as host:
        bad = await host.invoker.call("srv::elicit_bad_schema", {}, consumer=BOT)
        assert prompter.elicited == []
        url = await host.invoker.call("srv::elicit_url", {"url": target}, consumer=BOT)
    assert bad.text == "elicit error -32602: Unsupported elicitation schema"
    assert url.text == "decline"
    assert (prompter.elicited[0].mode, prompter.elicited[0].url) == ("url", target)


async def test_set_roots_sends_list_changed(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-020, REQ-HSH-021: a legacy Server gets its configured roots and, after
    set_roots, notifications/roots/list_changed and the new roots."""
    table = legacy_table + '\nroots = ["file:///data/a"]'
    async with handler_host(setup, {"srv": table}) as host:
        before = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
        await host.connections.set_roots("srv", ["file:///data/b"])
        with anyio.fail_after(TIMEOUT):
            # the notification is one-way: poll the Server's counter (bounded by fail_after)
            while (await host.invoker.call("srv::roots_changed", {}, consumer=BOT)).text != "1":  # noqa: ASYNC110
                await anyio.sleep(0.05)
        after = await host.invoker.call("srv::list_roots", {}, consumer=BOT)
    assert json.loads(before.text) == ["file:///data/a"]
    assert json.loads(after.text) == ["file:///data/b"]


async def test_set_log_level_sends_set_level(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-030, REQ-HSH-031 (legacy): set_log_level sends logging/setLevel; the
    Server's notifications/message then land in logs()."""
    async with handler_host(setup, {"srv": legacy_table}) as host:
        assert (await host.invoker.call("srv::log_level", {}, consumer=BOT)).text == "none"
        await host.connections.set_log_level("srv", "debug")
        level = await host.invoker.call("srv::log_level", {}, consumer=BOT)
        await host.invoker.call("srv::log", {"data": "legacy line"}, consumer=BOT)
        with anyio.fail_after(TIMEOUT):
            while not [e for e in host.connections.logs("srv") if e.logger == "fixture"]:  # noqa: ASYNC110 - bounded poll
                await anyio.sleep(0.02)
        (entry,) = [e for e in host.connections.logs("srv") if e.logger == "fixture"]
    assert level.text == "debug"
    assert (entry.level, entry.text) == ("info", "legacy line")


async def test_definition_log_level_set_at_connect(setup: Setup, legacy_table: str) -> None:
    """REQ-HSH-031 (legacy): a definition log_level is sent with logging/setLevel after
    initialize."""
    async with handler_host(setup, {"srv": legacy_table + '\nlog_level = "warning"'}) as host:
        level = await host.invoker.call("srv::log_level", {}, consumer=BOT)
    assert level.text == "warning"
