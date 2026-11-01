"""The host side of one MCP App View, transport-agnostic (API §7, DD-012, Flow F).

``AppBridge`` is the pure-Python JSON-RPC state machine between one View (through its
sandbox proxy) and the Host. It performs no I/O itself: it talks to the world only through
``AppPorts`` — ``invoke_tool`` (bound by the adapter to ``Invoker.call("<server_id>::<name>",
…, consumer=Consumer.app(server_id))``; Art. 3: the bridge never touches a session),
``read_resource`` (``Catalog.read_resource`` of the same Server), ``confirm_link`` (the
human confirmation of ``ui/open-link``), ``log`` (``ConnectionManager.append_log``),
``send`` (one serialized message to the proxy / View) and the optional ``size_changed``.

Lifecycle: ``async with bridge:`` owns a task group for the View's requests.
``ui/notifications/sandbox-proxy-ready`` → one ``ui/notifications/sandbox-resource-ready
{html, csp: <policy>, sandbox: "allow-scripts"}`` (a repeat is ignored and logged). Before
the Host has answered ``ui/initialize`` only ``ui/initialize`` and ``ping`` are handled
(other requests → ``-32600`` "not initialized", notifications dropped).
``ui/notifications/initialized`` unlocks Host → View traffic: ``tool-input`` once, then
``tool-result`` (``success`` / ``tool_error``) or ``tool-cancelled {reason}``
(``cancelled`` / ``timeout`` / ``connection_lost``) once. Nothing but replies and the
proxy's resource is sent before that (REQ-APP-003). ``teardown(reason)`` sends
``ui/resource-teardown {reason}``, waits ≤ 2 s for the response and then cancels the View's
in-flight requests; leaving the context without it (a tab disconnect) cancels them without
sending anything (REQ-APP-010).

Every View request is logged as ``<method> → <outcome>`` (``ok``, the JSON-RPC error code
or the invocation outcome; level ``warning`` for an error reply) — never params,
arguments, results or HTML (REQ-APP-012, Art. 5). ``receive`` never raises.
"""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol, cast, get_args
from urllib.parse import urlsplit

import anyio
import mcp_types as types
from anyio.abc import TaskGroup

from reflex_mcphost.core.apps.constants import (
    APPS_PROTOCOL_VERSION,
    MAX_HEIGHT_PX,
    MAX_INFLIGHT,
    MAX_LINK_CHARS,
    MAX_LOG_DATA_BYTES,
    MAX_RESOURCE_BYTES,
    MIN_HEIGHT_PX,
    TEARDOWN_TIMEOUT_S,
)
from reflex_mcphost.core.apps.csp import CspResult
from reflex_mcphost.core.apps.messages import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    SERVER_ERROR,
    Invalid,
    Notification,
    Request,
    Response,
    error_message,
    notification_message,
    parse_message,
    request_message,
    result_message,
)
from reflex_mcphost.core.config.models import LogLevel
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.invoker import InvocationResult
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.toolset import outcome_of

__all__ = [
    "LOGGER",
    "RENDERED_OUTCOMES",
    "TEARDOWN_REASONS",
    "AppBridge",
    "AppPorts",
    "AppSeed",
    "TeardownReason",
    "is_openable_link",
]

LOGGER: Final = "mcp-app"
"""The log-stream logger name of every bridge entry."""
TeardownReason = Literal["replaced", "closed", "server_removed", "host_closing"]
TEARDOWN_REASONS: Final = frozenset(get_args(TeardownReason))
_O = InvocationOutcome
RESULT_OUTCOMES: Final = frozenset({_O.success, _O.tool_error})
CANCELLED_OUTCOMES: Final = frozenset({_O.cancelled, _O.timeout, _O.connection_lost})
RENDERED_OUTCOMES: Final = RESULT_OUTCOMES | CANCELLED_OUTCOMES
"""Invocation outcomes that render a View (REQ-APP-001, delta D2)."""
_COUNTED = frozenset({"tools/call", "resources/read"})
_UNSUPPORTED = frozenset({"ui/message", "ui/update-model-context"})
_TRUNCATED = " [truncated]"
_CANCEL_WAIT_S = 1.0
"""After a teardown, how long to wait for the cancelled requests to finish."""
_log = get_logger(__name__)

