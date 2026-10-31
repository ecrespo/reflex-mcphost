"""Transport factory: our own ``mcp.client.Transport`` per connection attempt (DD-001, S-2).

- **stdio**: ``stdio_client(params, errlog=<pipe write end>)`` — the SDK spawns the child in
  its own process group (POSIX ``start_new_session``) or Job Object (Windows) and owns the
  shutdown escalation. Never ``Client(StdioServerParameters)``: that path cannot capture
  stderr (architecture test ``test_stdio_transport_rule``). The executable is resolved first
  (honouring ``cwd`` and the definition's ``PATH``) so a missing one fails with
  ``CommandNotFoundError`` before anything is spawned.
- **Streamable HTTP**: ``streamable_http_client`` with an ``httpx2`` client built by the
  SDK's ``create_mcp_http_client`` carrying the static ``headers``. A response hook records
  a ``401`` with ``WWW-Authenticate`` (auth metadata) in ``auth_challenge``: the SDK maps
  HTTP errors to generic JSON-RPC errors, so the supervisor needs this to pick
  ``auth_required``. The same hook sets ``session_terminated`` on a ``404`` to a POST that
  carried ``Mcp-Session-Id`` — exactly the condition on which the SDK fails the request with
  ``MCPError(INVALID_REQUEST, "Session terminated")`` without re-initializing (Analyze
  P2-01); the supervisor then rebuilds the Client (REQ-CON-015).

``ObservedTransport`` wraps the read stream to set ``closed`` when it ends (EOF, closed or
broken): the SDK does not wake a ``Client`` body when the peer dies (S-2 Q3). ``SecretStr``
values are revealed (and registered for redaction) only while building params/headers.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any

import anyio
import anyio.to_thread
import httpx2
from mcp import StdioServerParameters
from mcp.client._transport import TransportStreams
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import MCP_SESSION_ID, streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from reflex_mcphost.core.config.models import HttpTransport, ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.stderr import LineSink, StderrPipe
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.secrets import reveal

__all__ = [
    "CommandNotFoundError",
    "ObservedTransport",
    "build_transport",
    "resolve_executable",
    "resolve_stdio_command",
]

STDERR_DRAIN_GRACE_S = 0.5
"""After the transport exits, wait this long for stderr EOF (a lingering descendant may
keep the pipe open) before the reader is cancelled."""


class CommandNotFoundError(MCPHostError):
    """The stdio executable cannot be resolved (state ``failed``, ``command_not_found``)."""

    def __init__(self, executable: str) -> None:
        self.executable = executable
        super().__init__(f"command not found: {executable}")


def resolve_executable(command: str, *, cwd: Path | None, path: str | None) -> str | None:
    """Where ``command`` would be found, or ``None``: a command with a path separator is
    resolved against ``cwd``; a bare name is searched on ``path`` (``None`` = ``os.defpath``)."""
    has_sep = os.sep in command or (os.altsep is not None and os.altsep in command)
    if has_sep or Path(command).is_absolute():
        candidate = Path(command)
        if not candidate.is_absolute() and cwd is not None:
            candidate = cwd / candidate
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None
    return shutil.which(command, path=path)


class _ObservedReadStream:
    """Proxy of a transport read stream that reports its end to ``on_end`` (S-2)."""

    def __init__(self, inner: Any, on_end: Callable[[], None]) -> None:
        self._inner = inner
        self._on_end = on_end

    def __getattr__(self, name: str) -> Any:  # forwards e.g. `last_context`
        return getattr(self._inner, name)

    async def receive(self) -> Any:
        try:
            return await self._inner.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError):
            self._on_end()
            raise

    def __aiter__(self) -> _ObservedReadStream:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _ObservedReadStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> bool | None:
        result: bool | None = await self._inner.__aexit__(*exc)
        return result


class ObservedTransport:
    """A single-use ``mcp.client.Transport`` exposing liveness and HTTP auth observations."""

    def __init__(
        self, open_streams: Callable[[ObservedTransport], AbstractAsyncContextManager[Any]]
    ) -> None:
        self._open_streams = open_streams
        self._cm: AbstractAsyncContextManager[Any] | None = None
        self._used = False
        self.closed = anyio.Event()
        """Set when the read stream ended: the Server is gone (stdio exit, HTTP close)."""
        self.auth_challenge = False
        """An HTTP ``401`` with ``WWW-Authenticate`` was received (auth metadata present)."""
        self.session_terminated = anyio.Event()
        """A legacy HTTP session was answered ``404``: the Client must be rebuilt."""

    def _mark_closed(self) -> None:
        self.closed.set()

    async def __aenter__(self) -> TransportStreams:
        if self._used:
            raise RuntimeError("ObservedTransport is single-use; build one per attempt")
        self._used = True
        self._cm = self._open_streams(self)
        read, write = await self._cm.__aenter__()
        return _ObservedReadStream(read, self._mark_closed), write

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        assert self._cm is not None  # noqa: S101 - entered above
        return await self._cm.__aexit__(exc_type, exc, tb)


async def resolve_stdio_command(transport: StdioTransport) -> str | None:
    """The executable ``transport`` would launch (its ``cwd`` and ``PATH`` — the
    definition's, else the SDK default environment's), or ``None``; in a worker thread."""
    env = {k: reveal(v) for k, v in transport.env.items()}
    path = env.get("PATH", get_default_environment().get("PATH", os.defpath))
    return await anyio.to_thread.run_sync(
        lambda: resolve_executable(transport.command, cwd=transport.cwd, path=path)
    )


async def _stdio_params(transport: StdioTransport) -> StdioServerParameters:
    if await resolve_stdio_command(transport) is None:
        raise CommandNotFoundError(transport.command)
    env = {k: reveal(v) for k, v in transport.env.items()}
    return StdioServerParameters(
        command=transport.command,
        args=list(transport.args),
        env=env or None,
        cwd=transport.cwd,
        encoding_error_handler="replace",
    )


def _stdio_opener(
    transport: StdioTransport, on_stderr: LineSink | None
) -> Callable[[ObservedTransport], AbstractAsyncContextManager[Any]]:
    sink: LineSink = on_stderr if on_stderr is not None else (lambda _line: None)

    @asynccontextmanager
    async def open_streams(_: ObservedTransport) -> AsyncIterator[Any]:
        params = await _stdio_params(transport)
        pipe = StderrPipe()
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(pipe.pump, sink)
                try:
                    async with stdio_client(params, errlog=pipe.child_end()) as streams:
                        pipe.close_parent_write_end()  # spawn done: EOF == all writers gone
                        yield streams
                finally:
                    pipe.close_parent_write_end()
                    with anyio.move_on_after(STDERR_DRAIN_GRACE_S, shield=True):
                        await pipe.eof.wait()
                    tg.cancel_scope.cancel()
        finally:
            pipe.close()

    return open_streams


def _http_opener(
    transport: HttpTransport, http_transport: httpx2.AsyncBaseTransport | None
) -> Callable[[ObservedTransport], AbstractAsyncContextManager[Any]]:
    @asynccontextmanager
    async def open_streams(observed: ObservedTransport) -> AsyncIterator[Any]:
        async def on_response(response: httpx2.Response) -> None:
            if response.status_code == 401 and "www-authenticate" in response.headers:
                observed.auth_challenge = True
            request = response.request
            if (
                response.status_code == 404
                and request.method == "POST"
                and MCP_SESSION_ID in request.headers
            ):
                observed.session_terminated.set()

        headers = {k: reveal(v) for k, v in transport.headers.items()}
        if http_transport is None:
            client = create_mcp_http_client(headers=headers)
        else:  # tests: in-memory MockTransport
            client = httpx2.AsyncClient(headers=headers, transport=http_transport)
        client.event_hooks = {"response": [on_response]}
        async with (
            client,
            streamable_http_client(str(transport.url), http_client=client) as streams,
        ):
            yield streams

    return open_streams


def build_transport(
    definition: ServerDefinition,
    *,
    on_stderr: LineSink | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> ObservedTransport:
    """A fresh transport for one connection attempt of ``definition``.

    ``on_stderr`` receives each stderr line of a stdio child (raw: the ``LogRing`` redacts).
    ``http_transport`` replaces the network layer of the ``httpx2`` client (tests).
    """
    transport = definition.transport
    if isinstance(transport, StdioTransport):
        return ObservedTransport(_stdio_opener(transport, on_stderr))
    return ObservedTransport(_http_opener(transport, http_transport))
