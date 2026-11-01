"""Which App resource renders as a View (API §7 "Rendering", REQ-APP-013, delta D5).

``select_view_html(read_result, uri)`` picks the ``contents`` item whose ``uri`` equals the
requested URI, checks its MIME type (``is_app_mime``), takes its ``text`` or decodes its
``blob`` from base64 as UTF-8, and bounds it at 5 MiB of UTF-8. Otherwise the View is not
rendered and the reason names why: ``missing_content``, ``mime_mismatch``,
``invalid_encoding`` or ``too_large`` (``read_failed`` is the caller's, for a read error).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import mcp_types as types

from reflex_mcphost.core.apps.constants import MAX_RESOURCE_BYTES, is_app_mime

__all__ = ["NotRendered", "NotRenderedReason", "ViewResource", "select_view_html"]

NotRenderedReason = Literal[
    "missing_content", "mime_mismatch", "invalid_encoding", "too_large", "read_failed"
]


class _ReadResult(Protocol):
    @property
    def contents(
        self,
    ) -> Sequence[types.TextResourceContents | types.BlobResourceContents]: ...


@dataclass(frozen=True)
class ViewResource:
    """An App resource that may render: its HTML and the metadata the View needs."""

    uri: str
    html: str
    csp_meta: Mapping[str, Any] | None
    """The raw ``_meta.ui.csp`` object (validate with ``build_csp``)."""
    prefers_border: bool = True
    """``_meta.ui.prefersBorder`` (default: border)."""


@dataclass(frozen=True)
class NotRendered:
    """Why no View renders (REQ-APP-013 notice)."""

    reason: NotRenderedReason


def _ui_meta(item: types.TextResourceContents | types.BlobResourceContents) -> Mapping[str, Any]:
    meta = item.meta if isinstance(item.meta, Mapping) else {}
    ui = meta.get("ui")
    return ui if isinstance(ui, Mapping) else {}


def _html(item: types.TextResourceContents | types.BlobResourceContents) -> str | NotRendered:
    if isinstance(item, types.TextResourceContents):
        try:
            size = len(item.text.encode("utf-8"))
        except UnicodeEncodeError:  # lone surrogates: not representable as UTF-8
            return NotRendered("invalid_encoding")
        return item.text if size <= MAX_RESOURCE_BYTES else NotRendered("too_large")
    blob = item.blob
    # base64: 4 characters per 3 bytes; refuse before decoding anything clearly too large
    if (len(blob) // 4) * 3 - 2 > MAX_RESOURCE_BYTES:
        return NotRendered("too_large")
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return NotRendered("invalid_encoding")
    if len(raw) > MAX_RESOURCE_BYTES:
        return NotRendered("too_large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return NotRendered("invalid_encoding")


def select_view_html(read_result: _ReadResult, uri: str) -> ViewResource | NotRendered:
    """The View resource for ``uri`` in a ``resources/read`` result, or why none renders."""
    item = next((c for c in read_result.contents if str(c.uri) == uri), None)
    if item is None:
        return NotRendered("missing_content")
    if not is_app_mime(item.mime_type):
        return NotRendered("mime_mismatch")
    html = _html(item)
    if isinstance(html, NotRendered):
        return html
    ui = _ui_meta(item)
    csp = ui.get("csp")
    border = ui.get("prefersBorder")
    return ViewResource(
        uri=uri,
        html=html,
        csp_meta=csp if isinstance(csp, Mapping) else None,
        prefers_border=border if isinstance(border, bool) else True,
    )
