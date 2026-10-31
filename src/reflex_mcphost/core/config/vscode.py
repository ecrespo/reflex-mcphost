"""VS Code ``mcp.json``, user and project (``servers``; ``type: stdio|http|sse``).

Read-only (Constitution Art. 6). ``inputs`` are not prompted for: ``${input:…}`` yields an
``unsupported_variable`` problem; ``${workspaceFolder}`` expands to the project directory
(API §5.3, DD-013).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from reflex_mcphost.core.config.common import parse_servers_json
from reflex_mcphost.core.config.models import ConfigOrigin, ParseOutcome


def parse(text: str, origin: ConfigOrigin, *, env: Mapping[str, str], cwd: Path) -> ParseOutcome:
    """Parse the file's ``servers`` table; problems are returned, never raised."""
    return parse_servers_json(text, origin, key="servers", env=env, cwd=cwd)
