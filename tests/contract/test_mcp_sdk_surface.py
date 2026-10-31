"""Pinned mcp 2.3 SDK surface (DD-001, DD-002, Tech Design §8 "Contract", risk R-2).

Every *Contract assertions* item of spikes S-1 and S-2 (``specs/plans/spikes/``) is pinned
here; a failure means the SDK moved and the design must be re-checked before upgrading.
"""

from __future__ import annotations

import contextvars
import inspect
import io
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp_types as types
import pytest
from mcp import Client, InputRequiredRoundsExceededError, MCPError, StdioServerParameters
from mcp.client import ClientExtension, ClientRequestContext, ClientSession, advertise
from mcp.client import session as client_session
from mcp.client import stdio as sdk_stdio
from mcp.client.stdio import stdio_client
from mcp.client.subscriptions import SubscriptionLost
from mcp.shared.dispatcher import ProgressFnT
from mcp_types import jsonrpc
from pydantic import ValidationError

from tests.conftest import MODERN, SERVERS
from tests.fixtures.servers.modern_server import build_server

pytestmark = pytest.mark.anyio
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

LISTEN = SERVERS / "listen_server.py"
CALLER: contextvars.ContextVar[str | None] = contextvars.ContextVar("caller", default=None)


def _params(*extra: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(MODERN), *extra])


def _text(result: Any) -> str:
    return str(result.content[0].text)


class Recorder:
    """Callbacks with the SDK signatures; records what they observe."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.seen: list[tuple[str, Any, str | None]] = []

    async def elicit(
        self, ctx: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult | types.ErrorData:
        self.seen.append(("elicit", ctx.request_id, CALLER.get()))
        if self.refuse:
            return types.ErrorData(code=-32001, message="refused by test")
        return types.ElicitResult(action="accept", content={"name": "Ada"})

    async def sample(
        self, ctx: ClientRequestContext, params: types.CreateMessageRequestParams
    ) -> types.CreateMessageResult | types.ErrorData:
        self.seen.append(("sample", ctx.request_id, CALLER.get()))
        if self.refuse:
            return types.ErrorData(code=-1, message="sampling refused by test")
        return types.CreateMessageResult(
            role="assistant", content=types.TextContent(type="text", text="Hello Ada!"), model="m"
        )


# ------------------------------------------------------------------ S-1: signatures


def test_call_tool_signature() -> None:
    """S-1: ClientSession.call_tool parameters, in order, with the MRTR keywords."""
    sig = inspect.signature(ClientSession.call_tool)
    assert list(sig.parameters) == [
        "self",
        "name",
        "arguments",
        "read_timeout_seconds",
        "progress_callback",
        "input_responses",
        "request_state",
        "meta",
        "allow_input_required",
        "allow_claimed",
    ]
    kw_only = [n for n, p in sig.parameters.items() if p.kind is p.KEYWORD_ONLY]
    assert kw_only[0] == "input_responses"
    assert sig.parameters["allow_input_required"].default is False


def test_client_keyword_arguments() -> None:
    """S-1: Client accepts the keyword-only arguments Tech Design Flow B relies on."""
    params = inspect.signature(Client.__init__).parameters
    for name in (
        "sampling_callback",
        "elicitation_callback",
        "list_roots_callback",
        "logging_callback",
        "log_level",
        "message_handler",
        "mode",
        "prior_discover",
        "input_required_max_rounds",
        "extensions",
        "cache",
        "read_timeout_seconds",
    ):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert isinstance(advertise("io.example/ext", {"a": 1}), ClientExtension)


def test_callback_protocol_signatures() -> None:
    """S-1: callback protocols keep their (context, params) shapes."""

    def shape(proto: type) -> list[str]:
        return list(inspect.signature(proto.__call__).parameters)

    assert shape(client_session.ElicitationFnT) == ["self", "context", "params"]
    assert shape(client_session.SamplingFnT) == ["self", "context", "params"]
    assert shape(client_session.ListRootsFnT) == ["self", "context"]
    assert shape(client_session.LoggingFnT) == ["self", "params"]
    assert shape(client_session.MessageHandlerFnT) == ["self", "message"]
    assert shape(ProgressFnT) == ["self", "progress", "total", "message"]


def test_input_required_result_fields() -> None:
    """S-1: InputRequiredResult fields; empty construction is rejected."""
    fields = types.InputRequiredResult.model_fields
    assert {"input_requests", "request_state", "result_type"} <= set(fields)
    assert types.InputRequiredResult(request_state="x").result_type == "input_required"
    with pytest.raises(ValidationError):
        types.InputRequiredResult()


def test_input_responses_reject_error_data() -> None:
    """S-1: ErrorData is not a valid InputResponses value."""
    with pytest.raises(ValidationError):
        types.CallToolRequestParams(
            name="x",
            input_responses={"a": types.ErrorData(code=-1, message="no")},  # type: ignore[dict-item]
        )


def test_header_mismatch_code() -> None:
    """S-1: HEADER_MISMATCH is -32020."""
    assert jsonrpc.HEADER_MISMATCH == -32020


# ------------------------------------------------------------------ S-1: modern behaviour


async def test_manual_mrtr_loop_round_trips_state() -> None:
    """S-1: InputRequiredResult with allow_input_required; request_state echoed byte-exact."""
    async with Client(build_server()) as client:
        session = client.session
        with pytest.raises(RuntimeError):
            await session.call_tool("ask_name", {})
        result = await session.call_tool("two_rounds", {}, allow_input_required=True)
        states: list[str | None] = []
        while isinstance(result, types.InputRequiredResult):
            states.append(result.request_state)
            responses: dict[str, Any] = {}
            for key, req in (result.input_requests or {}).items():
                if isinstance(req, types.ElicitRequest):
                    responses[key] = types.ElicitResult(action="accept", content={"name": "Ada"})
                else:
                    responses[key] = types.CreateMessageResult(
                        role="assistant",
                        content=types.TextContent(type="text", text="Hello Ada!"),
                        model="m",
                    )
            result = await session.call_tool(
                "two_rounds",
                {},
                input_responses=responses,
                request_state=result.request_state,
                allow_input_required=True,
            )
        assert states == ["r1", "r2:Ada"]
        assert _text(result) == "Ada -> Hello Ada!"


async def test_dispatch_input_request_and_request_id() -> None:
    """S-1: dispatch_input_request returns the callback's answer; request_id is the key."""
    rec = Recorder()
    async with Client(build_server(), elicitation_callback=rec.elicit) as client:
        session = client.session
        first = await session.call_tool("ask_name", {}, allow_input_required=True)
        assert isinstance(first, types.InputRequiredResult)
        (key, req), *_ = (first.input_requests or {}).items()
        answer = await session.dispatch_input_request(
            ClientRequestContext(session=session, request_id=key), req
        )
        assert isinstance(answer, types.ElicitResult)
        rec.seen.clear()
        await client.call_tool("ask_name", {})  # SDK auto-loop
        assert rec.seen[0][:2] == ("elicit", "ask")