Phase = Literal["waiting_proxy", "resource_sent", "replied", "initialized", "closing", "closed"]


class _ReadResult(Protocol):
    @property
    def contents(self) -> Any: ...


@dataclass(frozen=True)
class AppSeed:
    """What the View is rendered for: the UI invocation's arguments and final result."""

    arguments: Mapping[str, Any]
    result: InvocationResult


@dataclass(frozen=True)
class AppPorts:
    """The bridge's only ways out (DD-012: ``core.apps`` does no I/O)."""

    invoke_tool: Callable[[str, dict[str, Any]], Awaitable[InvocationResult]]
    """Bare tool name + arguments → ``Invoker.call`` of the View's own Server with
    ``Consumer.app(server_id)``. (Not named ``call_tool``: the Art. 3 architecture test
    reserves that name for the Invoker and the MRTR loop.)"""
    read_resource: Callable[[str], Awaitable[_ReadResult]]
    confirm_link: Callable[[str], Awaitable[bool]]
    log: Callable[[LogEntry], None]
    send: Callable[[str], Awaitable[object]]
    size_changed: Callable[[int], object] | None = None


def is_openable_link(url: object) -> bool:
    """``ui/open-link``: an ``http`` / ``https`` URL with a host name, at most 2,048
    characters, without whitespace or control characters (REQ-APP-015; the core twin of
    ``ui.models.is_openable_url``)."""
    if not isinstance(url, str) or len(url) > MAX_LINK_CHARS:
        return False
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(host)


def _dump_block(block: Any) -> Any:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", by_alias=True, exclude_none=True)
    return block


def call_tool_result(result: InvocationResult) -> dict[str, Any]:
    """The MCP ``CallToolResult`` shape rebuilt from an ``InvocationResult`` (API §7)."""
    shaped: dict[str, Any] = {"content": [_dump_block(b) for b in result.content]}
    if result.structured_content is not None:
        shaped["structuredContent"] = result.structured_content
    shaped["isError"] = result.outcome is not _O.success
    return shaped


def _resource_bytes(item: Any) -> int:
    if isinstance(item, types.TextResourceContents):
        return len(item.text.encode("utf-8", "surrogatepass"))
    if isinstance(item, types.BlobResourceContents):
        return (len(item.blob) * 3) // 4
    return 0


def _level(value: object) -> LogLevel:
    for level in get_args(LogLevel):
        if value == level:
            return cast(LogLevel, level)
    return "info"


def _render_data(data: object) -> str:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=repr)
    raw = text.encode("utf-8", "replace")
    if len(raw) > MAX_LOG_DATA_BYTES:
        text = raw[:MAX_LOG_DATA_BYTES].decode("utf-8", "ignore") + _TRUNCATED
    return text


