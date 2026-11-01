"""Static frontend assets of the UI layer: the MCP App relay (``mcp_app_relay.js``, DD-012).

``relay_library()`` exposes the relay through Reflex's shared-asset mechanism (pinned in
``tests/contract/test_reflex_surface.py``): inside a Reflex app directory (one with
``rxconfig.py``, where Reflex compiles) ``rx.asset(..., shared=True)`` resolves the file
next to this module, links it into the app's ``assets/external/`` directory (unless
``REFLEX_BACKEND_ONLY``) and returns its URL, whose ``importable_path`` is the component's
``library``. Elsewhere (a component rendered in a test or a script) the same path is
returned without touching the file system. Called when the component is built, never at
import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = ["RELAY_FILE", "RELAY_LIBRARY", "relay_library"]

RELAY_FILE: Final = "mcp_app_relay.js"
RELAY_LIBRARY: Final = f"$/public/external/{__name__.replace('.', '/')}/{RELAY_FILE}"
"""The build-time module path Reflex gives the shared asset."""


def _in_app_dir() -> bool:
    """Whether the current directory is a Reflex app directory (where Reflex compiles)."""
    return (Path.cwd() / "rxconfig.py").is_file()


def relay_library() -> str:
    """The build-time module path of the relay (a component ``library``)."""
    if not _in_app_dir():
        return RELAY_LIBRARY
    import reflex as rx

    path = rx.asset(RELAY_FILE, shared=True)
    library: str = path.importable_path
    return library
