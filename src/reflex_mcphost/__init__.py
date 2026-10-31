"""reflex-mcphost: an open MCP host built with Reflex (public API re-exports, API Spec §3.6)."""

import logging
from importlib.metadata import PackageNotFoundError, version

# Libraries never configure output (REQ-NFR-040): applications attach their own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

try:
    __version__: str = version("reflex-mcphost")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
    __version__ = "0.0.0"

__all__ = ["__version__"]
