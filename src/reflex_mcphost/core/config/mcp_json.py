"""Project ``.mcp.json`` (``mcpServers``; ``type`` honoured).

Read-only (Constitution Art. 6). API §5.3.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from reflex_mcphost.core.config.common import parse_servers_json
from reflex_mcphost.core.config.models import ConfigOrigin, ParseOutcome


def parse(text: str, origin: ConfigOrigin, *, env: Mapping[str, str], cwd: Path) -> ParseOutcome:
    """Parse the file's ``mcpServers`` table; problems are returned, never raised."""
    del cwd  # ``${workspaceFolder}`` is a VS Code form only
    return parse_servers_json(text, origin, key="mcpServers", env=env, cwd=None)


def parse_extra(
    text: str, origin: ConfigOrigin, *, env: Mapping[str, str], cwd: Path
) -> ParseOutcome:
    """``[sources] extra`` file: ``mcpServers`` style, or VS Code ``servers`` style when it
    has no ``mcpServers`` key but a ``servers`` key (API §5.2)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None  # parse_servers_json reports it
    if isinstance(data, dict) and "mcpServers" not in data and "servers" in data:
        return parse_servers_json(text, origin, key="servers", env=env, cwd=cwd)
    return parse_servers_json(text, origin, key="mcpServers", env=env, cwd=None)
