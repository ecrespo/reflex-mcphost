"""``core.apps.select_view_html``: which UI resource renders (F7-01; REQ-APP-013, API §7)."""

from __future__ import annotations

import base64

import mcp_types as types
import pytest

from reflex_mcphost.core.apps.constants import MAX_RESOURCE_BYTES, MIME_TYPE
from reflex_mcphost.core.apps.resource import NotRendered, ViewResource, select_view_html
from reflex_mcphost.core.catalog import ResourceContents

URI = "ui://basic"


def _text(text: str, *, uri: str = URI, mime: str | None = MIME_TYPE, meta: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    return types.TextResourceContents(uri=uri, mimeType=mime, text=text, _meta=meta)


def _blob(raw: bytes, *, uri: str = URI, mime: str | None = MIME_TYPE):  # type: ignore[no-untyped-def]
    return types.BlobResourceContents(
        uri=uri, mimeType=mime, blob=base64.b64encode(raw).decode("ascii")
    )


def _read(*items: object) -> ResourceContents:
    return ResourceContents(server_id="apps", uri=URI, contents=tuple(items))  # type: ignore[arg-type]


def test_text_item_selected() -> None:
    """REQ-APP-013: the item whose ``uri`` equals the requested URI, with its CSP meta and
    ``prefersBorder`` (default ``True``)."""
    meta = {"ui": {"csp": {"connectDomains": ["https://a.example.com"]}, "prefersBorder": False}}
    view = select_view_html(
        _read(_text("other", uri="ui://other"), _text("<p>hi</p>", meta=meta)), URI
    )
    assert view == ViewResource(
        uri=URI,
        html="<p>hi</p>",
        csp_meta={"connectDomains": ["https://a.example.com"]},
        prefers_border=False,
    )
    plain = select_view_html(_read(_text("<p>x</p>")), URI)
    assert isinstance(plain, ViewResource)
    assert plain.csp_meta is None
    assert plain.prefers_border is True


def test_blob_decoded_as_utf8() -> None:
    """REQ-APP-013: a ``blob`` is decoded from base64 as UTF-8."""
    view = select_view_html(_read(_blob("<p>grüße</p>".encode())), URI)
    assert isinstance(view, ViewResource)
    assert view.html == "<p>grüße</p>"


@pytest.mark.parametrize(
    "mime", ["TEXT/HTML; profile=mcp-app; charset=utf-8", "text/html;profile=mcp-app"]
)
def test_mime_variants_accepted(mime: str) -> None:
    """REQ-APP-013: MIME parsed as a media type, other parameters ignored."""
    assert isinstance(select_view_html(_read(_text("x", mime=mime)), URI), ViewResource)


@pytest.mark.parametrize("mime", ["text/html", None, "text/plain;profile=mcp-app"])
def test_mime_mismatch(mime: str | None) -> None:
    """REQ-APP-013: ``text/html`` alone (or none, or another type) → ``mime_mismatch``."""
    assert select_view_html(_read(_text("x", mime=mime)), URI) == NotRendered("mime_mismatch")


@pytest.mark.parametrize(
    "items",
    [(), (_text("x", uri="ui://other"),)],
    ids=["empty", "other-uri"],
)
def test_missing_content(items: tuple[object, ...]) -> None:
    """REQ-APP-013: no ``contents`` item for the requested URI → ``missing_content``."""
    assert select_view_html(_read(*items), URI) == NotRendered("missing_content")


def test_invalid_encoding() -> None:
    """REQ-APP-013: content that is not valid UTF-8 (or not base64) → ``invalid_encoding``."""
    assert select_view_html(_read(_blob(b"\xff\xfe<p>")), URI) == NotRendered("invalid_encoding")
    bad = types.BlobResourceContents(uri=URI, mimeType=MIME_TYPE, blob="!!not-base64!!")
    assert select_view_html(_read(bad), URI) == NotRendered("invalid_encoding")
    surrogate = _text("a\ud800b")
    assert select_view_html(_read(surrogate), URI) == NotRendered("invalid_encoding")


def test_size_limit_exact() -> None:
    """REQ-APP-013: exactly 5 MiB renders; one byte more → ``too_large`` (text and blob;
    UTF-8 bytes, not characters)."""
    exact = "a" * MAX_RESOURCE_BYTES
    assert isinstance(select_view_html(_read(_text(exact)), URI), ViewResource)
    assert select_view_html(_read(_text(exact + "a")), URI) == NotRendered("too_large")
    multibyte = "é" * (MAX_RESOURCE_BYTES // 2) + "a"  # 2 bytes each → 5 MiB + 1
    assert select_view_html(_read(_text(multibyte)), URI) == NotRendered("too_large")
    assert isinstance(select_view_html(_read(_blob(b"a" * MAX_RESOURCE_BYTES)), URI), ViewResource)
    assert select_view_html(_read(_blob(b"a" * (MAX_RESOURCE_BYTES + 1))), URI) == NotRendered(
        "too_large"
    )


def test_malformed_meta_is_ignored() -> None:
    """REQ-APP-013: a ``_meta.ui`` that is not an object leaves CSP unset and border on."""
    view = select_view_html(_read(_text("x", meta={"ui": "nope"})), URI)
    assert isinstance(view, ViewResource)
    assert (view.csp_meta, view.prefers_border) == (None, True)
    odd = select_view_html(_read(_text("x", meta={"ui": {"prefersBorder": "no"}})), URI)
    assert isinstance(odd, ViewResource)
    assert odd.prefers_border is True
