"""Server log notifications into the per-Server ring (F4-04). *Logging is deprecated in the
pinned spec (SEP-2577); compatibility feature.*"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import mcp_types as types
import pytest

from reflex_mcphost.core.connection.stderr import MAX_LINE_BYTES, LogEntry, LogRing
from reflex_mcphost.core.handlers.logging import logging_callback, render_data
from reflex_mcphost.core.secrets import MASK, register_secret

pytestmark = pytest.mark.anyio
T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def note(
    level: types.LoggingLevel, data: object, logger: str | None = "db"
) -> types.LoggingMessageNotificationParams:
    return types.LoggingMessageNotificationParams(level=level, data=data, logger=logger)


async def test_entries_keep_level_logger_and_timestamp() -> None:
    """REQ-HSH-030: each notifications/message becomes a log entry with level, logger and
    timestamp; non-string data is rendered as JSON; every entry reaches the event hook."""
    seen: list[LogEntry] = []
    ring = LogRing(10, on_entry=seen.append, clock=lambda: T0)
    callback = logging_callback(ring)
    await callback(note("warning", "disk almost full"))
    await callback(note("debug", {"rows": 3, "name": "ü"}, logger=None))
    first, second = ring.snapshot()
    assert (first.level, first.logger, first.timestamp, first.text) == (
        "warning",
        "db",
        T0,
        "disk almost full",
    )
    assert (second.level, second.logger) == ("debug", None)
    assert json.loads(second.text) == {"rows": 3, "name": "ü"}
    assert seen == [first, second]


async def test_ring_stays_bounded() -> None:
    """REQ-HSH-030: the ring keeps at most log_buffer_size entries (oldest dropped)."""
    ring = LogRing(3)
    callback = logging_callback(ring)
    for i in range(5):
        await callback(note("info", f"line {i}"))
    assert [e.text for e in ring.snapshot()] == ["line 2", "line 3", "line 4"]


async def test_registered_secret_is_masked() -> None:
    """REQ-HSH-030, Art. 5: a registered secret inside the data is masked before storage."""
    secret = "log-secret-4f9a2c1b7d"  # noqa: S105 - test sentinel
    register_secret(secret)
    ring = LogRing(5)
    await logging_callback(ring)(note("error", {"token": secret}))
    (entry,) = ring.snapshot()
    assert secret not in entry.text
    assert MASK in entry.text


def test_render_is_capped_like_stderr_lines() -> None:
    """REQ-HSH-030: huge data is cut like a stderr line; unserializable data never raises."""
    rendered = render_data("x" * (MAX_LINE_BYTES * 2))
    assert rendered.endswith(" [truncated]")
    assert len(rendered.encode("utf-8")) <= MAX_LINE_BYTES + len(" [truncated]")
    assert "<object object" in render_data(object())
    assert render_data(None) == "null"
