"""Modern-era (2026-07-28) fixture Server on mcp 2.3 (Tech Design §8; test-only code).

Constitution Art. 1 forbids ``mcp.server`` in shipped code; fixtures live under ``tests/``
and never reach the wheel (checked by ``scripts/check_wheel.py``).

Run: ``python modern_server.py [--transport stdio|http] [--port N] [--stderr-interval S]``.

Tools: ``echo`` · ``structured`` (has ``outputSchema``) · ``slow`` (sleeps, cancellable) ·
``progress`` (three progress notifications) · ``ask_name`` (one MRTR elicitation round) ·
``stderr`` (writes lines to stderr) · ``crash`` (``os._exit``) · ``pid`` · ``two_rounds``
(MRTR elicitation then sampling) · ``bad_structured`` (violates its ``outputSchema``) ·
``client_caps`` (the client capabilities the Server sees) · ``regional`` (valid
``x-mcp-header``) · ``bad_header`` (invalid ``x-mcp-header``) · ``grüße`` (non-ASCII name).
``--record-headers PATH`` (HTTP) appends every request's headers to ``PATH`` as JSON lines.
Flags ``--ignore-eof`` / ``--ignore-term`` make the process survive stdin EOF / SIGTERM;
``--spawn-grandchild PIDFILE`` starts a sleeping child process in the same process group and
writes its pid to ``PIDFILE`` (REQ-CON-008 no-orphan test). Resources: ``fixture://hello``
and the template ``fixture://items/{item}``. Prompt: ``greet(name)``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

OBJ: dict[str, Any] = {"type": "object", "properties": {}}
UNICODE_TOOL = "grüße"
TOOLS = [
    types.Tool(
        name="echo",
        description="Echo the text argument.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    types.Tool(
        name="structured",
        description="Return structured content matching the output schema.",
        input_schema=OBJ,
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    ),
    types.Tool(
        name="slow",
        description="Sleep for `seconds` (default 30); cancellable.",
        input_schema={"type": "object", "properties": {"seconds": {"type": "number"}}},
    ),
    types.Tool(name="progress", description="Report three progress steps.", input_schema=OBJ),
    types.Tool(name="ask_name", description="Elicit a name via MRTR.", input_schema=OBJ),
    types.Tool(
        name="stderr",
        description="Write `lines` lines to stderr.",
        input_schema={"type": "object", "properties": {"lines": {"type": "integer"}}},
    ),
    types.Tool(name="pid", description="Return the process id.", input_schema=OBJ),
    types.Tool(name="two_rounds", description="Elicit, then sample.", input_schema=OBJ),
    types.Tool(
        name="bad_structured",
        description="Structured content that violates the output schema.",
        input_schema=OBJ,
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    ),
    types.Tool(
        name="crash",
        description="Exit the process immediately.",
        input_schema={"type": "object", "properties": {"code": {"type": "integer"}}},
    ),
    types.Tool(
        name="client_caps",
        description="Return the client capabilities this Server sees.",
        input_schema=OBJ,
    ),
    types.Tool(
        name="regional",
        description="Echo `region`; the argument is mirrored as the Mcp-Param-Region header.",
        input_schema={
            "type": "object",
            "properties": {"region": {"type": "string", "x-mcp-header": "Region"}},
            "required": ["region"],
        },
    ),
    types.Tool(
        name="bad_header",
        description="Invalid x-mcp-header annotation (on an object): clients must drop it.",
        input_schema={
            "type": "object",
            "properties": {"blob": {"type": "object", "x-mcp-header": "Blob"}},
        },
    ),
    types.Tool(
        name=UNICODE_TOOL, description="Non-ASCII name (base64 Mcp-Name).", input_schema=OBJ
    ),
]


def _text(text: str) -> list[types.ContentBlock]:
    return [types.TextContent(type="text", text=text)]


def _sample(name: str) -> types.CreateMessageRequest:
    return types.CreateMessageRequest(
        params=types.CreateMessageRequestParams(
            messages=[
                types.SamplingMessage(
                    role="user", content=types.TextContent(type="text", text=f"Greet {name}")
                )
            ],
            max_tokens=32,
        )
    )


def _elicit() -> types.ElicitRequest:
    return types.ElicitRequest(
        params=types.ElicitRequestFormParams(
            message="What is your name?",
            requested_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
    )


async def on_list_tools(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(
    ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    args = params.arguments or {}
    match params.name:
        case "echo":
            return types.CallToolResult(content=_text(str(args.get("text", ""))))
        case "structured":
            return types.CallToolResult(
                content=_text(json.dumps({"value": 42})), structured_content={"value": 42}
            )
        case "slow":
            await anyio.sleep(float(args.get("seconds", 30)))
            return types.CallToolResult(content=_text("slept"))
        case "progress":
            for i in range(1, 4):
                await ctx.session.report_progress(float(i), 3.0, f"step {i}")
                await anyio.sleep(0.02)
            return types.CallToolResult(content=_text("done"))
        case "ask_name":
            if params.request_state is None:
                return types.InputRequiredResult(
                    input_requests={"ask": _elicit()}, request_state="asked"
                )
            answer = (params.input_responses or {}).get("ask")
            if isinstance(answer, types.ElicitResult) and answer.action == "accept":
                return types.CallToolResult(
                    content=_text(f"hello {(answer.content or {})['name']}")
                )
            action = getattr(answer, "action", "missing")
            return types.CallToolResult(content=_text(f"no name ({action})"))
        case "pid":
            return types.CallToolResult(
                content=_text(str(os.getpid())), structured_content={"pid": os.getpid()}
            )
        case "bad_structured":
            return types.CallToolResult(
                content=_text("bad"), structured_content={"value": "not an int"}
            )
        case "two_rounds":
            state = params.request_state
            responses = params.input_responses or {}
            await ctx.session.report_progress(0.0 if state is None else 0.5, 1.0, f"leg {state}")
            if state is None:
                return types.InputRequiredResult(
                    input_requests={"ask": _elicit()}, request_state="r1"
                )
            if state == "r1":
                elicit = responses.get("ask")
                name = (getattr(elicit, "content", None) or {}).get("name", "?")
                return types.InputRequiredResult(
                    input_requests={"llm": _sample(str(name))}, request_state=f"r2:{name}"
                )
            sampled = responses.get("llm")
            text = getattr(getattr(sampled, "content", None), "text", "?")
            return types.CallToolResult(content=_text(f"{state[3:]} -> {text}"))
        case "stderr":
            for i in range(int(args.get("lines", 1))):
                sys.stderr.write(f"fixture stderr line {i}\n")
            sys.stderr.flush()
            return types.CallToolResult(content=_text("written"))
        case "client_caps":
            caps = ctx.session.client_capabilities
            dumped = caps.model_dump(mode="json", exclude_none=True) if caps is not None else None
            return types.CallToolResult(content=_text(json.dumps(dumped)))
        case "regional":
            return types.CallToolResult(content=_text(str(args.get("region", ""))))
        case name if name == UNICODE_TOOL:
            return types.CallToolResult(content=_text("hallo"))
        case "crash":
            sys.stderr.write("fixture: crashing\n")
            sys.stderr.flush()
            os._exit(int(args.get("code", 3)))
    return types.CallToolResult(is_error=True, content=_text(f"unknown tool {params.name}"))


async def on_list_resources(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListResourcesResult:
    return types.ListResourcesResult(
        resources=[types.Resource(uri="fixture://hello", name="hello", mime_type="text/plain")]
    )


async def on_list_resource_templates(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListResourceTemplatesResult:
    return types.ListResourceTemplatesResult(
        resource_templates=[
            types.ResourceTemplate(uri_template="fixture://items/{item}", name="item")
        ]
    )


async def on_read_resource(
    ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams
) -> types.ReadResourceResult:
    uri = str(params.uri)
    if uri == "fixture://hello":
        text = "hello world"
    elif uri.startswith("fixture://items/"):
        text = f"item {uri.removeprefix('fixture://items/')}"
    else:
        raise ValueError(f"unknown resource {uri}")
    return types.ReadResourceResult(
        contents=[types.TextResourceContents(uri=uri, text=text, mime_type="text/plain")]
    )


async def on_list_prompts(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListPromptsResult:
    return types.ListPromptsResult(
        prompts=[
            types.Prompt(
                name="greet",
                description="Greet someone.",
                arguments=[types.PromptArgument(name="name", required=True)],
            )
        ]
    )


async def on_get_prompt(
    ctx: ServerRequestContext[Any], params: types.GetPromptRequestParams
) -> types.GetPromptResult:
    name = (params.arguments or {}).get("name", "world")
    return types.GetPromptResult(
        messages=[
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=f"Hi {name}")
            )
        ]
    )


def build_server() -> Server[Any]:
    return Server(
        "fixture-modern",
        version="0.0.1",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_resource_templates=on_list_resource_templates,
        on_read_resource=on_read_resource,
        on_list_prompts=on_list_prompts,
        on_get_prompt=on_get_prompt,
    )


def _chatter(interval: float) -> None:
    n = 0
    while True:
        n += 1
        sys.stderr.write(f"fixture chatter {n}\n")
        sys.stderr.flush()
        time.sleep(interval)


async def _stdio() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--stderr-interval", type=float, default=None)
    parser.add_argument("--ignore-eof", action="store_true")
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--record-headers", default=None, help="append request headers (JSONL)")
    parser.add_argument("--spawn-grandchild", default=None, help="write the child's pid here")
    opts = parser.parse_args()
    if opts.spawn_grandchild:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        Path(opts.spawn_grandchild).write_text(str(child.pid), encoding="utf-8")
    if opts.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if opts.stderr_interval:
        threading.Thread(target=_chatter, args=(opts.stderr_interval,), daemon=True).start()
    if opts.transport == "stdio":
        anyio.run(_stdio)
        while opts.ignore_eof:  # simulate a Server that does not exit on stdin EOF
            time.sleep(1)
    else:
        import uvicorn

        app: Any = build_server().streamable_http_app()
        if opts.record_headers:
            from header_recorder import HeaderRecorder  # script mode: sibling module

            app = HeaderRecorder(app, Path(opts.record_headers))
        uvicorn.run(app, host="127.0.0.1", port=opts.port, log_level="warning")


if __name__ == "__main__":
    main()
