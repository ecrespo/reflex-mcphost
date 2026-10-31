"""Legacy-era (2025-11-25) fixture Server on ``mcp>=1.30,<2`` (Tech Design §8; test-only).

Runs in its own environment because the project pins mcp 2.3:
``uv run --isolated --no-project --with "mcp>=1.30,<2" python legacy_server.py
[--transport stdio|http] [--port N]``. Uses the ``initialize`` handshake and, over HTTP,
``Mcp-Session-Id`` sessions. Same surface as ``modern_server.py``; elicitation and sampling
are server-initiated requests instead of MRTR.

HTTP only: ``--record-headers PATH`` appends each request's headers to ``PATH`` (JSON lines,
``header_recorder``); the ``drop_session`` tool forgets the caller's session, so every later
request carrying that ``Mcp-Session-Id`` gets a plain ``404`` (REQ-CON-015 tests).
``--drop-every-session`` (HTTP) answers ``404`` to every request carrying a session id, so
each new session dies at once (bounded-rebuild test).

Catalog fixtures (F2-12/F2-13): ``add_tool(name)`` registers a tool and sends
``notifications/tools/list_changed`` · ``list_counts`` returns how often each list method was
served · resource ``fixture://blob`` is binary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel import NotificationOptions
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

parser = argparse.ArgumentParser()
parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--record-headers", default=None, help="append request headers (JSONL)")
parser.add_argument("--drop-every-session", action="store_true", help="404 every session")
opts = parser.parse_args()

DROPPED: set[str] = set()
"""Session ids answered with 404 from now on (``drop_session``)."""


class SessionDropper:
    """ASGI middleware: ``404`` (no JSON-RPC body) for requests of a dropped session."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            session_id = headers.get("mcp-session-id")
            if session_id is not None and (opts.drop_every_session or session_id in DROPPED):
                body = b"session not found"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"content-length", b"%d" % len(body)),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


COUNTS: dict[str, int] = {"tools": 0, "resources": 0, "resource_templates": 0, "prompts": 0}


class CountingFastMCP(FastMCP):  # type: ignore[misc]
    """Counts each list request it serves (``list_counts`` tool)."""

    async def list_tools(self) -> Any:
        COUNTS["tools"] += 1
        return await super().list_tools()

    async def list_resources(self) -> Any:
        COUNTS["resources"] += 1
        return await super().list_resources()

    async def list_resource_templates(self) -> Any:
        COUNTS["resource_templates"] += 1
        return await super().list_resource_templates()

    async def list_prompts(self) -> Any:
        COUNTS["prompts"] += 1
        return await super().list_prompts()


mcp = CountingFastMCP("fixture-legacy", host="127.0.0.1", port=opts.port, log_level="WARNING")
_init_options = mcp._mcp_server.create_initialization_options


def _declare_list_changed(*args: Any, **kwargs: Any) -> Any:
    """Declare ``listChanged`` for every list: the fixture does send the notifications."""
    return _init_options(
        NotificationOptions(tools_changed=True, prompts_changed=True, resources_changed=True)
    )


mcp._mcp_server.create_initialization_options = _declare_list_changed


class Name(BaseModel):
    name: str


class Value(BaseModel):
    value: int


@mcp.tool()
def echo(text: str) -> str:
    """Echo the text argument."""
    return text


@mcp.tool()
def structured() -> Value:
    """Return structured content matching the output schema."""
    return Value(value=42)


@mcp.tool()
async def slow(seconds: float = 30) -> str:
    """Sleep for `seconds`; cancellable."""
    await anyio.sleep(seconds)
    return "slept"


@mcp.tool()
async def progress(ctx: Context) -> str:  # type: ignore[type-arg]
    """Report three progress steps."""
    for i in range(1, 4):
        await ctx.report_progress(i, 3, f"step {i}")
        await anyio.sleep(0.02)
    return "done"


@mcp.tool()
async def ask_name(ctx: Context) -> str:  # type: ignore[type-arg]
    """Elicit a name (server-initiated request)."""
    try:
        answer = await ctx.elicit("What is your name?", Name)
    except McpError as exc:  # the Host answered with a JSON-RPC error
        return f"elicit error {exc.error.code}: {exc.error.message}"
    if answer.action == "accept":
        return f"hello {answer.data.name}"
    return f"no name ({answer.action})"


@mcp.tool()
async def sample(ctx: Context) -> str:  # type: ignore[type-arg]
    """Ask the Host for a completion (server-initiated sampling)."""
    from mcp.types import SamplingMessage, TextContent

    try:
        result = await ctx.session.create_message(
            messages=[SamplingMessage(role="user", content=TextContent(type="text", text="hi"))],
            max_tokens=8,
        )
    except McpError as exc:
        return f"sampling error {exc.error.code}: {exc.error.message}"
    return getattr(result.content, "text", "?")


@mcp.tool()
def stderr(lines: int = 1) -> str:
    """Write `lines` lines to stderr."""
    for i in range(lines):
        sys.stderr.write(f"fixture stderr line {i}\n")
    sys.stderr.flush()
    return "written"


@mcp.tool()
def crash(code: int = 3) -> str:
    """Exit the process immediately."""
    os._exit(code)


@mcp.tool()
def pid() -> str:
    """Return the process id."""
    return str(os.getpid())


@mcp.tool()
async def client_caps(ctx: Context) -> str:  # type: ignore[type-arg]
    """Return the client capabilities this Server saw in ``initialize``."""
    params = ctx.session.client_params
    caps = params.capabilities.model_dump(mode="json", exclude_none=True) if params else None
    return json.dumps(caps)


@mcp.tool()
def drop_session(ctx: Context) -> str:  # type: ignore[type-arg]
    """Forget the caller's HTTP session: later requests with its id get 404."""
    request = ctx.request_context.request
    session_id = request.headers.get("mcp-session-id") if request is not None else None
    if session_id:
        DROPPED.add(session_id)
    return f"dropped {session_id}"


@mcp.tool()
async def add_tool(ctx: Context, name: str = "added") -> str:  # type: ignore[type-arg]
    """Register a tool named `name` and notify tools/list_changed."""

    def added() -> str:
        return "added"

    mcp.add_tool(added, name=name, description="Added at runtime.")
    await ctx.session.send_tool_list_changed()
    return f"added {name}"


@mcp.tool()
def list_counts() -> str:
    """How often each list method was served."""
    return json.dumps(COUNTS)


@mcp.resource("fixture://blob", mime_type="application/octet-stream")
def blob() -> bytes:
    return b"\x00\x01fixture-blob\xff"


@mcp.resource("fixture://hello", mime_type="text/plain")
def hello() -> str:
    return "hello world"


@mcp.resource("fixture://items/{item}")
def item(item: str) -> str:
    return f"item {item}"


@mcp.prompt()
def greet(name: str) -> str:
    """Greet someone."""
    return f"Hi {name}"


def _serve_http() -> None:
    import uvicorn

    app: Any = SessionDropper(mcp.streamable_http_app())
    if opts.record_headers:
        from header_recorder import HeaderRecorder  # script mode: sibling module

        app = HeaderRecorder(app, Path(opts.record_headers))
    uvicorn.run(app, host="127.0.0.1", port=opts.port, log_level="warning")


if __name__ == "__main__":
    if opts.transport == "http":
        _serve_http()
    else:
        mcp.run("stdio")
