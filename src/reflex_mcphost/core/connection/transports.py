"""Transport factory: our own ``mcp.client.Transport`` per connection attempt (DD-001, S-2).

- **stdio**: ``stdio_client(params, errlog=<pipe write end>)`` — the SDK spawns the child in
  its own process group (POSIX ``start_new_session``) or Job Object (Windows) and owns the
  shutdown escalation. Never ``Client(StdioServerParameters)``: that path cannot capture
  stderr (architecture test ``test_stdio_transport_rule``). The executable is resolved first
  (honouring ``cwd`` and the definition's ``PATH``) so a missing one fails with
  ``CommandNotFoundError`` before anything is spawned.
- **Streamable HTTP**: ``streamable_http_client`` with an ``httpx2`` client built by the
  SDK's ``create_mcp_http_client`` carrying the static ``headers``. A Server with static
  headers never gets OAuth (REQ-AUTH-009): a ``401`` on its endpoint sets ``fatal_reason =
  "http_unauthorized"`` (state ``failed``, delta D6). A Server without them gets the
  ``OAuthController``'s per-attempt ``httpx2.Auth`` (Flow E), which reports a failed OAuth
  flow in ``auth_challenge`` / ``auth_reason`` and closes an established transport on a
  ``401`` so the supervisor reconnects. Without a controller (tests, embedding) a ``401``
  with ``WWW-Authenticate`` sets ``auth_challenge``: the SDK maps HTTP errors to generic
  JSON-RPC errors, so the supervisor needs this to pick ``auth_required``. The same hook
  sets ``session_terminated`` on a ``404`` to a POST that
  carried ``Mcp-Session-Id`` — exactly the condition on which the SDK fails the request with
  ``MCPError(INVALID_REQUEST, "Session terminated")`` without re-initializing (Analyze
  P2-01); the supervisor then rebuilds the Client (REQ-CON-015).

- **Process-group cleanup** (REQ-CON-008, TD DD-002): the SDK signals the child's process
  group only when the child outlives the stdin-EOF grace, so a Server that exits promptly
  leaves its descendants alive. After ``stdio_client`` has exited — for any reason — the
  remains of the group are signalled: POSIX ``os.killpg(pgid, SIGTERM)``, ``SIGKILL`` after
  ``GROUP_KILL_TIMEOUT_S`` if any member is left (``ProcessLookupError`` = nothing left,
  logged at DEBUG). ``pgid`` is the child pid, which the SDK's ``start_new_session`` makes
  the group id; it is read once, right after the spawn, from the ``process`` local of the
  ``stdio_client`` generator (the SDK exposes no other pid surface; pinned by
  ``tests/contract/test_mcp_sdk_surface.py``). Windows: the SDK puts the child in a Job
  Object with ``KILL_ON_JOB_CLOSE`` and closes it on every shutdown, which kills the
  remaining members; nothing more is done there.

``ObservedTransport`` wraps the read stream to set ``closed`` when it ends (EOF, closed or
broken): the SDK does not wake a ``Client`` body when the peer dies (S-2 Q3). ``SecretStr``
values are revealed (and registered for redaction) only while building params/headers.
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

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
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.secrets import reveal

if TYPE_CHECKING:
    from reflex_mcphost.core.auth.oauth import OAuthController

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
GROUP_KILL_TIMEOUT_S = 2.0
"""SIGTERM → SIGKILL wait of the process-group cleanup (REQ-CON-008: 2 s / 2 s)."""
_GROUP_POLL_S = 0.01

_log = get_logger(__name__)


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
        """Authorization is required: an HTTP ``401`` with auth metadata (no controller) or
        a failed OAuth flow (controller)."""
        self.auth_reason: str | None = None
        """``ServerStatus.reason`` for ``auth_required`` (``client_id_required``) or None."""
        self.fatal_reason: str | None = None
        """A permanent failure observed on HTTP (``http_unauthorized``, REQ-AUTH-009)."""
        self.established = False
        """A request to the Server's endpoint succeeded on this transport."""
        self.session_terminated = anyio.Event()
        """A legacy HTTP session was answered ``404``: the Client must be rebuilt."""

    def mark_closed(self) -> None:
        self.closed.set()

    async def __aenter__(self) -> TransportStreams:
        if self._used:
            raise RuntimeError("ObservedTransport is single-use; build one per attempt")
        self._used = True
        self._cm = self._open_streams(self)
        read, write = await self._cm.__aenter__()
        return _ObservedReadStream(read, self.mark_closed), write

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


