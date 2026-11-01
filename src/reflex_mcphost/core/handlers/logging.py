"""Server log notifications → the per-Server log ring (REQ-HSH-030, API §4.5 ``logs``).

*Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*

``logging_callback(ring)`` is the SDK ``LoggingFnT`` of every Client (registering it
declares nothing): each ``notifications/message`` — modern: on a request's response
stream, only when that request carried a log level; legacy: at any time — becomes a
``LogEntry`` with the notification's level and logger and the ring's timestamp. ``data`` is
rendered as text (JSON for non-strings) and capped like a stderr line; the ring redacts it
(Art. 5) and publishes ``server.log``.
"""

from __future__ import annotations

import json

import mcp_types as types
from mcp.client.session import LoggingFnT

from reflex_mcphost.core.connection.stderr import MAX_LINE_BYTES, LogRing

__all__ = ["logging_callback", "render_data"]

_TRUNCATED = " [truncated]"


def render_data(data: object) -> str:
    """``data`` as one bounded line: strings as is, anything else as JSON."""
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=False, default=repr, sort_keys=True)
        except (TypeError, ValueError):
            text = repr(data)
    raw = text.encode("utf-8")
    if len(raw) > MAX_LINE_BYTES:
        text = raw[:MAX_LINE_BYTES].decode("utf-8", "ignore") + _TRUNCATED
    return text


def logging_callback(ring: LogRing) -> LoggingFnT:
    """The SDK ``logging_callback`` appending to ``ring``."""

    async def callback(params: types.LoggingMessageNotificationParams) -> None:
        ring.add(render_data(params.data), level=params.level, logger=params.logger)

    return callback
