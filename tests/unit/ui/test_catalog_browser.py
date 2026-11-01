"""``catalog_browser``, result view models and the invoke flow with a fake Host (F6-05;
API §8, REQ-UI-004)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import anyio
import mcp_types as types
import pytest

from reflex_mcphost.core.catalog import PromptResult, ResourceContents
from reflex_mcphost.core.errors import UnknownEntryError
from reflex_mcphost.core.invoker import InvocationError, InvocationResult
from reflex_mcphost.core.outcomes import InvocationOutcome, Progress
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import actions, models
from reflex_mcphost.ui.components.catalog_browser import catalog_browser
from reflex_mcphost.ui.state import HostState
from tests.unit.core.consent.factories import tool_entry
from tests.unit.ui.rig import published, run_background

pytestmark = pytest.mark.anyio
PNG = base64.b64encode(b"\x89PNG fake").decode("ascii")
NOW = datetime(2026, 11, 1, tzinfo=UTC)
TAB = "tab-1"  # a client id, not a secret
ECHO_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def test_content_rows_are_text_or_safe_data_urls() -> None:
    """REQ-INV-006, Art. 5: text is plain text; images and audio become ``data:`` URLs
    only for an allowed MIME type and valid base64 (SVG and anything else is a
    placeholder naming its type); resource links and embedded text resources are text."""
    blocks = [
        types.TextContent(type="text", text="<script>x</script>"),
        types.ImageContent(type="image", data=PNG, mime_type="image/png"),
        types.ImageContent(type="image", data=PNG, mime_type="image/svg+xml"),
        types.ImageContent(type="image", data="not base64!", mime_type="image/png"),
        types.AudioContent(type="audio", data=PNG, mime_type="audio/wav"),
        types.ResourceLink(type="resource_link", name="doc", uri="file:///doc"),
        types.EmbeddedResource(
            type="resource",
            resource=types.TextResourceContents(
                uri="file:///t", text="inner", mime_type="text/plain"
            ),
        ),
        types.EmbeddedResource(
            type="resource",
            resource=types.BlobResourceContents(
                uri="file:///b", blob=PNG, mime_type="application/zip"
            ),
        ),
    ]
    rows = models.content_rows(blocks)
    assert [r["kind"] for r in rows] == [
        "text",
        "image",
        "placeholder",
        "placeholder",
        "audio",
        "resource_link",
        "text",
        "placeholder",
    ]
    assert rows[0]["text"] == "<script>x</script>"
    assert rows[1]["src"] == f"data:image/png;base64,{PNG}"
    assert "image/svg+xml" in rows[2]["text"]
    assert rows[2]["src"] == rows[3]["src"] == ""
    assert rows[4]["src"].startswith("data:audio/wav;base64,")
    assert rows[5]["text"] == "doc — file:///doc"
    assert (rows[6]["text"], rows[6]["uri"]) == ("inner", "file:///t")
    assert "application/zip" in rows[7]["text"]
    json.dumps(rows)


def test_tool_detail_template_variables_and_result_view() -> None:
    """REQ-UI-004, REQ-CAT-011, REQ-CAT-006: tool details carry the description and
    annotations as plain hints, the schemas as JSON text and the form fields; template
    variables are parsed per RFC 6570; the result view holds outcome, error and blocks."""
    entry = tool_entry("srv::echo", input_schema=ECHO_SCHEMA, description="**Echo**").model_copy(
        update={"annotations": types.ToolAnnotations(read_only_hint=True, title="<i>t</i>")}
    )
    detail = models.tool_detail(entry)
    assert detail["description"] == "**Echo**"
    assert {"name": "readOnlyHint", "value": "true"} in detail["annotations"]
    assert json.loads(detail["input_schema"]) == ECHO_SCHEMA
    assert [f["name"] for f in detail["fields"]] == ["text"]
    assert detail["output_schema"] == ""
    assert models.template_variables("fixture://items/{item}{?q,page}{+path*}{item}") == [
        "item",
        "q",
        "page",
        "path",
    ]
    result = InvocationResult(
        id="inv_1",
        fqn=Fqn.parse("srv::structured"),
        consumer=Consumer.human(),
        outcome=InvocationOutcome.tool_error,
        content=(types.TextContent(type="text", text="tool failed"),),
        structured_content={"value": 42},
        error=InvocationError(code="tool_error", message="the tool reported an error"),
        started_at=NOW,
        duration_ms=5,
    )
    view = models.result_view(result)
    assert (view["outcome"], view["ok"], view["error_code"]) == ("tool_error", False, "tool_error")
    assert view["blocks"][0]["text"] == "tool failed"
    assert json.loads(view["structured"]) == {"value": 42}
    assert models.progress_row(Progress(progress=1, total=3, message="step 1"))["text"] == (
        "1/3 step 1"
    )


class FakeHandle:
    def __init__(self, result: InvocationResult, steps: int = 2) -> None:
        self.id = result.id
        self._result = result
        self._steps = steps
        self.cancelled = False

    async def progress(self) -> AsyncIterator[Progress]:
        for i in range(1, self._steps + 1):
            yield Progress(progress=i, total=self._steps)

    async def result(self) -> InvocationResult:
        return self._result

    async def cancel(self) -> None:
        self.cancelled = True


class FakeCatalog:
    def __init__(self) -> None:
        self.reads: list[tuple[str, Any, Any, Consumer]] = []

    def get_tool(self, fqn: str) -> Any:
        if fqn != "srv::echo":
            raise UnknownEntryError(fqn)
        return tool_entry("srv::echo", input_schema=ECHO_SCHEMA)

    async def read_resource(self, server_id: str, uri: str, *, consumer: Consumer) -> Any:
        self.reads.append(("read_resource", server_id, uri, consumer))
        contents = (types.TextResourceContents(uri=uri, text="hello", mime_type="text/plain"),)
        return ResourceContents(server_id=server_id, uri=uri, contents=contents)

    async def read_template(self, fqn: str, params: Any, *, consumer: Consumer) -> Any:
        self.reads.append(("read_template", fqn, dict(params), consumer))
        uri = f"fixture://items/{params['item']}"
        contents = (types.TextResourceContents(uri=uri, text="item", mime_type="text/plain"),)
        return ResourceContents(server_id="srv", uri=uri, contents=contents)

    async def get_prompt(self, fqn: str, arguments: Any, *, consumer: Consumer) -> Any:
        self.reads.append(("get_prompt", fqn, dict(arguments), consumer))
        message = types.PromptMessage(
            role="user", content=types.TextContent(type="text", text="Hi")
        )
        return PromptResult(fqn=Fqn.parse(fqn), messages=(message,))


class FakeInvoker:
    def __init__(self, result: InvocationResult) -> None:
        self.started: list[tuple[str, Any, Consumer]] = []
        self.handle = FakeHandle(result)

    def start(self, fqn: str, arguments: Any, *, consumer: Consumer) -> FakeHandle:
        self.started.append((fqn, arguments, consumer))
        return self.handle


def _result(
    outcome: InvocationOutcome = InvocationOutcome.success, **extra: Any
) -> InvocationResult:
    return InvocationResult(
        id="inv_9",
        fqn=Fqn.parse("srv::echo"),
        consumer=Consumer.human(),
        outcome=outcome,
        started_at=NOW,
        duration_ms=1,
        **extra,
    )


class FakeHost:
    def __init__(self, result: InvocationResult | None = None) -> None:
        self.catalog = FakeCatalog()
        self.invoker = FakeInvoker(
            result or _result(content=(types.TextContent(type="text", text="hi"),))
        )


class Commits:
    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []

    async def __call__(self, fields: dict[str, Any]) -> None:
        self.history.append(fields)
        self.fields.update(fields)


async def test_invoke_starts_as_human_and_commits_progress_and_result() -> None:
    """REQ-UI-004, REQ-INV-005, REQ-INV-006, Art. 3: Invoke coerces the form and calls
    ``invoker.start`` once as ``human:ui``; progress values and the result are committed;
    the handle is released afterwards."""
    host, commit, handles = FakeHost(), Commits(), {}
    await actions.invoke(host, "srv::echo", {"text": "hi"}, commit, token=TAB, handles=handles)
    assert host.invoker.started == [("srv::echo", {"text": "hi"}, Consumer.human())]
    progress = [h["invocation"]["progress"] for h in commit.history if "invocation" in h]
    assert [p["text"] for p in progress[-1]] == ["1/2", "2/2"]
    final = commit.fields["invocation"]
    assert final["running"] is False
    assert final["result"]["blocks"][0]["text"] == "hi"
    assert handles == {}


async def test_invoke_maps_errors_back_to_fields() -> None:
    """REQ-UI-004, REQ-INV-002: a coercion error stops before the Invoker; an
    ``invalid_arguments`` result maps its issues back to the field; an unknown tool is a
    form-level error."""
    host, commit = FakeHost(), Commits()
    await actions.invoke(host, "srv::echo", {}, commit, token=TAB, handles={})
    assert commit.fields == {"form_errors": {"text": "this field is required"}}
    assert host.invoker.started == []
    issue = {"path": "/text", "keyword": "type", "message": "value is not of type 'string'"}
    bad = _result(
        InvocationOutcome.invalid_arguments,
        error=InvocationError(code="invalid_arguments", message="m", details={"issues": [issue]}),
    )
    host = FakeHost(bad)
    await actions.invoke(
        host, "srv::echo", {models.JSON_FIELD: '{"text": 5}'}, commit, token=TAB, handles={}
    )
    assert host.invoker.started == [("srv::echo", {"text": 5}, Consumer.human())]
    assert commit.fields["form_errors"] == {"text": "value is not of type 'string'"}
    assert commit.fields["invocation"]["result"]["outcome"] == "invalid_arguments"
    await actions.invoke(host, "srv::missing", {}, commit, token=TAB, handles={})
    assert "" in commit.fields["form_errors"]


async def test_cancel_uses_the_tabs_handle() -> None:
    """REQ-INV-004: Cancel calls ``InvocationHandle.cancel`` of this tab's invocation."""
    handle = FakeHandle(_result())
    handles: dict[str, Any] = {"tab": handle}
    assert await actions.cancel_invocation("other", handles) is False
    assert await actions.cancel_invocation("tab", handles) is True
    assert handle.cancelled


