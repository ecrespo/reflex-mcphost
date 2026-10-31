"""Legacy-era (2025-11-25) fixture Server on ``mcp>=1.30,<2`` (Tech Design §8; test-only).

Runs in its own environment because the project pins mcp 2.3:
``uv run --isolated --no-project --with "mcp>=1.30,<2" python legacy_server.py
[--transport stdio|http] [--port N]``. Uses the ``initialize`` handshake and, over HTTP,
``Mcp-Session-Id`` sessions. Same surface as ``modern_server.py``; elicitation and sampling
are server-initiated requests instead of MRTR.
"""

from __future__ import annotations

import argparse
import os
import sys

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

parser = argparse.ArgumentParser()
parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
parser.add_argument("--port", type=int, default=8000)
opts = parser.parse_args()

mcp = FastMCP("fixture-legacy", host="127.0.0.1", port=opts.port, log_level="WARNING")


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


if __name__ == "__main__":
    mcp.run("streamable-http" if opts.transport == "http" else "stdio")
