"""Minimal modern fixture on the high-level ``MCPServer`` (implements ``subscriptions/listen``).

Test-only (Constitution Art. 1). Tool: ``pid``.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("fixture-listen")


@server.tool()
def pid() -> int:
    """Return this process id."""
    return os.getpid()


if __name__ == "__main__":
    server.run("stdio")
