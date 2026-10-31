"""Resource, template and prompt reads on both eras (F2-13; REQ-CAT-005…007, -010) and their
MRTR input requests (F3-10; REQ-HSH-046)."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import mcp_types as types
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.errors import (
    CapabilityNotSupportedError,
    InputRequestError,
    ServerRequestError,
    ServerUnavailableError,
    UnknownEntryError,
    UnknownServerError,
)
from reflex_mcphost.core.mrtr import (
    ELICITATION,
    SAMPLING,
    InputContext,
    SamplingDeniedError,
    current_consumer,
)
from reflex_mcphost.core.types import Consumer, Fqn
from tests.integration.connection.test_supervisor import stdio_def
from tests.integration.test_catalog_refresh import Running, catalog_running

pytestmark = pytest.mark.anyio
BLOB = b"\x00\x01fixture-blob\xff"


@pytest.fixture(params=["modern", "legacy"])
async def run(
    request: pytest.FixtureRequest, modern_stdio: StdioServerParameters
) -> AsyncIterator[Running]:
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        params: StdioServerParameters = request.getfixturevalue("legacy_stdio")
    else:
        params = modern_stdio
    async with catalog_running(stdio_def(params, server_id=request.param)) as running:
        await running.ready(request.param)
        yield running


def _server(run: Running) -> str:
    (server_id,) = run.supervisors
    return server_id


async def test_read_text_resource(run: Running) -> None:
    """REQ-CAT-005: resources/read returns text contents with their MIME type."""
    sid = _server(run)
    result = await run.catalog.read_resource(sid, "fixture://hello")
    assert (result.server_id, result.uri) == (sid, "fixture://hello")
    (content,) = result.contents
    assert isinstance(content, types.TextResourceContents)
    assert (content.text, content.mime_type) == ("hello world", "text/plain")


async def test_read_blob_resource(run: Running) -> None:
    """REQ-CAT-005: a binary resource comes back as base64 blob contents with its MIME
    type."""
    result = await run.catalog.read_resource(_server(run), "fixture://blob")
    (content,) = result.contents
    assert isinstance(content, types.BlobResourceContents)
    assert base64.b64decode(content.blob) == BLOB
    assert content.mime_type == "application/octet-stream"


async def test_read_template_expands_rfc6570(run: Running) -> None:
    """REQ-CAT-006: filling fixture://items/{item} with "a b" expands per RFC 6570
    (percent-encoded) and reads the resulting URI."""
    sid = _server(run)
    result = await run.catalog.read_template(f"{sid}::item", {"item": "a b"})
    assert result.uri == "fixture://items/a%20b"
    (content,) = result.contents
    assert isinstance(content, types.TextResourceContents)
    assert content.text == "item a%20b"


async def test_get_prompt_substitutes_arguments(run: Running) -> None:
    """REQ-CAT-007: prompts/get returns the messages with the argument substituted."""
    sid = _server(run)
    result = await run.catalog.get_prompt(Fqn(server_id=sid, name="greet"), {"name": "Ada"})
    assert str(result.fqn) == f"{sid}::greet"
    (message,) = result.messages
    assert message.role == "user"
    assert isinstance(message.content, types.TextContent)
    assert message.content.text == "Hi Ada"


async def test_read_errors(run: Running) -> None:
    """REQ-CAT-004, API §3.4: unknown FQN → UnknownEntryError; unknown Server →
    UnknownServerError; a Server JSON-RPC error → ServerRequestError whose text never
    echoes the Server's `data`; a stopped Server → ServerUnavailableError."""
    sid = _server(run)
    with pytest.raises(UnknownEntryError):
        await run.catalog.get_prompt(f"{sid}::nope")
    with pytest.raises(UnknownEntryError):
        await run.catalog.read_template(f"{sid}::nope", {})
    with pytest.raises(UnknownServerError):
        await run.catalog.read_resource("ghost", "fixture://hello")
    with pytest.raises(ServerRequestError) as info:
        await run.catalog.read_resource(sid, "fixture://missing")
    assert info.value.server_id == sid
    assert repr(info.value) == f"ServerRequestError(server_id={sid!r}, code={info.value.code})"
    if info.value.data is not None:
        assert str(info.value.data) not in str(info.value)
    await run.supervisors[sid].stop()
    with pytest.raises(ServerUnavailableError) as unavailable:
        await run.catalog.read_resource(sid, "fixture://hello")
    assert unavailable.value.state == "disconnected"
    with pytest.raises(ServerUnavailableError):
        await run.catalog.get_prompt(f"{sid}::greet", {"name": "x"})  # stale entry, not ready


