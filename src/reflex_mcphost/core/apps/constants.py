"""MCP Apps ``2026-01-26`` wire constants, limits and visibility (API §7, DD-012, delta D5).

The extension id and MIME type are defined here — never imported from ``mcp.server.apps``
(Constitution Art. 1 forbids ``mcp.server`` in shipped code). Limits follow REQ-APP-013 and
API §7 "Limits".
"""

from __future__ import annotations

from typing import Protocol

__all__ = [
    "APPS_PROTOCOL_VERSION",
    "EXTENSION_ID",
    "MAX_HEIGHT_PX",
    "MAX_INFLIGHT",
    "MAX_LINK_CHARS",
    "MAX_LOG_DATA_BYTES",
    "MAX_MESSAGE_BYTES",
    "MAX_METHOD_CHARS",
    "MAX_RESOURCE_BYTES",
    "MIME_TYPE",
    "MIN_HEIGHT_PX",
    "TEARDOWN_TIMEOUT_S",
    "app_visible",
    "is_app_mime",
]

EXTENSION_ID = "io.modelcontextprotocol/ui"
"""The MCP Apps extension identifier (client capability ``extensions`` key, REQ-APP-007)."""
MIME_TYPE = "text/html;profile=mcp-app"
"""The MIME type of an App resource (``ui://…``)."""
APPS_PROTOCOL_VERSION = "2026-01-26"
"""The MCP Apps revision the Host speaks in ``ui/initialize`` (pinned revision set, Art. 2)."""

MAX_RESOURCE_BYTES = 5 * 2**20
"""An App resource (UTF-8 bytes) and a View ``resources/read`` result: at most 5 MiB."""
MAX_MESSAGE_BYTES = 2**20
"""A serialized View → Host message: at most 1 MiB."""
MAX_INFLIGHT = 4
"""View requests (``tools/call``, ``resources/read``) in flight per View."""
TEARDOWN_TIMEOUT_S = 2.0
"""How long ``ui/resource-teardown`` waits for the View's response (REQ-APP-010)."""
MIN_HEIGHT_PX = 80
MAX_HEIGHT_PX = 4000
"""``ui/notifications/size-changed`` height clamp."""
MAX_METHOD_CHARS = 128
MAX_LINK_CHARS = 2048
"""``ui/open-link``: longest URL considered openable (REQ-APP-015)."""
MAX_LOG_DATA_BYTES = 4096
"""A View ``notifications/message`` ``data`` is cut to 4 KiB in the log stream."""

_HTML = "text/html"
_PROFILE = "mcp-app"


class _HasVisibility(Protocol):
    @property
    def visibility(self) -> tuple[str, ...]: ...


def is_app_mime(mime: object) -> bool:
    """Whether ``mime`` is ``text/html`` (case-insensitive) with the single parameter
    ``profile=mcp-app``; other parameters (e.g. ``charset``) are ignored (REQ-APP-013)."""
    if not isinstance(mime, str):
        return False
    media, *params = mime.split(";")
    if media.strip().lower() != _HTML:
        return False
    profiles: list[str] = []
    for param in params:
        name, sep, value = param.partition("=")
        if name.strip().lower() != "profile":
            continue
        if not sep:
            return False
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        profiles.append(value)
    return profiles == [_PROFILE]


def app_visible(entry: _HasVisibility) -> bool:
    """Whether a View may call the tool: its visibility includes ``"app"`` (REQ-APP-008)."""
    return "app" in entry.visibility
