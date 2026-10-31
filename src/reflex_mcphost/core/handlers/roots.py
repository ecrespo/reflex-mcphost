"""Roots handler (REQ-HSH-020…022, API §4.5 ``set_roots``).

*Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*

A Server's roots are exactly the ones the Consumer configured for it (its own definition or
``[overrides.<id>] roots``; default none). Every root is re-checked when served: anything
that is not an absolute ``file://`` URI is dropped with a warning (REQ-HSH-022, a security
invariant). The same core serves the modern ``roots/list`` router slot and the legacy
``list_roots_callback`` (REQ-HSH-045). Not consent-gated, not audited (not an effect).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

import mcp_types as types
from mcp.client import ClientRequestContext
from mcp.client.session import ListRootsFnT
from pydantic import ValidationError

from reflex_mcphost.core.handlers.base import Attribute
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.mrtr import ROOTS, InputContext, InputHandler

__all__ = ["RootsHandler", "RootsLookup", "is_file_root", "require_file_roots"]

RootsLookup = Callable[[str], Sequence[str]]
"""``server_id`` → its effective configured roots."""
_log = get_logger(__name__)


def is_file_root(uri: str) -> bool:
    """``True`` only for an absolute ``file://`` URI (``file:///path`` or
    ``file://host/path``) without query or fragment."""
    if not isinstance(uri, str) or "://" not in uri:
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return (
        parts.scheme.lower() == "file"
        and parts.path.startswith("/")
        and not parts.query
        and not parts.fragment
    )


def require_file_roots(roots: Sequence[str]) -> tuple[str, ...]:
    """The roots as a tuple; ``ValueError`` if any is not an absolute ``file://`` URI."""
    if isinstance(roots, str):
        raise ValueError("roots must be a sequence of file:// URIs, not a string")
    out = tuple(roots)
    for index, uri in enumerate(out):
        if not is_file_root(uri):
            raise ValueError(f"root #{index} is not an absolute file:// URI")
    return out


class RootsHandler:
    """Serves the configured roots of each Server."""

    def __init__(self, roots_of: RootsLookup) -> None:
        self._roots_of = roots_of

    def roots(self, server_id: str) -> list[types.Root]:
        """The Server's valid roots (invalid ones dropped and logged)."""
        log = ContextAdapter(_log, server_id=server_id)
        try:
            configured = tuple(self._roots_of(server_id))
        except Exception as exc:
            log.warning("roots unavailable (%s); serving none", type(exc).__name__)
            return []
        roots: list[types.Root] = []
        for index, uri in enumerate(configured):
            if not is_file_root(uri):
                log.warning("root dropped: #%d is not an absolute file:// URI", index)
                continue
            try:
                roots.append(types.Root(uri=uri))
            except ValidationError:
                log.warning("root dropped: #%d is not a valid file URL", index)
        return roots

    async def handle(self, ctx: InputContext) -> types.ListRootsResult:
        return types.ListRootsResult(roots=self.roots(ctx.server_id))

    def as_input_handler(self) -> InputHandler:
        """The modern router slot ``roots/list``."""

        async def slot(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
            return await self.handle(ctx)

        return slot

    def as_callback(self, attribute: Attribute) -> ListRootsFnT:
        """The legacy SDK ``list_roots_callback``."""

        async def callback(
            context: ClientRequestContext,
        ) -> types.ListRootsResult | types.ErrorData:
            return await self.handle(attribute(ROOTS, str(context.request_id)))

        return callback