async def test_auto_loop_error_data_and_round_cap() -> None:
    """S-1: callback ErrorData -> MCPError(code); round cap -> InputRequiredRoundsExceeded."""
    rec = Recorder(refuse=True)
    async with Client(build_server(), elicitation_callback=rec.elicit) as client:
        with pytest.raises(MCPError) as info:
            await client.call_tool("ask_name", {})
        assert info.value.code == -32001
        assert isinstance(info.value.error, types.ErrorData)
    ok = Recorder()
    async with Client(
        build_server(),
        elicitation_callback=ok.elicit,
        sampling_callback=ok.sample,
        input_required_max_rounds=1,
    ) as client:
        with pytest.raises(InputRequiredRoundsExceededError) as rounds:
            await client.call_tool("two_rounds", {})
        assert rounds.value.max_rounds == 1


async def test_output_schema_violation_raises_runtime_error() -> None:
    """S-1: structured content violating outputSchema -> RuntimeError from call_tool."""
    async with Client(build_server()) as client:
        await client.list_tools()
        with pytest.raises(RuntimeError):
            await client.session.call_tool("bad_structured", {}, allow_input_required=True)


@pytest.mark.parametrize("transport", ["inproc", "stdio"])
async def test_progress_callback_signature(transport: str) -> None:
    """S-1: ProgressFnT is called as (progress, total, message) on every leg."""
    target: Any = build_server() if transport == "inproc" else _params()
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    async with Client(target) as client:
        await client.session.call_tool("progress", {}, progress_callback=on_progress)
    assert seen == [(1.0, 3.0, "step 1"), (2.0, 3.0, "step 2"), (3.0, 3.0, "step 3")]


# ------------------------------------------------------------------ S-1: legacy era


@pytest.mark.interop
async def test_legacy_call_tool_and_error_data(legacy_stdio: StdioServerParameters) -> None:
    """S-1: legacy returns CallToolResult; ErrorData reaches the Server as a JSON-RPC error;
    a caller ContextVar is NOT visible inside legacy callbacks."""
    rec = Recorder(refuse=True)
    async with Client(
        legacy_stdio, elicitation_callback=rec.elicit, sampling_callback=rec.sample
    ) as client:
        token = CALLER.set("inv-legacy")
        try:
            result = await client.session.call_tool("ask_name", {}, allow_input_required=True)
            sampled = await client.call_tool("sample", {})
        finally:
            CALLER.reset(token)
    assert isinstance(result, types.CallToolResult)
    assert _text(result) == "elicit error -32001: refused by test"
    assert _text(sampled) == "sampling error -1: sampling refused by test"
    assert [s[2] for s in rec.seen] == [None, None]


@pytest.mark.interop
async def test_legacy_progress(legacy_stdio: StdioServerParameters) -> None:
    """S-1: ProgressFnT shape holds for legacy Servers too."""
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    async with Client(legacy_stdio) as client:
        await client.session.call_tool("progress", {}, progress_callback=on_progress)
    assert seen == [(1.0, 3.0, "step 1"), (2.0, 3.0, "step 2"), (3.0, 3.0, "step 3")]


# ------------------------------------------------------------------ S-2: stdio transport


