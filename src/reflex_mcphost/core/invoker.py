"""``Invoker``: the single effect path for ``tools/call`` (API §4.8, Tech Design Flow C, Art. 3).

Pipeline (normative order, API §4.8):

1. resolve the FQN in the catalog (``not_found``);
2. the Server is ``ready`` and the entry is not stale (``server_unavailable``);
3. validate the arguments against ``input_schema`` — no consent prompt on failure
   (``invalid_arguments``, REQ-INV-002);
4. ``ConsentPolicy.evaluate`` once per invocation (``denied``, REQ-CNS-001, REQ-HSH-042);
5. ``AuditLog.begin`` — on failure nothing is sent (``audit_unavailable``, REQ-INV-012);
6. ``tools/call`` through the MRTR loop (``core.mrtr``) under a pausable deadline that
   covers every leg and backoff but not the time handlers spend waiting on the
   ``Prompter`` (DD-004); progress is forwarded (REQ-INV-005); a broken stream ends
   ``connection_lost`` and is never re-issued (REQ-INV-015);
7. validate ``structuredContent`` against ``output_schema`` (``protocol_error``,
   REQ-INV-007);
8. ``isError: true`` → ``tool_error`` (REQ-INV-008);
9. ``AuditLog.finish`` — a failure is logged and the result still returned (REQ-INV-009).

On a legacy connection every ``tools/call`` leg is registered in the supervisor's
``InFlightRegistry`` with its deadline, so server-initiated requests can be attributed to it
(REQ-HSH-045/047) and an attributed prompter wait pauses the budget (DD-004 legacy); a
sampling request refused for this call ends it ``denied/sampling_denied`` whatever
``CallToolResult`` the Server returns (Tech Design Flow C note).

Every attempt yields exactly one audit record (REQ-INV-009): steps 1-4 failures are written
with ``AuditLog.record``; allowed calls are begun at step 5 and finished at step 9. An attempt
whose record cannot be written ends ``audit_unavailable`` with no record (Art. 3). Expected
failures are outcomes, never exceptions (API §3.4). If the task awaiting ``call()`` is
cancelled, the request is cancelled through the SDK (stdio / legacy HTTP:
``notifications/cancelled``; modern HTTP: the response stream is closed), the record is
finished ``cancelled`` and the cancellation re-raised (REQ-INV-004). Invocations are
independent and unlimited in number, on one Server too (REQ-INV-013).
"""

from __future__ import annotations

import inspect
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
import httpx2
import mcp_types as types
from anyio.abc import TaskGroup
from mcp import Client, MCPError
from pydantic import BaseModel, Field, ValidationError

from reflex_mcphost.core.audit import AuditLog, AuditUnavailableError
from reflex_mcphost.core.catalog import Catalog, ToolEntry
from reflex_mcphost.core.config.models import ServerDefinition
from reflex_mcphost.core.connection.inflight import InFlightEntry, InFlightRegistry
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.consent.outcome import ConsentOutcome
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.errors import (
    HostClosedError,
    ServerUnavailableError,
    UnknownEntryError,
    UnknownServerError,
)
from reflex_mcphost.core.events import (
    EventBus,
    InvocationFinished,
    InvocationProgress,
    InvocationStarted,
)
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.mrtr import (
    InputHandlerError,
    InputRequestRouter,
    InputRoundsExceededError,
    LoopState,
    PausableDeadline,
    SamplingDeniedError,
    UndeclaredInputRequestError,
    call_tool_leg,
    drive,
)
from reflex_mcphost.core.outcomes import InvocationOutcome, Progress, ProgressCallback
from reflex_mcphost.core.schema import SchemaCompileError, SchemaValidation, compile_schema
from reflex_mcphost.core.secrets import redact
from reflex_mcphost.core.types import Consumer, Fqn, new_invocation_id

__all__ = [
    "DEFAULT_TOOL_TIMEOUT_S",
    "InvocationError",
    "InvocationHandle",
    "InvocationOutcome",
    "InvocationResult",
    "Invoker",
    "Progress",
    "ProgressCallback",
    "map_sdk_error",
]

DEFAULT_TOOL_TIMEOUT_S = 60.0
MAX_ERROR_MESSAGE = 500
_O = InvocationOutcome
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    anyio.EndOfStream,
    httpx2.TransportError,
    ConnectionError,
)
_log = get_logger(__name__)