async def test_resource_subscription(run: Running) -> None:
    """REQ-CAT-010 (COULD): modern — a subscribed URI rides on the listen stream and
    notifications/resources/updated becomes one resource.updated event; unsubscribing stops
    it. Legacy fixture declares no resources.subscribe → CapabilityNotSupportedError."""
    sid = _server(run)
    if sid == "legacy":
        with pytest.raises(CapabilityNotSupportedError):
            await run.catalog.subscribe(sid, "fixture://hello")
        return
    await run.catalog.subscribe(sid, "fixture://hello")
    await run.catalog.subscribe(sid, "fixture://hello")  # idempotent
    # the listen stream is reopened with the URI; poll touch until the event arrives
    observed = run.observed
    for _ in range(50):
        await run.call(sid, "touch", {"uri": "fixture://hello"})
        try:
            await observed.until(lambda: bool(observed.updates), within=0.1)
            break
        except TimeoutError:
            continue
    assert observed.updates[0].server_id == sid
    assert observed.updates[0].uri == "fixture://hello"
    await run.catalog.unsubscribe(sid, "fixture://hello")
    await run.catalog.unsubscribe(sid, "fixture://other")  # not subscribed: no-op


# ------------------------------------------------------------------ MRTR inside reads (F3-10)


@pytest.fixture
async def modern_run(modern_stdio: StdioServerParameters) -> AsyncIterator[Running]:
    async with catalog_running(stdio_def(modern_stdio)) as running:
        await running.ready("modern")
        yield running


async def test_read_with_undeclared_input_request_raises(modern_run: Running) -> None:
    """REQ-HSH-046, REQ-HSH-044: with no elicitation handler (Phase 3 declares none) an
    input request inside resources/read or prompts/get raises InputRequestError
    undeclared_input_request."""
    with pytest.raises(InputRequestError) as info:
        await modern_run.catalog.read_resource("modern", "fixture://ask")
    assert (info.value.server_id, info.value.code) == ("modern", "undeclared_input_request")
    with pytest.raises(InputRequestError) as prompt_info:
        await modern_run.catalog.get_prompt("modern::ask_prompt")
    assert prompt_info.value.code == "undeclared_input_request"


async def test_read_input_rounds_exceeded(modern_run: Running) -> None:
    """REQ-HSH-046, REQ-HSH-043: a read that keeps returning input_required past 8 rounds
    raises InputRequestError input_rounds_exceeded."""
    with pytest.raises(InputRequestError) as info:
        await modern_run.catalog.read_resource("modern", "fixture://loop")
    assert info.value.code == "input_rounds_exceeded"


async def test_read_input_request_fulfilled_with_consumer(modern_run: Running) -> None:
    """REQ-HSH-040, API §4.6: a handler fulfils the read's input request, sees the read's
    Consumer, and the read is retried to completion."""
    seen: list[Consumer | None] = []

    async def elicit(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        seen.append(current_consumer.get())
        return types.ElicitResult(action="accept", content={"name": "Ada"})

    modern_run.router.set_handler(ELICITATION, elicit)
    bot = Consumer.agent("bot")
    result = await modern_run.catalog.read_resource("modern", "fixture://ask", consumer=bot)
    (content,) = result.contents
    assert isinstance(content, types.TextResourceContents)
    assert content.text == "answered"
    prompt = await modern_run.catalog.get_prompt("modern::ask_prompt", {"name": "Bo"})
    assert prompt.messages[0].content.text == "Hi Bo"  # type: ignore[union-attr]
    assert seen == [bot, Consumer.human()]


async def test_read_sampling_denied(modern_run: Running) -> None:
    """REQ-HSH-046: a denied input request inside a read raises InputRequestError
    sampling_denied (a refusing handler stands in for the Phase 4 sampling path)."""

    async def deny(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
        raise SamplingDeniedError("denied")

    modern_run.router.set_handler(ELICITATION, deny)
    modern_run.router.set_handler(SAMPLING, deny)
    with pytest.raises(InputRequestError) as info:
        await modern_run.catalog.read_resource("modern", "fixture://ask")
    assert info.value.code == "sampling_denied"
