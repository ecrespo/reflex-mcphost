"""Cursor ``mcp.json``, user and project (``mcpServers``; ``type`` honoured).

Read-only (Constitution Art. 6). API §5.3.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from reflex_mcphost.core.config.common import parse_servers_json
from reflex_mcphost.core.config.models import ConfigOrigin, ParseOutcome


def parse(text: str, origin: ConfigOrigin, *, env: Mapping[str, str], cwd: Path) -> ParseOutcome:
    """Parse the file's ``mcpServers`` table; problems are returned, never raised."""
    del cwd  # ``${workspaceFolder}`` is a VS Code form only
    return parse_servers_json(text, origin, key="mcpServers", env=env, cwd=None)