class InvocationError(BaseModel, frozen=True):
    """Why an invocation did not succeed: a stable ``code`` (also the audit ``error_code``),
    a bounded message and details (validation issues, JSON-RPC error). Never carries argument
    values (Art. 5)."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class InvocationResult(BaseModel, frozen=True):
    """The outcome of one invocation attempt (API §4.8)."""

    id: str
    fqn: Fqn
    consumer: Consumer
    outcome: InvocationOutcome
    content: tuple[types.ContentBlock, ...] = ()
    structured_content: dict[str, Any] | None = None
    error: InvocationError | None = None
    consent: ConsentOutcome | None = None
    started_at: datetime
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.outcome is InvocationOutcome.success

    @property
    def text(self) -> str:
        """The text content blocks, concatenated (a convenience for agents)."""
        return "".join(b.text for b in self.content if isinstance(b, types.TextContent))


def _bounded(text: str) -> str:
    text = redact(text)
    return text if len(text) <= MAX_ERROR_MESSAGE else text[: MAX_ERROR_MESSAGE - 1] + "…"


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def map_sdk_error(exc: BaseException) -> tuple[InvocationOutcome, InvocationError]:
    """Classify a failure of pipeline step 6 (Tech Design §5.3, Flow C step 7)."""
    exc = _leaf(exc)
    if isinstance(exc, UndeclaredInputRequestError):
        return _O.protocol_error, InvocationError(
            code="undeclared_input_request",
            message=f"the Server sent an input request the Host did not declare ({exc.method})",
            details={"method": exc.method},
        )
    if isinstance(exc, InputRoundsExceededError):
        return _O.protocol_error, InvocationError(
            code="input_rounds_exceeded",
            message=f"the Server kept asking for input after {exc.rounds} rounds",
        )
    if isinstance(exc, SamplingDeniedError):
        return _O.denied, InvocationError(code="sampling_denied", message="sampling was denied")
    if isinstance(exc, InputHandlerError):
        return _O.protocol_error, InvocationError(
            code="input_handler_failed",
            message=f"an input request could not be fulfilled ({exc.method})",
            details={"method": exc.method, "reason": exc.reason},
        )
    if isinstance(exc, ServerUnavailableError):
        return _O.server_unavailable, InvocationError(
            code="server_unavailable", message=str(exc), details={"state": exc.state}
        )
    if isinstance(exc, MCPError):
        if exc.code == types.CONNECTION_CLOSED:
            return _O.connection_lost, InvocationError(
                code="connection_lost", message="the connection closed before the result"
            )
        # `data` is Server-controlled and untrusted: kept out of the result (Art. 5)
        return _O.protocol_error, InvocationError(
            code=str(exc.code),
            message=_bounded(exc.error.message),
            details={"code": exc.code},
        )
    if isinstance(exc, _TRANSPORT_ERRORS):
        return _O.connection_lost, InvocationError(
            code="connection_lost",
            message=f"the transport failed before the result ({type(exc).__name__})",
        )
    if isinstance(exc, RuntimeError | ValidationError):
        # session.call_tool: output-schema mismatch, unexpected InputRequiredResult (S-1)
        return _O.protocol_error, InvocationError(
            code="invalid_result", message=_bounded(str(exc).splitlines()[0] if str(exc) else "")
        )
    return _O.protocol_error, InvocationError(
        code="internal_error", message=f"unexpected failure ({type(exc).__name__})"
    )


class _Supervisor(Protocol):
    """What the Invoker needs from a ``ServerSupervisor`` (Host-internal)."""

    @property
    def status(self) -> ServerStatus: ...

    @property
    def definition(self) -> ServerDefinition: ...

    @property
    def inflight(self) -> InFlightRegistry: ...

    def lease(self) -> AbstractAsyncContextManager[Client]: ...


SupervisorLookup = Callable[[str], _Supervisor]


@dataclass
class _Invocation:
    """Mutable state of one attempt, shared by the pipeline and its handle."""

    id: str
    fqn: Fqn
    arguments: Mapping[str, Any] | None
    consumer: Consumer
    timeout: float | None
    reset_on_progress: bool | None
    on_progress: ProgressCallback | None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    t0: float = field(default_factory=time.perf_counter)
    consent: ConsentOutcome | None = None
    audit_started: bool = False
    loop: LoopState = field(default_factory=LoopState)
    progress: list[Progress] = field(default_factory=list)
    changed: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)
    result: InvocationResult | None = None
    scope: anyio.CancelScope | None = None
    cancel_requested: bool = False

    def notify(self) -> None:
        self.changed.set()
        self.changed = anyio.Event()

    @property
    def started_at_us(self) -> int:
        return int(self.started_at.timestamp() * 1_000_000)

    def duration_ms(self) -> int:
        return max(0, int((time.perf_counter() - self.t0) * 1000))


class InvocationHandle:
    """A scheduled invocation (API §4.8): its id, result, cancellation and progress."""

    def __init__(self, invocation: _Invocation) -> None:
        self._inv = invocation

    @property
    def id(self) -> str:
        return self._inv.id

    async def result(self) -> InvocationResult:
        """The result once the attempt ended (also after ``cancel()``)."""
        await self._inv.done.wait()
        if self._inv.result is None:  # pragma: no cover - done is set after the result
            raise RuntimeError("invocation ended without a result")
        return self._inv.result

    async def cancel(self) -> None:
        """Cancel the invocation (REQ-INV-004) and wait until it is recorded."""
        inv = self._inv
        inv.cancel_requested = True
        if inv.scope is not None:
            inv.scope.cancel()
        await inv.done.wait()

    async def progress(self) -> AsyncIterator[Progress]:
        """Every progress notification in arrival order, until the invocation ends."""
        seen = 0
        while True:
            inv = self._inv
            while seen < len(inv.progress):
                yield inv.progress[seen]
                seen += 1
            if inv.done.is_set():
                return
            await inv.changed.wait()


class Invoker:
    """Runs tool invocations through consent and audit (API §4.8)."""

    def __init__(
        self,
        *,
        catalog: Catalog,
        supervisor_of: SupervisorLookup,
        consent: ConsentPolicy,
        audit: AuditLog,
        events: EventBus,
        router: InputRequestRouter | None = None,
        task_group: TaskGroup | None = None,
        default_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
        reset_timeout_on_progress: bool = False,
    ) -> None:
        self._catalog = catalog
        self._supervisor_of = supervisor_of
        self._consent = consent
        self._audit = audit
        self._events = events
        self._router = router if router is not None else InputRequestRouter()
        self._task_group = task_group
        self._default_timeout_s = default_timeout_s
        self._reset_on_progress = reset_timeout_on_progress
        self._closed = False

    def close(self) -> None:
        """Refuse new invocations (Host shutdown); in-flight ones end through cancellation."""
        self._closed = True

    # ------------------------------------------------------------------ public

    async def call(
        self,
        fqn: Fqn | str,
        arguments: Mapping[str, Any] | None,
        *,
        consumer: Consumer,
        timeout: float | None = None,  # noqa: ASYNC109 - API §4.8: a budget that excludes prompter waits (DD-004), not a plain fail_after
        on_progress: ProgressCallback | None = None,
        reset_timeout_on_progress: bool | None = None,
    ) -> InvocationResult:
        """Invoke a tool and return its result; expected failures are outcomes."""
        inv = self._new(fqn, arguments, consumer, timeout, reset_timeout_on_progress, on_progress)
        return await self._run(inv)

    def start(
        self,
        fqn: Fqn | str,
        arguments: Mapping[str, Any] | None,
        *,
        consumer: Consumer,
        timeout: float | None = None,
        reset_timeout_on_progress: bool | None = None,
    ) -> InvocationHandle:
        """Schedule an invocation in the Host task group (sync)."""
        if self._task_group is None:
            raise RuntimeError("the Invoker has no task group (Host.start() has not run)")
        inv = self._new(fqn, arguments, consumer, timeout, reset_timeout_on_progress, None)
        self._task_group.start_soon(self._scheduled, inv, name=f"invocation:{inv.id}")
        return InvocationHandle(inv)

    # ------------------------------------------------------------------ attempt

    def _new(
        self,
        fqn: Fqn | str,
        arguments: Mapping[str, Any] | None,
        consumer: Consumer,
        timeout: float | None,
        reset: bool | None,
        on_progress: ProgressCallback | None,
    ) -> _Invocation:
        if self._closed:
            raise HostClosedError()
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be > 0")
        key = fqn if isinstance(fqn, Fqn) else Fqn.parse(fqn)
        return _Invocation(
            id=new_invocation_id(),
            fqn=key,
            arguments=arguments,
            consumer=consumer,
            timeout=timeout,
            reset_on_progress=reset,
            on_progress=on_progress,
        )

    async def _scheduled(self, inv: _Invocation) -> None:
        with anyio.CancelScope() as scope:
            inv.scope = scope
            if inv.cancel_requested:
                scope.cancel()
            await self._run(inv)

    async def _run(self, inv: _Invocation) -> InvocationResult:
        self._events.publish(
            InvocationStarted(invocation_id=inv.id, fqn=inv.fqn, consumer=inv.consumer)
        )
        try:
            return await self._pipeline(inv)
        except anyio.get_cancelled_exc_class():
            if inv.result is None:
                with anyio.CancelScope(shield=True):
                    await self._conclude(
                        inv,
                        _O.cancelled,
                        InvocationError(code="cancelled", message="the invocation was cancelled"),
                    )
            raise

    async def _pipeline(self, inv: _Invocation) -> InvocationResult:
        log = ContextAdapter(_log, invocation_id=inv.id, fqn=str(inv.fqn))
        # 1. resolve
        try:
            entry = self._catalog.get_tool(inv.fqn)
        except UnknownEntryError:
            return await self._conclude(
                inv, _O.not_found, InvocationError(code="not_found", message="no such tool")
            )
        # 2. Server ready
        try:
            supervisor = self._supervisor_of(entry.server_id)
            status = supervisor.status
        except (UnknownServerError, ServerUnavailableError) as exc:
            return await self._conclude(inv, *map_sdk_error(_unavailable(exc)))
        if status.state is not ServerState.READY or entry.stale:
            return await self._conclude(
                inv, *map_sdk_error(ServerUnavailableError(entry.server_id, status.state))
            )
        # 3. arguments
        arguments, problem = _validate_arguments(entry, inv.arguments)
        if problem is not None:
            return await self._conclude(inv, *problem)
        # 4. consent (may await the Prompter: not part of the call timeout, DD-004)
        consent = await self._evaluate(entry, arguments, inv, status, log)
        inv.consent = consent
        if not consent.allowed:
            return await self._conclude(
                inv,
                _O.denied,
                InvocationError(
                    code="consent_denied", message=consent.reason or "denied by consent"
                ),
            )
        # 5. audit begin
        try:
            with anyio.CancelScope(shield=True):
                await self._audit.begin(
                    consumer=inv.consumer,
                    decision_source=consent.source,
                    fqn=inv.fqn,
                    arguments=arguments,
                    rule_id=consent.rule_id,
                    invocation_id=inv.id,
                    started_at_us=inv.started_at_us,
                )
        except AuditUnavailableError as exc:
            return await self._conclude(inv, *_audit_unavailable(exc), write=False)
        inv.audit_started = True
        # 6. tools/call
        timeout = inv.timeout or supervisor.definition.timeout_s or self._default_timeout_s
        deadline = PausableDeadline(timeout)
        outcome = await self._call(inv, entry, arguments, supervisor, status, deadline)
        if isinstance(outcome, tuple):
            return await self._conclude(inv, *outcome)
        # 7.-8. classify the result
        return await self._conclude(inv, *_classify(entry, outcome), call_result=outcome)

    async def _evaluate(
        self,
        entry: ToolEntry,
        arguments: dict[str, Any],
        inv: _Invocation,
        status: ServerStatus,
        log: ContextAdapter,
    ) -> ConsentOutcome:
        info = status.server_info
        server_name = (info.title or info.name) if info is not None else entry.server_id
        try:
            request = await self._consent.tool_request(
                entry, arguments, consumer=inv.consumer, server_name=server_name or entry.server_id
            )
        except Exception as exc:
            log.error("consent request could not be built (%s); denying", type(exc).__name__)
            return ConsentOutcome(
                allowed=False,
                source="error",
                reason=f"consent request could not be built ({type(exc).__name__})",
            )
        return await self._consent.evaluate(request)

    async def _call(
        self,
        inv: _Invocation,
        entry: ToolEntry,
        arguments: dict[str, Any],
        supervisor: _Supervisor,
        status: ServerStatus,
        deadline: PausableDeadline,
    ) -> types.CallToolResult | tuple[InvocationOutcome, InvocationError]:
        reset = inv.reset_on_progress
        reset = self._reset_on_progress if reset is None else reset
        log = ContextAdapter(_log, invocation_id=inv.id, server_id=entry.server_id)

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            item = Progress(progress=progress, total=total, message=message)
            inv.progress.append(item)
            inv.notify()
            if reset:
                deadline.reset()
            self._events.publish(
                InvocationProgress(
                    invocation_id=inv.id, fqn=inv.fqn, consumer=inv.consumer, progress=item
                )
            )
            if inv.on_progress is not None:
                try:
                    pending = inv.on_progress(item)
                    if inspect.isawaitable(pending):
                        await pending
                except Exception as exc:
                    log.warning("progress callback failed (%s)", type(exc).__name__)

        result: types.Result | None = None
        tracked: InFlightEntry | None = None
        track: AbstractContextManager[InFlightEntry | None] = nullcontext()
        if status.era == "legacy":  # server-initiated requests attribute to this leg
            track = supervisor.inflight.track(
                consumer=inv.consumer, deadline=deadline, invocation_id=inv.id
            )
        try:
            async with supervisor.lease() as client:
                leg = call_tool_leg(
                    client,
                    entry.name,
                    arguments,
                    progress_callback=on_progress,
                    modern=status.era == "modern",
                )
                with track as tracked, deadline.running():
                    result = await drive(
                        leg,
                        router=self._router,
                        server_id=entry.server_id,
                        consumer=inv.consumer,
                        deadline=deadline,
                        state=inv.loop,
                        invocation_id=inv.id,
                    )
        except Exception as exc:
            if not deadline.expired:
                outcome, error = map_sdk_error(exc)
                if tracked is not None and tracked.sampling_denied and outcome is _O.protocol_error:
                    return _sampling_denied()
                if error.code in ("internal_error", "invalid_result"):
                    log.warning("tools/call failed (%s)", type(exc).__name__)
                return outcome, error
        if tracked is not None and tracked.sampling_denied and not deadline.expired:
            return _sampling_denied()
        if deadline.expired:
            return _O.timeout, InvocationError(
                code="timeout",
                message=f"no result within {_timeout_text(deadline)}",
                details={"timeout_s": deadline.budget_s},
            )
        if not isinstance(result, types.CallToolResult):
            return _O.protocol_error, InvocationError(
                code="invalid_result",
                message=f"unexpected tools/call result {type(result).__name__}",
            )
        return result

    # ------------------------------------------------------------------ finish

    async def _conclude(
        self,
        inv: _Invocation,
        outcome: InvocationOutcome,
        error: InvocationError | None = None,
        *,
        call_result: types.CallToolResult | None = None,
        write: bool = True,
    ) -> InvocationResult:
        """Write the attempt's single audit record, publish ``invocation.finished``, and
        hand the result to the handle."""
        duration = inv.duration_ms()
        if write:
            with anyio.CancelScope(shield=True):
                written = await self._write_audit(inv, outcome, error, duration)
            if not written:
                attempted = outcome.value
                outcome = _O.audit_unavailable
                error = InvocationError(
                    code="audit_unavailable",
                    message="the audit record could not be written; nothing was executed",
                    details={"attempted_outcome": attempted},
                )
                call_result = None
        result = InvocationResult(
            id=inv.id,
            fqn=inv.fqn,
            consumer=inv.consumer,
            outcome=outcome,
            content=tuple(call_result.content) if call_result is not None else (),
            structured_content=call_result.structured_content if call_result else None,
            error=error,
            consent=inv.consent,
            started_at=inv.started_at,
            duration_ms=duration,
        )
        inv.result = result
        self._events.publish(
            InvocationFinished(
                invocation_id=inv.id,
                fqn=inv.fqn,
                consumer=inv.consumer,
                outcome=outcome,
                duration_ms=duration,
            )
        )
        inv.done.set()
        inv.notify()
        return result

    async def _write_audit(
        self,
        inv: _Invocation,
        outcome: InvocationOutcome,
        error: InvocationError | None,
        duration: int,
    ) -> bool:
        """``False`` only when an early-failure record could not be written; a failing
        ``finish`` is logged by the ``AuditLog`` and the result still returned (step 9)."""
        code = error.code if error is not None else None
        if inv.audit_started:
            await self._audit.finish(
                inv.id,
                outcome=outcome,
                duration_ms=duration,
                input_rounds=inv.loop.rounds,
                error_code=code,
            )
            return True
        consent = inv.consent
        try:
            await self._audit.record(
                consumer=inv.consumer,
                outcome=outcome,
                duration_ms=duration,
                decision_source=consent.source if consent is not None else "not_evaluated",
                fqn=inv.fqn,
                arguments=inv.arguments if isinstance(inv.arguments, Mapping) else None,
                rule_id=consent.rule_id if consent is not None else None,
                error_code=code,
                input_rounds=inv.loop.rounds,
                invocation_id=inv.id,
                started_at_us=inv.started_at_us,
            )
        except AuditUnavailableError:
            return False  # logged at ERROR by the AuditLog (Art. 3)
        return True


def _sampling_denied() -> tuple[InvocationOutcome, InvocationError]:
    """A legacy sampling refusal for this call overrides whatever the Server returned."""
    return map_sdk_error(SamplingDeniedError("sampling_denied"))


def _timeout_text(deadline: PausableDeadline) -> str:
    return f"{deadline.budget_s:g} s" if deadline.budget_s is not None else "the deadline"


def _unavailable(exc: UnknownServerError | ServerUnavailableError) -> ServerUnavailableError:
    if isinstance(exc, ServerUnavailableError):
        return exc
    return ServerUnavailableError(exc.server_id, "unknown")


def _audit_unavailable(exc: AuditUnavailableError) -> tuple[InvocationOutcome, InvocationError]:
    return _O.audit_unavailable, InvocationError(
        code="audit_unavailable",
        message="the audit record could not be written; nothing was sent",
        details={"reason": exc.reason},
    )


def _issues(validation: SchemaValidation) -> dict[str, Any]:
    return {
        "issues": [issue.model_dump() for issue in validation.issues],
        "truncated": validation.truncated,
    }


def _validate_arguments(
    entry: ToolEntry, arguments: object
) -> tuple[dict[str, Any], tuple[InvocationOutcome, InvocationError] | None]:
    """Step 3 (REQ-INV-002): ``None`` validates as ``{}``; a schema the Host cannot compile
    is the Server's fault (``protocol_error``), nothing is sent."""
    if arguments is not None and not isinstance(arguments, Mapping):
        return {}, (
            _O.invalid_arguments,
            InvocationError(code="invalid_arguments", message="arguments must be an object"),
        )
    args: dict[str, Any] = dict(arguments) if isinstance(arguments, Mapping) else {}
    try:
        compiled = compile_schema(entry.input_schema, key=f"{entry.definition_hash}:input")
        validation = compiled.validate(args)
    except SchemaCompileError as exc:
        return args, (
            _O.protocol_error,
            InvocationError(
                code="invalid_input_schema",
                message=f"the tool's input schema is unusable: {exc.detail}",
            ),
        )
    if not validation.ok:
        return args, (
            _O.invalid_arguments,
            InvocationError(
                code="invalid_arguments",
                message="the arguments do not match the tool's input schema",
                details=_issues(validation),
            ),
        )
    return args, None


def _classify(
    entry: ToolEntry, result: types.CallToolResult
) -> tuple[InvocationOutcome, InvocationError | None]:
    """Steps 7-8. Error results are not validated against the output schema (MCP: the
    schema describes successful structured results)."""
    if result.is_error:
        error = InvocationError(code="tool_error", message="the tool reported an error")
        return _O.tool_error, error
    if entry.output_schema is None:
        return _O.success, None
    if result.structured_content is None:
        return _O.protocol_error, InvocationError(
            code="missing_structured_content",
            message="the tool declares an output schema but returned no structured content",
        )
    try:
        compiled = compile_schema(entry.output_schema, key=f"{entry.definition_hash}:output")
        validation = compiled.validate(result.structured_content)
    except SchemaCompileError as exc:
        return _O.protocol_error, InvocationError(
            code="invalid_output_schema",
            message=f"the tool's output schema is unusable: {exc.detail}",
        )
    if not validation.ok:
        return _O.protocol_error, InvocationError(
            code="output_schema_mismatch",
            message="structured content does not match the tool's output schema",
            details=_issues(validation),
        )
    return _O.success, None
