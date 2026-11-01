"""The ``catalog.changed`` publish-time line (F8-02, delta D9; REQ-NFR-004 (b)).

The reference app records the Host's ``catalog.changed`` publish time at DEBUG under
``reflex_mcphost.ui`` only in JSON log mode, so the E2E can measure publish → list DOM
update on the same machine's wall clock. No new public API.
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Iterator

import anyio
import pytest

from reflex_mcphost.app import cli
from reflex_mcphost.core.events import CatalogChanged, EventBus, ServerLog
from reflex_mcphost.core.logging import ROOT_LOGGER
from reflex_mcphost.ui.state import TIMING_LOGGER, log_catalog_publishes

pytestmark = pytest.mark.anyio


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root, timing = logging.getLogger(ROOT_LOGGER), logging.getLogger(TIMING_LOGGER)
    saved = (root.handlers[:], root.level, root.propagate, timing.level)
    try:
        yield
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        root.propagate = saved[2]
        timing.setLevel(saved[3])


def test_timing_logger_is_debug_only_in_json_mode(restore_logging: None) -> None:
    """REQ-NFR-004 (delta D9): JSON log mode enables DEBUG for the timing logger only;
    text mode leaves it at the console's INFO, and other ``reflex_mcphost`` loggers stay at
    INFO in both modes."""
    timing, other = logging.getLogger(TIMING_LOGGER), logging.getLogger(ROOT_LOGGER + ".ui")
    assert TIMING_LOGGER.startswith(ROOT_LOGGER + ".ui.")
    cli.configure_logging(json_lines=False, stream=io.StringIO())
    assert not timing.isEnabledFor(logging.DEBUG)
    cli.configure_logging(json_lines=True, stream=io.StringIO())
    assert timing.isEnabledFor(logging.DEBUG)
    assert not other.isEnabledFor(logging.DEBUG)
    cli.configure_logging(json_lines=False, stream=io.StringIO())
    assert not timing.isEnabledFor(logging.DEBUG)


async def test_publish_line_carries_server_and_wall_clock(restore_logging: None) -> None:
    """REQ-NFR-004 (b): each ``catalog.changed`` gets one JSON DEBUG line naming the Server
    and the wall-clock publish time in ms (taken by an idle dedicated subscriber); other
    events produce no line."""
    stream = io.StringIO()
    cli.configure_logging(json_lines=True, stream=stream)
    bus = EventBus()
    async with anyio.create_task_group() as tg:
        await tg.start(log_catalog_publishes, bus)
        before = time.time() * 1000
        bus.publish(CatalogChanged(server_id="s00", lists=("tools",)))
        bus.publish(ServerLog.model_construct(server_id="s00"))  # ignored (not catalog)
        await anyio.sleep(0.05)
        after = time.time() * 1000
        tg.cancel_scope.cancel()
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(lines) == 1
    (line,) = lines
    assert line["level"] == "DEBUG"
    assert line["logger"] == TIMING_LOGGER
    assert line["server_id"] == "s00"
    assert line["msg"].startswith("catalog.changed published tools epoch_ms=")
    stamp = float(line["msg"].rsplit("=", 1)[1])
    assert before <= stamp <= after


async def test_no_subscriber_without_debug(restore_logging: None) -> None:
    """REQ-NFR-004 (delta D9): outside JSON mode the timing task returns at once and never
    subscribes to the bus (no cost in normal runs)."""
    cli.configure_logging(json_lines=False, stream=io.StringIO())
    bus = EventBus()
    with anyio.fail_after(1):
        async with anyio.create_task_group() as tg:
            await tg.start(log_catalog_publishes, bus)
    assert bus.subscriber_count == 0
