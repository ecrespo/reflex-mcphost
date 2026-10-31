"""Resource, template and prompt reads on both eras (F2-13; REQ-CAT-005…007, -010)."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import mcp_types as types
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.errors import (
    CapabilityNotSupportedError,
    ServerRequestError,
    ServerUnavailableError,
    UnknownEntryError,
    UnknownServerError,
)
from reflex_mcphost.core.types import Fqn
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