def spawned_pid(stdio_cm: object) -> int | None:
    """The pid of the child an entered ``stdio_client(...)`` context manager spawned.

    Read-only: the ``process`` local of the suspended ``stdio_client`` async generator (the
    SDK exposes no pid; pinned by the SDK contract test). ``None`` if it is not available.
    """
    frame = getattr(getattr(stdio_cm, "gen", None), "ag_frame", None)
    process = frame.f_locals.get("process") if frame is not None else None
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) and pid > 0 else None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # EPERM never proves the group is gone
        pass
    return True


async def reap_process_group(pgid: int, *, grace: float = GROUP_KILL_TIMEOUT_S) -> bool:
    """Signal what is left of process group ``pgid`` after its leader exited (POSIX only).

    SIGTERM, then SIGKILL after ``grace`` seconds if a member is left. Returns whether anything was
    left to signal. Never raises for a gone or unsignalable group (REQ-CON-008).
    """
    if sys.platform == "win32":  # the SDK's Job Object (KILL_ON_JOB_CLOSE) covers Windows
        return False
    log = _log  # pgid only: no command line or environment (Art. 5)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        log.debug("process group %d already empty after the transport closed", pgid)
        return False
    except PermissionError:
        log.debug("process group %d not fully signalable; waiting for it", pgid)
    log.info("signalled the remaining members of process group %d (SIGTERM)", pgid)
    with anyio.move_on_after(grace):
        while _group_alive(pgid):  # noqa: ASYNC110 - no event signals another process's death
            await anyio.sleep(_GROUP_POLL_S)
        return True
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return True
    log.warning("process group %d outlived SIGTERM by %.0f s; sent SIGKILL", pgid, grace)
    return True


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
                pgid: int | None = None
                try:
                    client_cm = stdio_client(params, errlog=pipe.child_end())
                    async with client_cm as streams:
                        pgid = spawned_pid(client_cm)
                        pipe.close_parent_write_end()  # spawn done: EOF == all writers gone
                        yield streams
                finally:
                    if pgid is not None:  # also after a clean exit (REQ-CON-008)
                        with anyio.CancelScope(shield=True):
                            await reap_process_group(pgid)
                    pipe.close_parent_write_end()
                    with anyio.move_on_after(STDERR_DRAIN_GRACE_S, shield=True):
                        await pipe.eof.wait()
                    tg.cancel_scope.cancel()
        finally:
            pipe.close()

    return open_streams


def _same_endpoint(a: httpx2.URL, b: httpx2.URL) -> bool:
    return (a.scheme, a.host, a.port, a.path) == (b.scheme, b.host, b.port, b.path)


def _http_opener(
    transport: HttpTransport,
    http_transport: httpx2.AsyncBaseTransport | None,
    oauth: OAuthController | None,
) -> Callable[[ObservedTransport], AbstractAsyncContextManager[Any]]:
    static = bool(transport.headers)
    endpoint = httpx2.URL(str(transport.url))

    @asynccontextmanager
    async def open_streams(observed: ObservedTransport) -> AsyncIterator[Any]:
        async def on_response(response: httpx2.Response) -> None:
            request = response.request
            if _same_endpoint(request.url, endpoint):
                if response.is_success:
                    observed.established = True
                elif response.status_code == 401:
                    if static:  # REQ-AUTH-009: never OAuth; a permanent failure (D6)
                        observed.fatal_reason = "http_unauthorized"
                        if observed.established:
                            observed.mark_closed()
                    elif oauth is None and "www-authenticate" in response.headers:
                        observed.auth_challenge = True
            if (
                response.status_code == 404
                and request.method == "POST"
                and MCP_SESSION_ID in request.headers
            ):
                observed.session_terminated.set()

        headers = {k: reveal(v) for k, v in transport.headers.items()}
        auth = oauth.auth_for(observed) if oauth is not None and not static else None
        if http_transport is None:
            client = create_mcp_http_client(headers=headers, auth=auth)
        else:  # tests: in-memory MockTransport
            client = httpx2.AsyncClient(headers=headers, transport=http_transport, auth=auth)
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
    oauth: OAuthController | None = None,
) -> ObservedTransport:
    """A fresh transport for one connection attempt of ``definition``.

    ``on_stderr`` receives each stderr line of a stdio child (raw: the ``LogRing`` redacts).
    ``http_transport`` replaces the network layer of the ``httpx2`` client (tests).
    ``oauth`` is the Server's ``OAuthController`` (HTTP without static headers, Flow E).
    """
    transport = definition.transport
    if isinstance(transport, StdioTransport):
        return ObservedTransport(_stdio_opener(transport, on_stderr))
    return ObservedTransport(_http_opener(transport, http_transport, oauth))