async def test_reads_go_through_the_catalog_as_human() -> None:
    """REQ-CAT-005, REQ-CAT-006, REQ-CAT-007: resource reads, template reads (form
    variables) and prompt gets call the Catalog as ``human:ui`` and show the content."""
    host = FakeHost()
    resource = await actions.read_resource(host, "srv", "fixture://hello")
    template = await actions.read_template(host, "srv::item", {"item": "7", "empty": ""})
    prompt = await actions.get_prompt(host, "srv::greet", {"name": "Ada"})
    assert resource["blocks"][0]["text"] == "hello"
    assert (template["title"], template["blocks"][0]["text"]) == ("fixture://items/7", "item")
    assert (prompt["blocks"][0]["role"], prompt["blocks"][0]["text"]) == ("user", "Hi")
    human = Consumer.human()
    assert host.catalog.reads == [
        ("read_resource", "srv", "fixture://hello", human),
        ("read_template", "srv::item", {"item": "7"}, human),
        ("get_prompt", "srv::greet", {"name": "Ada"}, human),
    ]


async def test_state_selects_entries_and_runs_the_background_handlers() -> None:
    """REQ-UI-004: selecting a tool opens its details and form; the background Invoke
    and Read handlers commit into the tab's state; tabs and filters are bounded."""
    host = FakeHost()
    state = HostState(_reflex_internal_init=True)
    state.resource_templates = [
        {"id": "srv::item", "fqn": "srv::item", "server_id": "srv", "uri_template": "x/{item}"}
    ]
    with published(host):
        HostState.select_entry.fn(state, "srv", "srv::echo")
        assert state.catalog_selected["fqn"] == "srv::echo"
        await run_background(HostState.invoke_tool, state, {"text": "hi"})
        assert state.invocation["result"]["outcome"] == "success"
        HostState.set_catalog_tab.fn(state, "templates")
        HostState.select_entry.fn(state, "srv", "srv::item")
        assert state.catalog_selected["variables"] == ["item"]
        assert state.invocation == {}
        await run_background(HostState.read_selected, state, {"item": "3"})
        assert state.read_result["title"] == "fixture://items/3"
        HostState.set_catalog_tab.fn(state, "tools")
        HostState.select_entry.fn(state, "srv", "srv::missing")
        assert state.form_errors[""]
    HostState.set_catalog_tab.fn(state, "bogus")
    assert state.catalog_tab == "tools"
    state.tools = [{"id": f"srv::t{i}", "fqn": f"srv::t{i}"} for i in range(2_000)]
    assert (len(state.catalog_shown), state.catalog_more) == (200, 1_800)
    HostState.set_catalog_filter.fn(state, "t1999")
    assert [r["id"] for r in state.catalog_shown] == ["srv::t1999"]


async def test_cancel_handler_cancels_the_running_invocation() -> None:
    """REQ-INV-004: the Cancel handler reaches the handle of the tab's invocation."""
    state = HostState(_reflex_internal_init=True)
    handle = FakeHandle(_result())
    actions.INVOCATIONS[state.router.session.client_token] = handle
    try:
        with anyio.fail_after(5):
            await run_background(HostState.cancel_invocation, state)
    finally:
        actions.INVOCATIONS.clear()
    assert handle.cancelled


def test_catalog_browser_tabs_and_plain_text() -> None:
    """REQ-UI-004, REQ-CAT-011, Art. 5: tabs Tools / Resources / Templates / Prompts, the
    invoke form, Cancel, outcome and blocks; images only from ``src`` data; no Markdown or
    raw HTML."""
    rendered = str(catalog_browser())
    for needle in (
        "catalog-browser",
        "Tools",
        "Resources",
        "Templates",
        "Prompts",
        "invoke-form",
        "invoke-cancel",
        "invoke-outcome",
        "Hints (untrusted)",
        "structured",
    ):
        assert needle in rendered, needle
    for forbidden in ("Markdown", "dangerouslySetInnerHTML"):
        assert forbidden not in rendered
