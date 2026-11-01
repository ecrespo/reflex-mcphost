"""MCP Apps fixture Server, both eras (F7-06a; Tech Design §8, test-only code).

Constitution Art. 1 forbids ``mcp.server`` in shipped code; this fixture lives under
``tests/`` and never reaches the wheel (``scripts/check_wheel.py``).

Modern (2026-07-28) on the project's mcp 2.3: ``python apps_server.py``. Legacy
(2025-11-25) on ``mcp>=1.30,<2`` through the isolated-uv pattern of ``legacy_server.py``:
``uv run --isolated --no-project --with "mcp>=1.30,<2" python apps_server.py``. The SDK
generation is detected from the installed ``mcp`` version; tools, resources and results are
written once as wire dicts and validated into either SDK's types.

Tools: ``get-time`` (``_meta.ui.resourceUri = ui://basic``; returns ``structuredContent
{time}``) · ``probe`` (``ui://probe``; echoes its arguments) · ``app_only_echo``
(visibility ``["app"]``) · ``model_only_echo`` (visibility ``["model"]``) · ``slow``
(sleeps ``seconds``, default 30; for cancel / timeout) · ``client_caps`` (``{"mcp_apps":
bool, "extensions": {...}}``: whether the Client declared ``io.modelcontextprotocol/ui``) ·
``call_counts`` (how often each tool was called).

Resources (``text/html;profile=mcp-app`` unless stated): ``ui://basic`` (the vendored
``basic-vanillajs-1.0.1.mcp-app.html``) · ``ui://probe`` (the hand-written ``probe.html``)
· ``ui://wrong-mime`` (``text/html``) · ``ui://too-big`` (5 MiB + 1 byte) · ``ui://bad-csp``
(valid and invalid ``_meta.ui.csp`` entries) · ``ui://external`` (one valid external
domain) · ``ui://bad-utf8`` (a blob that is not UTF-8) · ``ui://missing`` (its only
``contents`` item has another URI).
"""

from __future__ import annotations

import base64
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

import anyio