def test_stdio_client_signature() -> None:
    """S-2 #1: stdio_client(server, errlog=sys.stderr)."""
    sig = inspect.signature(stdio_client)
    assert list(sig.parameters) == ["server", "errlog"]
    assert sig.parameters["errlog"].default is sys.stderr


def test_termination_constants() -> None:
    """S-2 #6: SDK stdio shutdown waits are 2 s / 2 s (REQ-CON-008)."""
    assert sdk_stdio.PROCESS_TERMINATION_TIMEOUT == 2.0
    assert sdk_stdio.FORCE_KILL_TIMEOUT == 2.0


@posix_only
async def test_errlog_pipe_receives_stderr() -> None:
    """S-2 #2: a pipe passed as errlog receives the child's stderr lines."""
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as errlog:
        async with stdio_client(_params(), errlog=errlog) as streams:
            assert len(streams) == 2
            async with Client(_stdio_transport(streams)) as client:
                await client.call_tool("stderr", {"lines": 2})
    data = b""
    os.set_blocking(read_fd, False)
    with anyio.fail_after(5):
        while b"fixture stderr line 1" not in data:
            try:
                data += os.read(read_fd, 65536)
            except BlockingIOError:
                await anyio.sleep(0.02)
    os.close(read_fd)


async def test_errlog_requires_fileno() -> None:
    """S-2 #2: errlog without fileno() fails at spawn."""
    with pytest.raises(io.UnsupportedOperation):
        async with stdio_client(_params(), errlog=io.StringIO()):
            pass


@asynccontextmanager
async def _stdio_transport(streams: Any) -> AsyncIterator[Any]:
    yield streams


class _Watch:
    def __init__(self) -> None:
        self.closed = anyio.Event()


class _EofWatchingRead:
    def __init__(self, inner: Any, watch: _Watch) -> None:
        self._inner, self._watch = inner, watch

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def receive(self) -> Any:
        try:
            return await self._inner.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError):
            self._watch.closed.set()
            raise

    def __aiter__(self) -> _EofWatchingRead:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _EofWatchingRead:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> bool | None:
        result: bool | None = await self._inner.__aexit__(*exc)
        return result


@asynccontextmanager
async def _watched(params: StdioServerParameters, watch: _Watch) -> AsyncIterator[Any]:
    async with stdio_client(params, errlog=subprocess.DEVNULL) as (read, write):  # type: ignore[arg-type]
        yield _EofWatchingRead(read, watch), write


@posix_only
async def test_transport_wrapper_sees_child_death() -> None:
    """S-2 #3, #4: Client takes a Transport; EOF is seen < 1 s after SIGKILL; the body is not
    cancelled, message_handler is not called, and a pending request fails CONNECTION_CLOSED."""
    watch = _Watch()
    handled: list[object] = []

    async def message_handler(message: object) -> None:
        handled.append(message)

    async with Client(_watched(_params(), watch), message_handler=message_handler) as client:
        pid = int(_text(await client.call_tool("pid", {})))
        os.kill(pid, signal.SIGKILL)
        with anyio.fail_after(1):
            await watch.closed.wait()
        await anyio.sleep(0.2)  # the body is still running: not cancelled
        with pytest.raises(MCPError) as info:
            await client.list_tools()
        assert info.value.error.code == types.CONNECTION_CLOSED == -32000
    assert not [m for m in handled if isinstance(m, Exception)]


@posix_only
async def test_listen_raises_subscription_lost() -> None:
    """S-2 #5: a modern listen() stream raises SubscriptionLost when the child dies."""
    listen = StdioServerParameters(command=sys.executable, args=[str(LISTEN)])
    async with Client(stdio_client(listen, errlog=subprocess.DEVNULL)) as client:  # type: ignore[arg-type]
        pid = int(_text(await client.call_tool("pid", {})))
        with pytest.raises(SubscriptionLost):  # noqa: PT012 - kill must happen inside listen
            async with client.listen(tools_list_changed=True) as sub:
                os.kill(pid, signal.SIGKILL)
                with anyio.fail_after(3):
                    async for _ in sub:
                        pass


@posix_only
async def test_shutdown_escalates_and_is_shielded(monkeypatch: pytest.MonkeyPatch) -> None:
    """S-2 #6, #7: a Server ignoring EOF and SIGTERM is killed on exit, after the (patched)
    waits; exit completes even when the enclosing scope is cancelled."""
    monkeypatch.setattr(sdk_stdio, "PROCESS_TERMINATION_TIMEOUT", 0.3)
    monkeypatch.setattr(sdk_stdio, "FORCE_KILL_TIMEOUT", 0.3)
    params = _params("--ignore-eof", "--ignore-term")
    pid = 0
    started = 0.0
    with anyio.CancelScope() as scope:
        async with Client(stdio_client(params, errlog=subprocess.DEVNULL)) as client:  # type: ignore[arg-type]
            pid = int(_text(await client.call_tool("pid", {})))
            started = time.monotonic()
            scope.cancel()
    elapsed = time.monotonic() - started
    assert elapsed >= 0.5  # waited through both patched timeouts
    assert elapsed < 4.0  # used the patched values, not the 2 s defaults
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
