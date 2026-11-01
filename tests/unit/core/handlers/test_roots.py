"""``RootsHandler`` and the roots URI check (F4-03). *Roots are deprecated in the pinned
spec (SEP-2577); compatibility feature.*"""

from __future__ import annotations

import logging

import mcp_types as types
import pytest

from reflex_mcphost.core.handlers.roots import RootsHandler, is_file_root, require_file_roots
from reflex_mcphost.core.mrtr import ROOTS
from tests.unit.core.handlers.fakes import ctx, legacy_context

pytestmark = pytest.mark.anyio


def _uris(result: types.ListRootsResult) -> list[str]:
    return [str(root.uri) for root in result.roots]


async def test_only_configured_file_roots_are_returned(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-HSH-020, REQ-HSH-022: the Server's configured roots are returned; anything that
    is not an absolute file:// URI is dropped with a warning."""
    configured = {
        "srv": (
            "file:///home/me/work",
            "https://example.com/x",
            "file://relative",
            "/home/me/plain",
            "file:///tmp/a%20b",
        )
    }
    handler = RootsHandler(lambda server_id: configured.get(server_id, ()))
    with caplog.at_level(logging.WARNING):
        result = await handler.handle(ctx(ROOTS))
    assert _uris(result) == ["file:///home/me/work", "file:///tmp/a%20b"]
    assert sum("root dropped" in r.getMessage() for r in caplog.records) == 3


async def test_default_is_no_roots() -> None:
    """REQ-HSH-020: a Server without configured roots gets none."""
    handler = RootsHandler(lambda server_id: ())
    assert _uris(await handler.handle(ctx(ROOTS))) == []


async def test_both_adapters_serve_the_same_roots() -> None:
    """REQ-HSH-045: the modern slot and the legacy callback return the same result."""
    handler = RootsHandler(lambda server_id: ("file:///data",))
    modern = await handler.as_input_handler()(ctx(ROOTS), types.ListRootsRequest())
    legacy = await handler.as_callback(lambda m, k: ctx(m))(legacy_context())
    assert isinstance(modern, types.ListRootsResult)
    assert isinstance(legacy, types.ListRootsResult)
    assert _uris(modern) == _uris(legacy) == ["file:///data"]


async def test_lookup_failure_serves_no_roots() -> None:
    """REQ-HSH-022 (fail closed): if the roots cannot be looked up none are exposed."""

    def broken(server_id: str) -> tuple[str, ...]:
        raise KeyError(server_id)

    assert _uris(await RootsHandler(broken).handle(ctx(ROOTS))) == []


@pytest.mark.parametrize(
    ("uri", "ok"),
    [
        ("file:///", True),
        ("file:///home/me", True),
        ("FILE:///home/me", True),
        ("file://host/share", True),
        ("file:relative", False),
        ("file://", False),
        ("https://x", False),
        ("", False),
        ("file:///x?query=1", False),
    ],
)
def test_is_file_root(uri: str, ok: bool) -> None:
    """REQ-HSH-022: only absolute file:// URIs are roots."""
    assert is_file_root(uri) is ok


def test_require_file_roots_rejects_other_schemes() -> None:
    """REQ-HSH-022 (API §4.5 set_roots): any non-file:// URI raises ValueError."""
    assert require_file_roots(["file:///a", "file:///b"]) == ("file:///a", "file:///b")
    with pytest.raises(ValueError, match="file://"):
        require_file_roots(["file:///a", "https://x"])
    with pytest.raises(ValueError, match="not a string"):
        require_file_roots("file:///a")  # a bare string is not a sequence of roots