APPS = Path(__file__).resolve().parent.parent / "apps"
BASIC_HTML = APPS / "basic-vanillajs-1.0.1.mcp-app.html"
PROBE_HTML = APPS / "probe.html"
APP_MIME = "text/html;profile=mcp-app"
EXTENSION_ID = "io.modelcontextprotocol/ui"
MAX_RESOURCE_BYTES = 5 * 2**20
TEXT_ARG: dict[str, Any] = {"type": "object", "properties": {"text": {"type": "string"}}}
OBJ: dict[str, Any] = {"type": "object", "properties": {}}
BAD_CSP: dict[str, list[Any]] = {
    "resourceDomains": [
        "https://cdn.example.com",
        "*",
        "https:",
        "'unsafe-eval'",
        "https://127.0.0.1",
        "https://x.example;script-src *",
    ],
    "connectDomains": ["wss://rt.example.com", "http://api.example.com"],
    "frameDomains": ["https://localhost"],
}
EXTERNAL_CSP: dict[str, list[str]] = {"connectDomains": ["https://api.example.com"]}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get-time",
        "title": "Get Time",
        "description": "Returns the current server time as an ISO 8601 string.",
        "inputSchema": OBJ,
        "outputSchema": {
            "type": "object",
            "properties": {"time": {"type": "string"}},
            "required": ["time"],
        },
        "_meta": {"ui": {"resourceUri": "ui://basic"}},
    },
    {
        "name": "probe",
        "description": "Render the probe View; echoes its arguments.",
        "inputSchema": {"type": "object"},
        "_meta": {"ui": {"resourceUri": "ui://probe"}},
    },
    {
        "name": "app_only_echo",
        "description": "Echo `text`; callable by Apps only.",
        "inputSchema": TEXT_ARG,
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "model_only_echo",
        "description": "Echo `text`; hidden from Apps.",
        "inputSchema": TEXT_ARG,
        "_meta": {"ui": {"visibility": ["model"]}},
    },
    {
        "name": "slow",
        "description": "Sleep `seconds` (default 30); cancellable.",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "client_caps",
        "description": "Whether the Client declared MCP Apps.",
        "inputSchema": OBJ,
    },
    {"name": "call_counts", "description": "How often each tool was called.", "inputSchema": OBJ},
]
RESOURCE_URIS = (
    "ui://basic",
    "ui://probe",
    "ui://wrong-mime",
    "ui://too-big",
    "ui://bad-csp",
    "ui://external",
    "ui://bad-utf8",
    "ui://missing",
)
CALLS: dict[str, int] = {}


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _html_item(
    uri: str, text: str, *, mime: str = APP_MIME, csp: dict[str, Any] | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {"uri": uri, "mimeType": mime, "text": text}
    if csp is not None:
        item["_meta"] = {"ui": {"csp": csp}}
    return item


def read(uri: str) -> list[dict[str, Any]]:
    """The ``contents`` of ``resources/read`` for ``uri`` (wire shape)."""
    page = "<!doctype html><title>fixture</title><p>fixture view</p>"
    match uri:
        case "ui://basic":
            return [_html_item(uri, BASIC_HTML.read_text(encoding="utf-8"))]
        case "ui://probe":
            return [_html_item(uri, PROBE_HTML.read_text(encoding="utf-8"))]
        case "ui://wrong-mime":
            return [_html_item(uri, page, mime="text/html")]
        case "ui://too-big":
            return [_html_item(uri, "a" * (MAX_RESOURCE_BYTES + 1))]
        case "ui://bad-csp":
            return [_html_item(uri, page, csp=BAD_CSP)]
        case "ui://external":
            return [_html_item(uri, page, csp=EXTERNAL_CSP)]
        case "ui://bad-utf8":
            blob = base64.b64encode(b"\xff\xfe<p>").decode("ascii")
            return [{"uri": uri, "mimeType": APP_MIME, "blob": blob}]
        case "ui://missing":
            return [_html_item("ui://elsewhere", page)]
    raise ValueError(f"unknown resource {uri}")


async def call(name: str, args: dict[str, Any], caps: dict[str, Any] | None) -> dict[str, Any]:
    """The ``CallToolResult`` of ``tools/call`` (wire shape); ``caps`` = the Client's
    capabilities as JSON."""
    CALLS[name] = CALLS.get(name, 0) + 1
    match name:
        case "get-time":
            now = "2026-11-01T12:00:00.000Z"
            return {"content": _text(now), "structuredContent": {"time": now}}
        case "probe":
            return {"content": _text(json.dumps(args, sort_keys=True))}
        case "app_only_echo" | "model_only_echo":
            return {"content": _text(str(args.get("text", "")))}
        case "slow":
            await anyio.sleep(float(args.get("seconds", 30)))
            return {"content": _text("slept")}
        case "client_caps":
            extensions = (caps or {}).get("extensions") or {}
            report = {"mcp_apps": EXTENSION_ID in extensions, "extensions": extensions}
            return {"content": _text(json.dumps(report, sort_keys=True))}
        case "call_counts":
            return {"content": _text(json.dumps(CALLS, sort_keys=True))}
    return {"content": _text(f"unknown tool {name}"), "isError": True}


# ---------------------------------------------------------------- mcp 2.x (modern)


def _run_modern() -> None:
    import mcp_types as types
    from mcp.server.lowlevel.server import Server
    from mcp.server.stdio import stdio_server

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in TOOLS])

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        caps = ctx.session.client_capabilities
        dumped = caps.model_dump(mode="json", by_alias=True, exclude_none=True) if caps else None
        result = await call(params.name, params.arguments or {}, dumped)
        return types.CallToolResult.model_validate(result)

    async def on_list_resources(ctx: Any, params: Any) -> Any:
        return types.ListResourcesResult(
            resources=[
                types.Resource(uri=u, name=u.removeprefix("ui://"), mime_type=APP_MIME)
                for u in RESOURCE_URIS
            ]
        )

    async def on_read_resource(ctx: Any, params: Any) -> Any:
        return types.ReadResourceResult.model_validate({"contents": read(str(params.uri))})

    async def on_list_resource_templates(ctx: Any, params: Any) -> Any:
        return types.ListResourceTemplatesResult(resource_templates=[])

    server: Any = Server(
        "fixture-apps",
        version="0.0.1",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
        on_list_resource_templates=on_list_resource_templates,
    )

    async def main() -> None:
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    anyio.run(main)


# ---------------------------------------------------------------- mcp 1.x (legacy)


def _run_legacy() -> None:
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server: Any = Server("fixture-apps-legacy", version="0.0.1")

    async def list_tools(req: Any) -> Any:
        tools = [types.Tool.model_validate(t) for t in TOOLS]
        return types.ServerResult(types.ListToolsResult(tools=tools))

    async def call_tool(req: Any) -> Any:
        params = server.request_context.session.client_params
        caps = (
            params.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
            if params is not None
            else None
        )
        result = await call(req.params.name, req.params.arguments or {}, caps)
        return types.ServerResult(types.CallToolResult.model_validate(result))

    async def list_resources(req: Any) -> Any:
        resources = [
            types.Resource(uri=u, name=u.removeprefix("ui://"), mimeType=APP_MIME)
            for u in RESOURCE_URIS
        ]
        return types.ServerResult(types.ListResourcesResult(resources=resources))

    async def list_templates(req: Any) -> Any:
        return types.ServerResult(types.ListResourceTemplatesResult(resourceTemplates=[]))

    async def read_resource(req: Any) -> Any:
        contents = read(str(req.params.uri))
        return types.ServerResult(types.ReadResourceResult.model_validate({"contents": contents}))

    server.request_handlers[types.ListToolsRequest] = list_tools
    server.request_handlers[types.CallToolRequest] = call_tool
    server.request_handlers[types.ListResourcesRequest] = list_resources
    server.request_handlers[types.ReadResourceRequest] = read_resource
    server.request_handlers[types.ListResourceTemplatesRequest] = list_templates

    async def main() -> None:
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    anyio.run(main)


def main() -> None:
    major = int(importlib.metadata.version("mcp").split(".")[0])
    if major >= 2:
        _run_modern()
    else:
        _run_legacy()


if __name__ == "__main__":
    sys.exit(main())
