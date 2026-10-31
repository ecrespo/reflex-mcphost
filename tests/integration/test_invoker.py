"""``Invoker`` pipeline against the fixture Servers, both eras (F3-08; API §4.8, Flow C).

Every Host runs on a fake home and a tmp data dir (see ``test_host``). The helpers here are
reused by ``test_invoker_deadlines`` and ``test_mrtr``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.audit import AuditQuery, AuditRecord, AuditUnavailableError
from reflex_mcphost.core.config.models import HostConfig
from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentTarget,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.errors import HostClosedError
from reflex_mcphost.core.events import (
    HostEvent,
    InvocationFinished,
    InvocationProgress,
    InvocationStarted,
)
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome, InvocationResult
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)
from reflex_mcphost.core.types import Consumer, Fqn
from tests.integration.test_host import MODERN_TABLE, TIMEOUT, Setup, _toml_server, setup

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
BOT = Consumer.agent("bot")
__all__ = ["setup"]  # re-exported fixture


@dataclass
class FakePrompter:
    """Answers every consent prompt with ``decision`` after ``delay`` seconds."""

    decision: ConsentDecision = ConsentDecision.ALLOW_ONCE
    delay: float = 0.0
    asked: list[ConsentRequest] = field(default_factory=list)
    supports_elicitation: bool = False

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        await anyio.sleep(self.delay)
        return ConsentAnswer(decision=self.decision)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        return SamplingVerdict(approved=False)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        return ElicitationResponse(action="decline")

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False


def http_table(url: str, **extra: Any) -> str:
    lines = ['transport = "http"', f"url = {json.dumps(url)}"]
    lines += [f"{k} = {json.dumps(v)}" for k, v in extra.items()]
    return "\n".join(lines)


def stdio_table(params: StdioServerParameters, **extra: Any) -> str:
    return _toml_server([params.command, *params.args], **extra)


def write_config(
    setup: Setup, servers: dict[str, str], host_lines: Sequence[str] = ()
) -> HostConfig:
    path = setup.write(servers)
    text = path.read_text(encoding="utf-8")
    if host_lines:
        text = text.replace("[host]\n", "[host]\n" + "\n".join(host_lines) + "\n", 1)
        path.write_text(text, encoding="utf-8")
    return HostConfig.load(path, cwd=setup.files.cwd, env=setup.env)


async def wait_tools(host: Host, server_id: str) -> None:
    """The Server is ready and its tools are in the catalog."""
    await host.connections.wait_ready(server_id, timeout=TIMEOUT)
    with anyio.fail_after(TIMEOUT):
        while not [t for t in host.catalog.tools(server_id) if not t.stale]:  # noqa: ASYNC110 - bounded poll (fail_after) of state that has no event
            await anyio.sleep(0.02)


@asynccontextmanager
async def running_host(
    setup: Setup,
    servers: dict[str, str],
    *,
    prompter: Any = None,
    allow: bool = True,
    host_lines: Sequence[str] = (),
) -> AsyncIterator[Host]:
    """A started Host with every Server ready; ``allow`` grants an any-Consumer server rule."""
    host = setup.host(write_config(setup, servers, host_lines), prompter=prompter)
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            for server_id in servers:
                await wait_tools(host, server_id)
                if allow:
                    await host.consent.grant(
                        effect="allow",
                        operation=OperationKind.TOOL_CALL,
                        target=ConsentTarget.server(server_id),
                    )
            yield host


async def records(host: Host, **filters: Any) -> list[AuditRecord]:
    return list((await host.audit.query(AuditQuery(**filters))).records)


async def call_counts(host: Host, server_id: str) -> dict[str, int]:
    result = await host.invoker.call(f"{server_id}::call_counts", {}, consumer=BOT)
    assert result.ok, result.error
    counts: dict[str, int] = json.loads(result.text)
    return counts


@pytest.fixture(params=["modern", "legacy"])
def era_table(request: pytest.FixtureRequest) -> str:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return stdio_table(params)
    return MODERN_TABLE


@pytest.fixture
async def host(setup: Setup, era_table: str) -> AsyncIterator[Host]:
    async with running_host(setup, {"srv": era_table}) as h:
        yield h


# ------------------------------------------------------------------ success


async def test_success_returns_content_and_structured_content(host: Host) -> None:
    """REQ-INV-001, REQ-INV-006, REQ-INV-014, REQ-INV-009: an allowed call reaches tools/call
    on the owning Client and returns text and structuredContent (legacy results carry no
    resultType and count as complete); exactly one audit record with hash, decision source,
    rule, duration and 0 input rounds."""
    result = await host.invoker.call("srv::structured", None, consumer=BOT)
    assert result.outcome is Out.success, result.error
    assert result.ok
    assert result.structured_content == {"value": 42}
    assert json.loads(result.text) == {"value": 42}
    assert result.consent is not None
    assert result.consent.source == "rule"
    assert result.id.startswith("inv_")
    (record,) = await records(host, fqn=result.fqn)
    assert record.invocation_id == result.id
    assert record.outcome is Out.success
    assert record.consumer == BOT
    assert record.decision_source == "rule"
    assert record.rule_id == result.consent.rule_id
    assert record.args_hash is not None
    assert record.args_hash.startswith("hmac-sha256:")
    assert record.input_rounds == 0
    assert record.duration_ms is not None
    assert record.duration_ms >= 0
    assert record.error_code is None


async def test_text_result(host: Host) -> None:
    """REQ-INV-001: the given arguments reach the Server."""
    result = await host.invoker.call("srv::echo", {"text": "héllo"}, consumer=BOT)
    assert (result.outcome, result.text) == (Out.success, "héllo")


async def test_invalid_arguments_rejected_before_consent(setup: Setup, era_table: str) -> None:
    """REQ-INV-002, REQ-INV-009: arguments failing inputSchema are rejected with the
    validation errors, no consent prompt is shown and one audit record is written."""
    prompter = FakePrompter()
    async with running_host(setup, {"srv": era_table}, prompter=prompter, allow=False) as host:
        result = await host.invoker.call("srv::echo", {"text": 5}, consumer=BOT)
        assert result.outcome is Out.invalid_arguments
        assert result.error is not None
        issues = result.error.details["issues"]
        assert issues
        assert issues[0]["path"] == "/text"
        assert prompter.asked == []
        (record,) = await records(host)
        assert (record.outcome, record.decision_source) == (Out.invalid_arguments, "not_evaluated")
        assert record.error_code == "invalid_arguments"
        not_object = await host.invoker.call("srv::echo", ["x"], consumer=BOT)  # type: ignore[arg-type]
        assert not_object.outcome is Out.invalid_arguments
        assert prompter.asked == []


async def test_tool_error(host: Host) -> None:
    """REQ-INV-008: isError: true → tool_error with the content, distinct from protocol
    errors."""
    result = await host.invoker.call("srv::tool_error", {}, consumer=BOT)
    assert result.outcome is Out.tool_error
    assert "tool failed" in result.text
    assert result.error is not None
    assert result.error.code == "tool_error"
    (record,) = await records(host, outcome=Out.tool_error)
    assert record.error_code == "tool_error"


async def test_bad_structured_content_is_protocol_error(host: Host) -> None:
    """REQ-INV-007: structuredContent violating the declared outputSchema → protocol_error
    with the validation detail."""
    result = await host.invoker.call("srv::bad_structured", {}, consumer=BOT)
    assert result.outcome is Out.protocol_error
    assert result.error is not None
    assert "not of type 'integer'" in result.error.message
    assert result.structured_content is None
    (record,) = await records(host, outcome=Out.protocol_error)
    assert record.fqn == result.fqn


# ------------------------------------------------------------------ early failures


async def test_early_failures_are_audited_once(setup: Setup) -> None:
    """REQ-INV-009, REQ-CNS-001, REQ-CNS-010, REQ-CAT-004: unknown tool → not_found; stopped
    Server → server_unavailable; headless Host without a rule → denied; each attempt writes
    exactly one record and none reaches the Server."""
    async with running_host(setup, {"srv": MODERN_TABLE}, allow=False) as host:
        missing = await host.invoker.call("srv::nope", {}, consumer=BOT)
        ghost = await host.invoker.call("ghost::echo", {}, consumer=BOT)
        assert (missing.outcome, ghost.outcome) == (Out.not_found, Out.not_found)
        denied = await host.invoker.call("srv::echo", {"text": "x"}, consumer=BOT)
        assert denied.outcome is Out.denied
        assert denied.consent is not None
        assert denied.consent.source == "no_prompter"
        assert denied.error is not None
        assert denied.error.code == "consent_denied"
        await host.consent.grant(
            effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.global_()
        )
        assert "echo" not in await call_counts(host, "srv")  # the denied call never arrived
        await host.connections.disconnect("srv")
        down = await host.invoker.call("srv::echo", {"text": "x"}, consumer=BOT)
        assert down.outcome is Out.server_unavailable
        rows = await records(host)
        by_id = {r.invocation_id: r for r in rows}
        for result in (missing, ghost, denied, down):
            assert by_id[result.id].outcome is result.outcome
        assert by_id[denied.id].decision_source == "no_prompter"
        assert len(rows) == 5  # four attempts + the call_counts call


async def test_audit_begin_failure_sends_nothing(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-INV-012, Art. 3: if the audit record cannot be written the call is not sent:
    outcome audit_unavailable, no record, and the fixture records no call."""
    real_begin = host.audit.begin

    async def failing_begin(**kwargs: Any) -> str:
        raise AuditUnavailableError(kwargs["invocation_id"], "OperationalError")

    monkeypatch.setattr(host.audit, "begin", failing_begin)
    result = await host.invoker.call("srv::echo", {"text": "x"}, consumer=BOT)
    assert result.outcome is Out.audit_unavailable
    assert result.error is not None
    assert result.error.code == "audit_unavailable"
    monkeypatch.setattr(host.audit, "begin", real_begin)
    assert "echo" not in await call_counts(host, "srv")
    assert [r.fqn for r in await records(host)] == [
        r.fqn for r in await records(host, outcome=Out.success)
    ]
    assert result.id not in {r.invocation_id for r in await records(host)}


