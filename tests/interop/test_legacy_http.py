"""Legacy Streamable HTTP sessions (F2-08, F2-09; REQ-CON-014, REQ-CON-015, API §4.5).

Session handling is SDK behaviour (``Mcp-Session-Id`` capture/resend, ``DELETE`` on close,
Art. 2); these tests prove it on the wire. The ``404``-on-session rebuild is the Host's
(Analyze P2-01).
"""

from __future__ import annotations

from typing import Any

import anyio
import mcp_types as types
import pytest
from mcp import MCPError

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.supervisor import ServerSupervisor
from reflex_mcphost.core.events import EventBus
from tests.conftest import RecordedHttp
from tests.integration.connection.test_supervisor import (
    TIMEOUT,
    FakeSleep,
    http_def,
    running,
    wait_state,
)

pytestmark = [pytest.mark.anyio, pytest.mark.interop]
S = ServerState


def _session_ids(records: list[dict[str, Any]]) -> list[str | None]:
    return [r["headers"].get("mcp-session-id") for r in records]


async def _echo(sup: ServerSupervisor, text: str = "hi") -> str:
    async with sup.lease() as client:
        result = await client.call_tool("echo", {"text": text})
    return str(result.content[0].text)  # type: ignore[union-attr]


async def test_session_id_on_every_request_and_delete_on_disconnect(
    legacy_http_recorded: RecordedHttp,
) -> None:
    """REQ-CON-014, REQ-CON-008 (HTTP part): the legacy Server's Mcp-Session-Id is sent on
    every request after initialize, and disconnect terminates the session with DELETE."""
    async with running(http_def(legacy_http_recorded.url)) as (sup, _):
        status = await wait_state(sup, S.READY)
        assert status.era == "legacy"
        assert await _echo(sup) == "hi"
        await sup.stop()
    records = legacy_http_recorded.requests()
    ids = _session_ids(records)
    # the `auto` probe (server/discover) and initialize precede the session
    assert ids[:2] == [None, None]
    assert len(records) > 3
    assert len(set(ids[2:])) == 1
    assert ids[2] is not None
    assert records[-1]["method"] == "DELETE"


async def test_modern_server_never_gets_a_session_id(
    modern_http_recorded: RecordedHttp,
) -> None:
    """REQ-CON-014: no Mcp-Session-Id is ever sent to a modern Server."""
    async with running(http_def(modern_http_recorded.url)) as (sup, _):
        assert (await wait_state(sup, S.READY)).era == "modern"
        await sup.stop()
    records = modern_http_recorded.requests()
    assert records
    assert set(_session_ids(records)) == {None}


async def test_404_on_session_rebuilds_without_state_change(
    legacy_http_recorded: RecordedHttp,
) -> None:
    """REQ-CON-015, API §4.5 (`ready` + legacy HTTP 404 on session id): the request that hit
    the 404 fails with the SDK error; the supervisor re-initializes internally — state stays
    ready, no server.state_changed, no failure counted, one server.log entry."""
    async with running(http_def(legacy_http_recorded.url)) as (sup, rec):
        await wait_state(sup, S.READY)
        before = len(rec.events)
        old_lease = sup.current_lease
        assert (await _echo(sup)).startswith("hi")
        async with sup.lease() as client:
            await client.call_tool("drop_session", {})
            with pytest.raises(MCPError) as info:
                await client.call_tool("echo", {"text": "lost"})
        assert info.value.error.code == types.INVALID_REQUEST
        with anyio.fail_after(TIMEOUT):
            while sup.current_lease in (None, old_lease):  # noqa: ASYNC110 - no lease event
                await anyio.sleep(0.02)
        assert await _echo(sup, "again") == "again"
        assert sup.status.state is S.READY
        assert sup.status.consecutive_failures == 0
        assert len(rec.events) == before
        notes = [e for e in sup.logs.snapshot() if e.logger == "host"]
        assert len(notes) == 1
        assert "session" in notes[0].text
    ids = [i for i in _session_ids(legacy_http_recorded.requests()) if i is not None]
    assert len(set(ids)) == 2  # the dropped session, then a fresh one


async def test_delete_is_sent_even_under_cancellation(
    legacy_http_recorded: RecordedHttp,
) -> None:
    """REQ-CON-009, REQ-CON-014: the Client exit is shielded — cancelling the task group
    that runs the supervisor still terminates the legacy session with DELETE."""
    bus = EventBus()
    with anyio.fail_after(TIMEOUT):
        async with anyio.create_task_group() as tg:
            sup = ServerSupervisor(http_def(legacy_http_recorded.url), events=bus)
            sup.start(tg)
            await sup.wait_for(S.READY)
            tg.cancel_scope.cancel()
    assert legacy_http_recorded.requests()[-1]["method"] == "DELETE"


async def test_404_on_every_session_is_bounded(legacy_http_dropping: RecordedHttp) -> None:
    """REQ-CON-015 (security review): a Server that 404s every session cannot make the Host
    re-initialize forever — after 3 in-place rebuilds without one successful request the
    connection counts as lost: reconnecting with backoff and failure counting."""
    sleep = FakeSleep()
    async with running(http_def(legacy_http_dropping.url), sleep=sleep, rng=lambda: 0.5) as (
        sup,
        _,
    ):
        status = await wait_state(sup, S.RECONNECTING)
        assert status.reason == "transport_error: session_terminated"
        with anyio.fail_after(TIMEOUT):
            while not sleep.delays:  # noqa: ASYNC110 - FakeSleep has no event
                await anyio.sleep(0.02)
        await sup.stop()
    notes = [e for e in sup.logs.snapshot() if "re-initializing" in e.text]
    assert len(notes) <= 3 * (len(sleep.delays) + 1)
    assert sleep.delays[0] == 1.0