class _ReplyError(Exception):
    """A JSON-RPC error reply raised inside a handler."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AppBridge:
    """The host side of one View (API §7)."""

    def __init__(
        self,
        *,
        server_id: str,
        view_id: str,
        seed: AppSeed,
        html: str,
        host_info: Mapping[str, Any],
        host_context: Mapping[str, Any],
        csp: CspResult,
        ports: AppPorts,
        teardown_timeout_s: float = TEARDOWN_TIMEOUT_S,
    ) -> None:
        if seed.result.outcome not in RENDERED_OUTCOMES:
            raise ValueError(f"outcome {seed.result.outcome.value!r} does not render a View")
        self.server_id = server_id
        self.view_id = view_id
        self._seed = seed
        self._html = html
        self._host_info = dict(host_info)
        self._host_context = dict(host_context)
        self._csp = csp
        self._ports = ports
        self._teardown_timeout_s = teardown_timeout_s
        self._phase: Phase = "waiting_proxy"
        self._task_group: TaskGroup | None = None
        self._entered = False
        self._inflight = 0
        self._link_pending = False
        self._next_id = 0
        self._teardown_id: str | None = None
        self._teardown_answered = anyio.Event()
        self._height: int | None = None
        self._unlocked = False
        self._scopes: set[anyio.CancelScope] = set()
        self._settled = anyio.Event()

    # ------------------------------------------------------------------ state

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def initialized(self) -> bool:
        """``True`` once the View sent ``ui/notifications/initialized``."""
        return self._unlocked

    @property
    def closed(self) -> bool:
        return self._phase == "closed"

    @property
    def height(self) -> int | None:
        """The last clamped height the View asked for (``size-changed``)."""
        return self._height

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> AppBridge:
        if self._entered:
            raise RuntimeError("an AppBridge runs once")
        self._entered = True
        self._task_group = await anyio.create_task_group().__aenter__()
        for dropped in self._csp.dropped:
            self._log(f"csp → dropped {dropped.list_name} entry {dropped.entry}", "warning")
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Discard the View (a tab disconnect when ``teardown`` did not run): in-flight
        requests are cancelled and nothing is sent."""
        task_group = self._task_group
        self._phase = "closed"
        self._cancel_inflight()
        if task_group is not None:
            self._task_group = None
            await task_group.__aexit__(None, None, None)

    async def teardown(self, reason: str) -> None:
        """``ui/resource-teardown {reason}``, wait ≤ 2 s for the response, then cancel the
        View's in-flight requests (REQ-APP-010). Idempotent."""
        if reason not in TEARDOWN_REASONS:
            raise ValueError(f"unknown teardown reason {reason!r}")
        if self._phase in ("closing", "closed"):
            return
        unlocked = self._unlocked
        self._phase = "closing"
        if unlocked:
            self._teardown_id = self._host_id()
            await self._send(
                request_message(self._teardown_id, "ui/resource-teardown", {"reason": reason})
            )
            with anyio.move_on_after(self._teardown_timeout_s):
                await self._teardown_answered.wait()
        self._phase = "closed"
        self._cancel_inflight()
        with anyio.move_on_after(_CANCEL_WAIT_S):  # their invocations record `cancelled`
            while self._scopes:
                await self._settled.wait()

    def _cancel_inflight(self) -> None:
        for scope in tuple(self._scopes):
            scope.cancel()

    async def set_host_context(self, **changes: Any) -> None:
        """Send ``ui/notifications/host-context-changed`` (theme, container size) once the
        View is initialized; before that the change only updates the initial context."""
        self._host_context.update(changes)
        if self._unlocked and self._phase == "initialized":
            await self._send(notification_message("ui/notifications/host-context-changed", changes))

    # ------------------------------------------------------------------ inbound

    async def receive(self, raw: str | bytes) -> None:
        """Handle one serialized message from the proxy / View. Never raises."""
        try:
            await self._receive(raw)
        except Exception as exc:  # contained: the View keeps running (REQ-APP-005)
            _log.error("MCP App message handling failed (%s)", type(exc).__name__)

    async def _receive(self, raw: str | bytes) -> None:
        if self._task_group is None or self._phase == "closed":
            return
        message = parse_message(raw)
        match message:
            case Invalid():
                self._log(
                    f"invalid message → {message.code if message.reply else 'dropped'}", "warning"
                )
                if message.reply:
                    await self._send(error_message(message.id, message.code, message.message))
            case Response():
                if message.id == self._teardown_id:
                    self._teardown_answered.set()
            case Notification():
                await self._notification(message)
            case Request():
                await self._request(message)

    async def _notification(self, note: Notification) -> None:
        method = note.method
        if method == "ui/notifications/sandbox-proxy-ready":
            if self._phase != "waiting_proxy":
                self._log(f"{method} → ignored", "warning")
                return
            self._phase = "resource_sent"
            await self._send(
                notification_message(
                    "ui/notifications/sandbox-resource-ready",
                    {"html": self._html, "csp": self._csp.policy, "sandbox": "allow-scripts"},
                )
            )
            return
        if self._phase not in ("replied", "initialized"):
            return  # notifications before the ui/initialize reply are dropped
        if method == "ui/notifications/initialized":
            if self._phase == "replied":
                self._phase = "initialized"
                self._unlocked = True
                await self._send_seed()
        elif method == "ui/notifications/size-changed":
            self._size_changed(note.params)
        elif method == "notifications/message":
            level = note.params.get("level")
            self._ports_log(_render_data(note.params.get("data")), _level(level))

    async def _send_seed(self) -> None:
        result = self._seed.result
        await self._send(
            notification_message(
                "ui/notifications/tool-input", {"arguments": dict(self._seed.arguments)}
            )
        )
        if result.outcome in RESULT_OUTCOMES:
            await self._send(
                notification_message("ui/notifications/tool-result", call_tool_result(result))
            )
        else:
            await self._send(
                notification_message(
                    "ui/notifications/tool-cancelled", {"reason": result.outcome.value}
                )
            )

    def _size_changed(self, params: Mapping[str, Any]) -> None:
        height = params.get("height")
        if (
            isinstance(height, bool)
            or not isinstance(height, int | float)
            or not math.isfinite(height)
        ):
            return
        clamped = max(MIN_HEIGHT_PX, min(MAX_HEIGHT_PX, round(height)))
        self._height = clamped
        if self._ports.size_changed is not None:
            try:
                self._ports.size_changed(clamped)
            except Exception as exc:
                _log.warning("size_changed port failed (%s)", type(exc).__name__)

    async def _request(self, request: Request) -> None:
        method = request.method
        if self._phase == "closing":
            await self._error(request, SERVER_ERROR, "the View is closing")
            return
        if method == "ping":
            await self._ok(request, {})
            return
        if method == "ui/initialize":
            if self._phase != "resource_sent":
                code = (
                    "not initialized" if self._phase == "waiting_proxy" else "already initialized"
                )
                await self._error(request, INVALID_REQUEST, code)
                return
            self._phase = "replied"
            await self._ok(request, self._initialize_result())
            return
        if self._phase not in ("replied", "initialized"):
            await self._error(request, INVALID_REQUEST, "not initialized")
            return
        if method in _COUNTED:
            if self._inflight >= MAX_INFLIGHT:
                await self._error(request, SERVER_ERROR, "too many concurrent requests")
                return
            self._spawn(request)
        elif method == "ui/open-link":
            await self._open_link(request)
        elif method == "ui/request-display-mode":
            await self._ok(request, {"mode": "inline"})
        elif method in _UNSUPPORTED:
            await self._error(request, METHOD_NOT_FOUND, "Method not supported by this host")
        else:
            await self._error(request, METHOD_NOT_FOUND, "Method not found")

    def _initialize_result(self) -> dict[str, Any]:
        context = {
            **self._host_context,
            "displayMode": "inline",
            "availableDisplayModes": ["inline"],
        }
        return {
            "protocolVersion": APPS_PROTOCOL_VERSION,
            "hostInfo": self._host_info,
            "hostCapabilities": {
                "openLinks": {},
                "serverTools": {},
                "serverResources": {},
                "logging": {},
                "sandbox": {"permissions": {}, "csp": self._csp.csp_lists()},
            },
            "hostContext": context,
        }

    # ------------------------------------------------------------------ counted requests

    def _spawn(self, request: Request) -> None:
        self._inflight += 1
        handler = self._tools_call if request.method == "tools/call" else self._resources_read
        self._start(self._counted, request, handler)

    def _start(self, fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        """Run ``fn`` concurrently in the bridge's task group (``_receive`` only runs while
        the group exists)."""
        if self._task_group is not None:
            self._task_group.start_soon(self._tracked, fn, *args)

    async def _tracked(self, fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        """Run one View request under its own cancel scope (cancelled by teardown / exit);
        an unexpected error is contained so the View keeps running."""
        with anyio.CancelScope() as scope:
            self._scopes.add(scope)
            try:
                if self._phase == "closed":
                    scope.cancel()
                await fn(*args)
            except Exception as exc:
                _log.error("MCP App request failed (%s)", type(exc).__name__)
            finally:
                self._scopes.discard(scope)
                self._settled.set()
                self._settled = anyio.Event()

    async def _counted(
        self, request: Request, handler: Callable[[Request], Awaitable[tuple[dict[str, Any], str]]]
    ) -> None:
        try:
            try:
                result, outcome = await handler(request)
            except _ReplyError as reply:
                await self._error(request, reply.code, reply.message)
                return
            await self._ok(request, result, outcome=outcome)
        finally:
            self._inflight -= 1

    async def _tools_call(self, request: Request) -> tuple[dict[str, Any], str]:
        name = request.params.get("name")
        arguments = request.params.get("arguments", {})
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            raise _ReplyError(INVALID_PARAMS, "tools/call needs a string name and object arguments")
        try:
            result = await self._ports.invoke_tool(name, arguments)
        except Exception as exc:
            _log.warning("MCP App tool call failed (%s)", type(exc).__name__)
            raise _ReplyError(SERVER_ERROR, "tool call failed") from None
        if result.outcome in RESULT_OUTCOMES:
            return call_tool_result(result), result.outcome.value
        if result.error is not None and result.error.code == "app_scope":
            raise _ReplyError(INVALID_PARAMS, "the tool is not available to this App")
        text = outcome_of(result).text
        return {"content": [{"type": "text", "text": text}], "isError": True}, result.outcome.value

    async def _resources_read(self, request: Request) -> tuple[dict[str, Any], str]:
        uri = request.params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise _ReplyError(INVALID_PARAMS, "resources/read needs a string uri")
        try:
            read = await self._ports.read_resource(uri)
        except Exception as exc:
            _log.warning("MCP App resource read failed (%s)", type(exc).__name__)
            raise _ReplyError(SERVER_ERROR, "resource read failed") from None
        contents = list(read.contents)
        if sum(_resource_bytes(c) for c in contents) > MAX_RESOURCE_BYTES:
            raise _ReplyError(SERVER_ERROR, "resource too large")
        return {"contents": [_dump_block(c) for c in contents]}, "ok"

    async def _open_link(self, request: Request) -> None:
        url = request.params.get("url")
        if not is_openable_link(url):
            await self._error(request, SERVER_ERROR, "the link cannot be opened")
            return
        if self._link_pending:
            await self._error(request, SERVER_ERROR, "a link confirmation is pending")
            return
        self._link_pending = True
        self._start(self._confirm_link, request, str(url))

    async def _confirm_link(self, request: Request, url: str) -> None:
        try:
            try:
                approved = await self._ports.confirm_link(url)
            except Exception as exc:
                _log.warning("MCP App link confirmation failed (%s)", type(exc).__name__)
                approved = False
        finally:
            self._link_pending = False
        if approved is True:
            await self._ok(request, {})
        else:
            await self._error(request, SERVER_ERROR, "link declined")

    # ------------------------------------------------------------------ outbound

    def _host_id(self) -> str:
        self._next_id += 1
        return f"host-{self._next_id}"

    async def _ok(self, request: Request, result: dict[str, Any], *, outcome: str = "ok") -> None:
        level: LogLevel = "info" if outcome in ("ok", _O.success.value) else "warning"
        self._log(f"{request.method} → {outcome}", level)
        await self._reply(result_message(request.id, result))

    async def _error(self, request: Request, code: int, message: str) -> None:
        self._log(f"{request.method} → {code}", "warning")
        await self._reply(error_message(request.id, code, message))

    async def _reply(self, message: str) -> None:
        if self._phase == "closed":
            return  # the View is gone
        await self._send(message)

    async def _send(self, message: str) -> None:
        try:
            pending = self._ports.send(message)
            if inspect.isawaitable(pending):
                await pending
        except Exception as exc:
            _log.warning("MCP App send failed (%s)", type(exc).__name__)

    def _log(self, text: str, level: LogLevel = "info") -> None:
        self._ports_log(text, level)

    def _ports_log(self, text: str, level: LogLevel) -> None:
        entry = LogEntry(level=level, logger=LOGGER, timestamp=datetime.now(UTC), text=text)
        try:
            self._ports.log(entry)
        except Exception as exc:
            _log.warning("MCP App log port failed (%s)", type(exc).__name__)
