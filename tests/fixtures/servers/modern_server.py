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

Catalog fixtures (F2-11…F2-14): the Server serves ``subscriptions/listen`` (an in-memory bus,
so it declares ``listChanged`` for every list). ``add_tool(name)`` registers a tool and
publishes ``ToolsListChanged`` · ``close_listen`` gracefully closes every open listen stream
· ``touch(uri)`` publishes ``ResourceUpdated`` · ``list_counts`` returns how often each list
method was served · ``ui_app`` / ``ui_legacy`` carry ``_meta.ui`` / deprecated
``_meta["ui/resourceUri"]``. Resource ``fixture://blob`` is binary. ``--protocol-versions
V[,V]`` answers ``server/discover`` with ``-32022`` naming only those versions (no overlap);
``--crash-after S`` exits the process ``S`` seconds after start (crash isolation).

Invoker fixtures (F3-08…F3-11): ``slow(seconds, marker)`` writes ``cancelled`` to ``marker``
when its request is cancelled · ``tool_error`` returns ``isError`` · ``rounds(n)`` needs
``n`` MRTR elicitation rounds · ``state_only(n)`` returns ``n`` state-only results ·
``opaque_state`` echoes the ``requestState`` it got back · ``mismatch`` answers its first
call with ``HEADER_MISMATCH`` (``-32020``) · ``noop`` returns its handler time ·
``call_counts`` returns how often each tool was called. Resource ``fixture://ask`` and prompt
``ask_prompt`` need one elicitation round; resource ``fixture://loop`` never stops asking.
"""

from __future__ import annotations

import argparse
import base64
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
from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    ResourceUpdated,
    ToolsListChanged,
)

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
    types.Tool(
        name="progress",
        description="Report `steps` (default 3) progress steps, `interval` seconds apart.",
        input_schema={
            "type": "object",
            "properties": {"steps": {"type": "integer"}, "interval": {"type": "number"}},
        },
    ),
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
    types.Tool(
        name="add_tool",
        description="Register a tool named `name` and publish ToolsListChanged.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
    ),
    types.Tool(name="close_listen", description="Close every listen stream.", input_schema=OBJ),
    types.Tool(
        name="touch",
        description="Publish ResourceUpdated for `uri`.",
        input_schema={"type": "object", "properties": {"uri": {"type": "string"}}},
    ),
    types.Tool(name="list_counts", description="How often each list was served.", input_schema=OBJ),
    types.Tool(name="tool_error", description="Return isError.", input_schema=OBJ),
    types.Tool(
        name="rounds",
        description="Need `n` MRTR elicitation rounds.",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    ),
    types.Tool(
        name="state_only",
        description="Return `n` state-only input-required results.",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    ),
    types.Tool(name="opaque_state", description="Echo the request state.", input_schema=OBJ),
    types.Tool(name="mismatch", description="First call: HEADER_MISMATCH.", input_schema=OBJ),
    types.Tool(name="noop", description="Return the handler time.", input_schema=OBJ),
    types.Tool(name="call_counts", description="How often each tool was called.", input_schema=OBJ),
    types.Tool(
        name="ui_app",
        description="Tool with an MCP Apps UI resource (app-only).",
        input_schema=OBJ,
        _meta={"ui": {"resourceUri": "ui://fixture/app", "visibility": ["app"]}},
    ),
    types.Tool(
        name="ui_legacy",
        description="Tool with the deprecated flat ui/resourceUri key.",
        input_schema=OBJ,
        _meta={"ui/resourceUri": "ui://fixture/legacy"},
    ),
]
BLOB = b"\x00\x01fixture-blob\xff"
BUS = InMemorySubscriptionBus()
LISTEN = ListenHandler(BUS)
COUNTS: dict[str, int] = {"tools": 0, "resources": 0, "resource_templates": 0, "prompts": 0}
CALLS: dict[str, int] = {}
OPAQUE_STATE = 'opaque:\u00e9 "q" \\ \n\t\u2713 {"not": "json"} \U0001f600'
"""A ``requestState`` the Host must echo byte-identical (REQ-HSH-041)."""
MISMATCHED: set[str] = set()


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
    COUNTS["tools"] += 1
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(
    ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
) -> types.CallToolResult | types.InputRequiredResult:
    args = params.arguments or {}
    CALLS[params.name] = CALLS.get(params.name, 0) + 1
    match params.name:
        case "echo":
            return types.CallToolResult(content=_text(str(args.get("text", ""))))
        case "structured":
            return types.CallToolResult(
                content=_text(json.dumps({"value": 42})), structured_content={"value": 42}
            )
        case "slow":
            try:
                await anyio.sleep(float(args.get("seconds", 30)))
            except anyio.get_cancelled_exc_class():
                if marker := args.get("marker"):
                    Path(str(marker)).write_text("cancelled", encoding="utf-8")  # noqa: ASYNC240 - sync on purpose: an await inside the cancelled scope would not run
                raise
            return types.CallToolResult(content=_text("slept"))
        case "tool_error":
            return types.CallToolResult(is_error=True, content=_text("tool failed"))
        case "rounds":
            done = int(params.request_state or "0")
            if done < int(args.get("n", 1)):
                return types.InputRequiredResult(
                    input_requests={f"r{done}": _elicit()}, request_state=str(done + 1)
                )
            return types.CallToolResult(content=_text(f"done after {done}"))
        case "state_only":
            done = int(params.request_state or "0")
            if done < int(args.get("n", 1)):
                return types.InputRequiredResult(request_state=str(done + 1))
            return types.CallToolResult(content=_text(f"state-only {done}"))
        case "opaque_state":
            if params.request_state is None:
                return types.InputRequiredResult(
                    input_requests={"ask": _elicit()}, request_state=OPAQUE_STATE
                )
            same = params.request_state == OPAQUE_STATE
            return types.CallToolResult(
                content=_text(json.dumps({"state": params.request_state, "same": same}))
            )
        case "mismatch":
            if "mismatch" not in MISMATCHED:
                MISMATCHED.add("mismatch")
                raise MCPError(types.HEADER_MISMATCH, "header mismatch (fixture)")
            return types.CallToolResult(content=_text(f"resent after {COUNTS['tools']} lists"))
        case "noop":
            started = time.perf_counter()
            elapsed = time.perf_counter() - started
            return types.CallToolResult(content=[], structured_content={"handler_s": elapsed})
        case "call_counts":
            return types.CallToolResult(content=_text(json.dumps(CALLS)))
        case "progress":
            steps = int(args.get("steps", 3))
            for i in range(1, steps + 1):
                await ctx.session.report_progress(float(i), float(steps), f"step {i}")
                await anyio.sleep(float(args.get("interval", 0.02)))
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
        case "add_tool":
            new = str(args.get("name", "added"))
            TOOLS.append(types.Tool(name=new, description="Added at runtime.", input_schema=OBJ))
            await BUS.publish(ToolsListChanged())
            return types.CallToolResult(content=_text(f"added {new}"))
        case "close_listen":
            LISTEN.close()
            return types.CallToolResult(content=_text("closed"))
        case "touch":
            await BUS.publish(ResourceUpdated(uri=str(args.get("uri", "fixture://hello"))))
            return types.CallToolResult(content=_text("touched"))
        case "list_counts":
            return types.CallToolResult(content=_text(json.dumps(COUNTS)))
        case "ui_app" | "ui_legacy":
            return types.CallToolResult(content=_text("ui"))
        case "crash":
            sys.stderr.write("fixture: crashing\n")
            sys.stderr.flush()
            os._exit(int(args.get("code", 3)))
    return types.CallToolResult(is_error=True, content=_text(f"unknown tool {params.name}"))


async def on_list_resources(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListResourcesResult:
    COUNTS["resources"] += 1
    return types.ListResourcesResult(
        resources=[
            types.Resource(uri="fixture://hello", name="hello", mime_type="text/plain"),
            types.Resource(uri="fixture://blob", name="blob", mime_type="application/octet-stream"),
        ]
    )


async def on_list_resource_templates(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListResourceTemplatesResult:
    COUNTS["resource_templates"] += 1
    return types.ListResourceTemplatesResult(
        resource_templates=[
            types.ResourceTemplate(uri_template="fixture://items/{item}", name="item")
        ]
    )


async def on_read_resource(
    ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams
) -> types.ReadResourceResult | types.InputRequiredResult:
    uri = str(params.uri)
    if uri == "fixture://ask":
        if params.request_state is None:
            return types.InputRequiredResult(
                input_requests={"ask": _elicit()}, request_state="asked"
            )
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, text="answered", mime_type="text/plain")]
        )
    if uri == "fixture://loop":
        done = int(params.request_state or "0")
        return types.InputRequiredResult(request_state=str(done + 1))
    if uri == "fixture://blob":
        data = base64.b64encode(BLOB).decode("ascii")
        return types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(uri=uri, blob=data, mime_type="application/octet-stream")
            ]
        )
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
    COUNTS["prompts"] += 1
    return types.ListPromptsResult(
        prompts=[
            types.Prompt(
                name="greet",
                description="Greet someone.",
                arguments=[types.PromptArgument(name="name", required=True)],
            ),
            types.Prompt(name="ask_prompt", description="Needs one elicitation round."),
        ]
    )


async def on_get_prompt(
    ctx: ServerRequestContext[Any], params: types.GetPromptRequestParams
) -> types.GetPromptResult | types.InputRequiredResult:
    if params.name == "ask_prompt" and params.request_state is None:
        return types.InputRequiredResult(input_requests={"ask": _elicit()}, request_state="asked")
    name = (params.arguments or {}).get("name", "world")
    return types.GetPromptResult(
        messages=[
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=f"Hi {name}")
            )
        ]
    )


def _refuse_versions(versions: list[str]) -> Any:
    async def on_discover(
        ctx: ServerRequestContext[Any], params: types.RequestParams
    ) -> types.DiscoverResult:
        data = {"supported": versions, "requested": ctx.protocol_version}
        raise MCPError(types.UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", data)

    return on_discover


def build_server(protocol_versions: list[str] | None = None) -> Server[Any]:
    server = _build_server()
    if protocol_versions:
        server.add_request_handler(
            "server/discover", types.RequestParams, _refuse_versions(protocol_versions)
        )
    return server


def _build_server() -> Server[Any]:
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
        on_subscriptions_listen=LISTEN,
    )


def _chatter(interval: float) -> None:
    n = 0
    while True:
        n += 1
        sys.stderr.write(f"fixture chatter {n}\n")
        sys.stderr.flush()
        time.sleep(interval)


def _crash_after(seconds: float) -> None:
    time.sleep(seconds)
    sys.stderr.write("fixture: crash-after elapsed\n")
    sys.stderr.flush()
    os._exit(3)


async def _stdio(protocol_versions: list[str] | None) -> None:
    server = build_server(protocol_versions)
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
    parser.add_argument("--protocol-versions", default=None, help="comma-separated; -32022")
    parser.add_argument("--crash-after", type=float, default=None, help="exit after S seconds")
    opts = parser.parse_args()
    versions = opts.protocol_versions.split(",") if opts.protocol_versions else None
    if opts.crash_after is not None:
        threading.Thread(target=_crash_after, args=(opts.crash_after,), daemon=True).start()
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
        anyio.run(_stdio, versions)
        while opts.ignore_eof:  # simulate a Server that does not exit on stdin EOF
            time.sleep(1)
    else:
        import uvicorn

        app: Any = build_server(versions).streamable_http_app()
        if opts.record_headers:
            from header_recorder import HeaderRecorder  # script mode: sibling module

            app = HeaderRecorder(app, Path(opts.record_headers))
        uvicorn.run(app, host="127.0.0.1", port=opts.port, log_level="warning")


if __name__ == "__main__":
    main()
