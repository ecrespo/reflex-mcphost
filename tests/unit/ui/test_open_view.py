"""``ui.actions.open_view``: the UI resource read, selection and CSP for a View (F7-04;
API §7 "Rendering", Flow F step 2; REQ-APP-001/013/014) with a fake Catalog."""

from __future__ import annotations

import base64
from typing import Any

import mcp_types as types
import pytest

from reflex_mcphost.core.apps.resource import NotRendered
from reflex_mcphost.core.catalog import ResourceContents
from reflex_mcphost.core.errors import ServerUnavailableError
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import actions
from tests.unit.ui.test_app_models import ui_tool

pytestmark = pytest.mark.anyio
URI = "ui://view"
MIME = "text/html;profile=mcp-app"
HTML = "<!doctype html><p>view</p>"


class FakeCatalog:
    def __init__(self, contents: list[Any] | Exception) -> None:
        self._contents = contents
        self.reads: list[tuple[str, str, Consumer]] = []

    async def read_resource(self, server_id: str, uri: str, *, consumer: Consumer) -> Any:
        self.reads.append((server_id, uri, consumer))
        if isinstance(self._contents, Exception):
            raise self._contents
        return ResourceContents(server_id=server_id, uri=uri, contents=tuple(self._contents))


class FakeHost:
    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog


def text(uri: str = URI, mime: str = MIME, body: str = HTML, meta: Any = None) -> Any:
    return types.TextResourceContents(uri=uri, mime_type=mime, text=body, _meta=meta)


def blob(raw: bytes) -> Any:
    return types.BlobResourceContents(
        uri=URI, mime_type=MIME, blob=base64.b64encode(raw).decode("ascii")
    )


async def test_view_plan_reads_as_human_ui_and_builds_the_csp() -> None:
    """REQ-APP-001, REQ-APP-014: the resource is read from the tool's own Server as
    ``human:ui``; the plan carries the HTML, the validated CSP and ``prefersBorder``."""
    meta = {
        "ui": {"csp": {"connectDomains": ["https://api.example.com", "*"]}, "prefersBorder": False}
    }
    catalog = FakeCatalog([text(meta=meta)])
    plan = await actions.open_view(FakeHost(catalog), ui_tool())
    assert isinstance(plan, actions.ViewPlan)
    assert catalog.reads == [("srv", URI, Consumer.human())]
    assert plan.resource.html == HTML
    assert plan.resource.prefers_border is False
    assert plan.csp.external is True
    assert "connect-src https://api.example.com" in plan.csp.policy
    assert [d.entry for d in plan.csp.dropped] == ["*"]


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        ([text(uri="ui://elsewhere")], "missing_content"),
        ([text(mime="text/html")], "mime_mismatch"),
        ([blob(b"\xff\xfe")], "invalid_encoding"),
        ([text(body="a" * (5 * 2**20 + 1))], "too_large"),
        (ServerUnavailableError("srv", "not ready"), "read_failed"),
        (ValueError("bad uri"), "read_failed"),
    ],
)
async def test_each_not_rendered_reason(contents: Any, reason: str) -> None:
    """REQ-APP-013: each failure gives its reason (``read_failed`` for a read error)."""
    plan = await actions.open_view(FakeHost(FakeCatalog(contents)), ui_tool())
    assert plan == NotRendered(reason)  # type: ignore[arg-type]


async def test_tool_without_a_ui_resource_reads_nothing() -> None:
    """REQ-APP-001: no ``ui_resource_uri`` → ``missing_content`` and no read."""
    catalog = FakeCatalog([text()])
    assert await actions.open_view(FakeHost(catalog), ui_tool(None)) == NotRendered(
        "missing_content"
    )
    assert catalog.reads == []
