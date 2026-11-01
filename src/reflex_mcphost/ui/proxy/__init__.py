"""Sandbox proxy for MCP App Views served on a distinct origin (DD-012, API §7).

``SandboxProxy`` serves ``proxy.html`` on ``http://localhost:<port>``; ``check_origin_pair``
verifies that ``localhost`` resolves only to loopback. Reflex-free (import-linter).
"""

from reflex_mcphost.ui.proxy.server import (
    LOCALHOST_NOT_LOOPBACK,
    PROXY_PATH,
    VIEW_ID_PATTERN,
    SandboxProxy,
    check_origin_pair,
    view_url,
)

__all__ = [
    "LOCALHOST_NOT_LOOPBACK",
    "PROXY_PATH",
    "VIEW_ID_PATTERN",
    "SandboxProxy",
    "check_origin_pair",
    "view_url",
]
