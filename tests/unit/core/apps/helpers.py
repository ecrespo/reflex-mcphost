"""Fake ports and a scripted View for the ``AppBridge`` unit tests (F7-02)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
import mcp_types as types

from reflex_mcphost.core.apps.bridge import AppBridge, AppPorts, AppSeed
from reflex_mcphost.core.apps.csp import CspResult, build_csp
from reflex_mcphost.core.catalog import ResourceContents
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.invoker import InvocationError, InvocationOutcome, InvocationResult
from reflex_mcphost.core.types import Consumer, Fqn

ARG_SECRET = "bridge-arg-secret-0201"  # noqa: S105 - test sentinel
RESULT_SECRET = "bridge-result-secret-0202"  # noqa: S105 - test sentinel
HTML_SECRET = "bridge-html-secret-0203"  # noqa: S105 - test sentinel
HTML = f"<!doctype html><p>view {HTML_SECRET}</p>"
HOST_INFO = {"name": "reflex-mcphost", "version": "0.1.0"}
HOST_CONTEXT = {"theme": "light", "locale": "en-US", "timeZone": "UTC"}
SERVER = "apps"
Out = InvocationOutcome


def invocation(
    outcome: InvocationOutcome = Out.success,
    *,
    text: str = f"result {RESULT_SECRET}",
    structured: dict[str, Any] | None = None,
    error: InvocationError | None = None,
    name: str = "get-time",
    consumer: Consumer | None = None,
) -> InvocationResult:
    return InvocationResult(
        id="inv_1",
        fqn=Fqn(server_id=SERVER, name=name),
        consumer=consumer or Consumer.human(),
        outcome=outcome,
        content=(types.TextContent(type="text", text=text),) if text else (),
        structured_content=structured,
        error=error,
        started_at=datetime.now(UTC),
        duration_ms=1,
    )


@dataclass
class FakePorts:
    """Records every port call; behavior is configurable per test."""

    tool_results: dict[str, InvocationResult] = field(default_factory=dict)
    tool_gate: anyio.Event | None = None
    tool_error: BaseException | None = None
    resource: ResourceContents | None = None
    resource_error: BaseException | None = None
    link_answer: bool = True
    link_gate: anyio.Event | None = None
    send_error: BaseException | None = None
    sent: list[dict[str, Any]] = field(default_factory=list)
    logged: list[LogEntry] = field(default_factory=list)
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    cancelled_calls: int = 0
    reads: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    heights: list[int] = field(default_factory=list)
    on_send: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def invoke_tool(self, name: str, arguments: dict[str, Any]) -> InvocationResult:
        self.tool_calls.append((name, arguments))
        if self.tool_gate is not None:
            try:
                await self.tool_gate.wait()
            except anyio.get_cancelled_exc_class():
                self.cancelled_calls += 1
                raise
        if self.tool_error is not None:
            raise self.tool_error
        return self.tool_results.get(name) or invocation(name=name, consumer=Consumer.app(SERVER))

    async def read_resource(self, uri: str) -> ResourceContents:
        self.reads.append(uri)
        if self.resource_error is not None:
            raise self.resource_error
        if self.resource is not None:
            return self.resource
        item = types.TextResourceContents(uri=uri, mimeType="text/plain", text="hello")
        return ResourceContents(server_id=SERVER, uri=uri, contents=(item,))

    async def confirm_link(self, url: str) -> bool:
        self.links.append(url)
        if self.link_gate is not None:
            await self.link_gate.wait()
        return self.link_answer

    def log(self, entry: LogEntry) -> None:
        self.logged.append(entry)

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        parsed = json.loads(message)
        self.sent.append(parsed)
        if self.on_send is not None:
            await self.on_send(parsed)

    def size_changed(self, height: int) -> None:
        self.heights.append(height)

    def ports(self) -> AppPorts:
        return AppPorts(
            invoke_tool=self.invoke_tool,
            read_resource=self.read_resource,
            confirm_link=self.confirm_link,
            log=self.log,
            send=self.send,
            size_changed=self.size_changed,
        )

    # ------------------------------------------------------------ views of the traffic

    def methods(self) -> list[str]:
        return [m["method"] for m in self.sent if "method" in m]

    def reply(self, msg_id: object) -> dict[str, Any]:
        (found,) = [m for m in self.sent if "method" not in m and m.get("id") == msg_id]
        return found

    def log_texts(self) -> list[str]:
        return [e.text for e in self.logged]


def req(msg_id: object, method: str, params: Mapping[str, Any] | None = None) -> str:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = dict(params)
    return json.dumps(msg)


def note(method: str, params: Mapping[str, Any] | None = None) -> str:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = dict(params)
    return json.dumps(msg)


PROXY_READY = note("ui/notifications/sandbox-proxy-ready")
INITIALIZE = req(
    "init",
    "ui/initialize",
    {
        "protocolVersion": "2026-01-26",
        "appInfo": {"name": "v", "version": "1"},
        "appCapabilities": {},
    },
)
INITIALIZED = note("ui/notifications/initialized")


def make_bridge(
    ports: FakePorts,
    *,
    result: InvocationResult | None = None,
    arguments: Mapping[str, Any] | None = None,
    csp: CspResult | None = None,
    teardown_timeout_s: float = 2.0,
) -> AppBridge:
    return AppBridge(
        server_id=SERVER,
        view_id="view-1",
        seed=AppSeed(
            arguments=dict(arguments if arguments is not None else {"q": ARG_SECRET}),
            result=result or invocation(structured={"time": "now"}),
        ),
        html=HTML,
        host_info=HOST_INFO,
        host_context=HOST_CONTEXT,
        csp=csp or build_csp(None),
        ports=ports.ports(),
        teardown_timeout_s=teardown_timeout_s,
    )


@asynccontextmanager
async def running(
    ports: FakePorts, *, initialize: bool = True, **kwargs: Any
) -> AsyncIterator[AppBridge]:
    """A bridge inside its context; with ``initialize`` the handshake is done."""
    async with make_bridge(ports, **kwargs) as bridge:
        if initialize:
            await bridge.receive(PROXY_READY)
            await bridge.receive(INITIALIZE)
            await bridge.receive(INITIALIZED)
        yield bridge