async def test_early_record_failure_is_audit_unavailable(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Art. 3, REQ-INV-009: an attempt whose single record cannot be written ends
    audit_unavailable."""

    async def failing_record(**kwargs: Any) -> str:
        raise AuditUnavailableError(kwargs["invocation_id"], "OperationalError")

    monkeypatch.setattr(host.audit, "record", failing_record)
    result = await host.invoker.call("srv::nope", {}, consumer=BOT)
    assert result.outcome is Out.audit_unavailable
    assert result.error is not None
    assert result.error.details == {"attempted_outcome": "not_found"}


async def test_finish_failure_still_returns_result(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API §4.8 step 9: a failing finalize is logged and the result still returned."""

    async def failing_finish(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(host.audit, "finish", failing_finish)
    result = await host.invoker.call("srv::echo", {"text": "kept"}, consumer=BOT)
    assert (result.outcome, result.text) == (Out.success, "kept")


# ------------------------------------------------------------------ concurrency, events


async def test_twenty_concurrent_calls_on_one_server(host: Host) -> None:
    """REQ-INV-013: 20 concurrent invocations on the same Server all succeed and overlap."""
    results: list[InvocationResult] = []

    async def one(i: int) -> None:
        results.append(await host.invoker.call("srv::slow", {"seconds": 0.5}, consumer=BOT))

    started = anyio.current_time()
    async with anyio.create_task_group() as tg:
        for i in range(20):
            tg.start_soon(one, i)
    elapsed = anyio.current_time() - started
    assert [r.outcome for r in results] == [Out.success] * 20
    assert len({r.id for r in results}) == 20
    assert elapsed < 20 * 0.5 / 2  # they ran concurrently
    assert len(await records(host, outcome=Out.success, limit=500)) == 20


async def test_start_handle_and_events(host: Host) -> None:
    """REQ-INV-005 (events), API §4.8, §4.11: start() schedules the call; the handle yields
    the result; invocation.started and invocation.finished are published once each."""
    seen: list[HostEvent] = []
    async with host.events.subscribe() as events:
        handle = host.invoker.start("srv::echo", {"text": "via handle"}, consumer=BOT)
        result = await handle.result()
        with anyio.fail_after(TIMEOUT):
            async for event in events:
                if isinstance(event, InvocationStarted | InvocationFinished | InvocationProgress):
                    seen.append(event)
                if isinstance(event, InvocationFinished) and event.invocation_id == handle.id:
                    break
    assert (result.id, result.text) == (handle.id, "via handle")
    mine = [e for e in seen if getattr(e, "invocation_id", None) == handle.id]
    assert [e.type for e in mine] == ["invocation.started", "invocation.finished"]
    finished = mine[-1]
    assert isinstance(finished, InvocationFinished)
    assert finished.outcome is Out.success
    assert finished.consumer == BOT
    assert [p async for p in handle.progress()] == []


async def test_invoker_lifecycle(setup: Setup) -> None:
    """API §4.1: host.invoker and host.audit exist only between start() and aclose()."""
    host = setup.host(write_config(setup, {}))
    for member in ("invoker", "audit"):
        with pytest.raises(RuntimeError):
            getattr(host, member)
    async with host:
        invoker = host.invoker
        assert host.audit is not None
        with pytest.raises(ValueError, match="invalid FQN"):
            await invoker.call("no-separator", {}, consumer=BOT)
        with pytest.raises(ValueError, match="timeout"):
            await invoker.call("a::b", {}, consumer=BOT, timeout=0)
    with pytest.raises(HostClosedError):
        host.invoker  # noqa: B018 - property access is the assertion
    with pytest.raises(HostClosedError):
        await invoker.call("a::b", {}, consumer=BOT)
    with pytest.raises(HostClosedError):
        invoker.start("a::b", {}, consumer=BOT)


async def test_interrupted_records_marked_at_start(setup: Setup) -> None:
    """DM §2.3, API §4.1: a record left in progress by a previous process is finished as
    connection_lost / host_interrupted when the next Host starts."""
    config = write_config(setup, {})
    async with setup.host(config) as host:
        dangling = await host.audit.begin(
            consumer=BOT, decision_source="rule", fqn=Fqn.parse("srv::echo"), arguments={}
        )
    async with setup.host(config) as again:
        record = await again.audit.get(dangling)
    assert (record.outcome, record.error_code) == (Out.connection_lost, "host_interrupted")
