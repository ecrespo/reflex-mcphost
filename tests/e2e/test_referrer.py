"""No referrer leaves the Host UI, on Chromium, Firefox and WebKit (F8-12; delta D5).

Every UI response carries ``Referrer-Policy: no-referrer`` (TD DD-017), so a page the UI
opens for a Server — an approved MCP App ``ui/open-link`` and an accepted url-mode
elicitation — learns nothing about the UI origin: the request has no ``Referer`` header
and the opened page sees ``document.referrer === ""``. The opened page is a local HTTP
fixture on ``127.0.0.1`` (another port than the UI, i.e. cross-origin) that records every
request's headers (WebKit sends an empty ``Referer:``, accepted only as exactly ``""``).
One ``reflex-mcphost run --no-browser`` of the module (the MCP Apps
fixture as ``apps`` and the modern fixture as ``modern``) serves every engine.

Local WebKit without its system libraries: the S-3 workaround (``MCPHOST_E2E_WEBKIT_PRELOAD``
plus ``PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1``, see ``tests/e2e/conftest.py``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from playwright.sync_api import BrowserContext, Page, expect

from tests.e2e.conftest import E2E, ENGINES, AppRun, Engines, app_run, apps_argv, modern_argv

pytestmark = pytest.mark.e2e
UI_TIMEOUT_MS = 30_000
expect.set_options(timeout=UI_TIMEOUT_MS)
PROXY = '[data-testid="mcp-app-proxy"]'
SELECT_ATTEMPTS = 5
SELECT_WAIT_MS = 3_000


# ------------------------------------------------------------------ the opened page


@dataclass
class Landing:
    """A local HTTP fixture recording the headers of every request, per path."""

    server: ThreadingHTTPServer
    seen: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def wait(self, path: str, timeout: float = 15.0) -> list[dict[str, str]]:
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                got = list(self.seen.get(path, []))
            if got:
                return got
            if time.monotonic() > deadline:
                raise AssertionError(f"{path} was never requested")
            time.sleep(0.05)


def _handler(landing: list[Landing]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            headers = {k.lower(): v for k, v in self.headers.items()}
            with landing[0].lock:
                landing[0].seen.setdefault(path, []).append(headers)
            body = b"<!doctype html><title>landing</title><p>landed</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


@pytest.fixture(scope="module")
def landing() -> Iterator[Landing]:
    holder: list[Landing] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(holder))
    holder.append(Landing(server=server))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield holder[0]
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def run(warm: E2E) -> Iterator[AppRun]:
    config = warm.write_config("referrer-e2e", {"apps": apps_argv(), "modern": modern_argv()})
    with app_run(warm, config) as started:
        yield started


@pytest.fixture(scope="module", params=ENGINES)
def engine(request: pytest.FixtureRequest, engines: Engines) -> str:
    name = str(request.param)
    engines.get(name)
    return name


@pytest.fixture
def context(engines: Engines, engine: str) -> Iterator[BrowserContext]:
    ctx = engines.get(engine).new_context()
    ctx.set_default_timeout(UI_TIMEOUT_MS)
    yield ctx
    ctx.close()


def _catalog(context: BrowserContext, run: AppRun) -> Page:
    page = context.new_page()
    response = page.goto(f"{run.origin}/catalog")
    assert response is not None
    assert response.headers.get("referrer-policy") == "no-referrer"
    expect(page.locator('[data-testid="catalog-entry"]').first).to_be_visible()
    return page


def _select(page: Page, name: str) -> None:
    """Filter the Catalog and select ``name`` (clicked again while the list re-renders)."""
    page.get_by_placeholder("Filter").fill(name)
    entry = page.locator('[data-testid="catalog-entry"]').filter(
        has=page.get_by_text(name, exact=True)
    )
    form = page.locator('[data-testid="invoke-form"]')
    detail = page.locator('[data-testid="catalog-detail"]')
    for _ in range(SELECT_ATTEMPTS):
        entry.first.click()
        try:
            expect(form).to_be_visible(timeout=SELECT_WAIT_MS)
            expect(detail).to_contain_text(name, timeout=SELECT_WAIT_MS)
        except AssertionError:
            continue
        return
    expect(form).to_be_visible()


def _invoke(page: Page, name: str, **fields: str) -> None:
    _select(page, name)
    form = page.locator('[data-testid="invoke-form"]')
    for key, value in fields.items():
        form.locator(f'input[name="{key}"]').fill(value)
    form.get_by_role("button", name="Invoke").click()


def _allow(page: Page, consumer: str, fqn: str) -> None:
    cards = page.locator('[data-testid="consent-card"]').filter(has_text=consumer)
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text(fqn)
    cards.get_by_role("button", name="Allow once").click()
    expect(cards).to_have_count(0)


def _assert_no_referrer(
    landing: Landing, path: str, opened: Page, *, engine: str, ui_origin: str
) -> None:
    """No ``Referer`` reached the opened page and it sees ``document.referrer === ""``.

    Chromium and Firefox send no ``Referer`` header at all. WebKit (Playwright's WPE build)
    sends the header with an *empty* value under ``no-referrer`` — no information, so on
    WebKit only exactly ``""`` is accepted (deviation, F8-12). On every engine the UI
    origin appears in no request header.
    """
    requests = landing.wait(path)
    for headers in requests:
        if engine == "webkit":
            assert headers.get("referer", "") == "", headers
        else:
            assert "referer" not in headers, headers
        assert not any(ui_origin in value for value in headers.values()), headers
    opened.wait_for_load_state()
    assert opened.url.startswith(landing.url(path))
    assert opened.evaluate("() => document.referrer") == ""


# ------------------------------------------------------------------ tests


def test_open_link_sends_no_referrer(
    run: AppRun, landing: Landing, context: BrowserContext, engine: str
) -> None:
    """REQ-APP-015, REQ-NFR-011 (TD DD-017, §6.1, delta D5): an approved ``ui/open-link``
    to a local page opens it in a new tab whose request has no ``Referer`` and whose
    ``document.referrer`` is empty."""
    path = f"/open-link-{engine}"
    page = _catalog(context, run)
    _invoke(page, "probe")
    _allow(page, "human:ui", "apps::probe")
    proxy = page.locator(PROXY)
    expect(proxy).to_be_visible()
    element = proxy.element_handle()
    proxy_frame = element.content_frame()
    assert proxy_frame is not None
    inner = proxy_frame.wait_for_selector("iframe", state="attached")
    assert inner is not None
    view = inner.content_frame()
    assert view is not None
    view.wait_for_function("() => window.probe !== undefined && window.probe.initialized")
    view.evaluate("(url) => { window.probe.openLink(url); }", landing.url(path))
    expect(page.locator('[data-testid="mcp-app-link-url"]')).to_have_text(landing.url(path))
    with context.expect_page() as popped:
        page.locator('[data-testid="mcp-app-link-approve"]').click()
    _assert_no_referrer(landing, path, popped.value, engine=engine, ui_origin=run.origin)


def test_url_elicitation_sends_no_referrer(
    run: AppRun, landing: Landing, context: BrowserContext, engine: str
) -> None:
    """REQ-HSH-015, REQ-NFR-011 (TD DD-017, §6.1, delta D5): accepting a url-mode
    elicitation ("Open and accept") opens the Server's local URL in a new tab with no
    ``Referer`` header and an empty ``document.referrer``."""
    path = f"/elicit-{engine}"
    page = _catalog(context, run)
    _invoke(page, "elicit_url", url=landing.url(path))
    _allow(page, "human:ui", "modern::elicit_url")
    card = page.locator('[data-testid="elicitation-card"]')
    expect(card.locator('[data-testid="elicitation-url"]')).to_have_text(landing.url(path))
    with context.expect_page() as popped:
        card.locator('[data-testid="elicitation-open"]').click()
    _assert_no_referrer(landing, path, popped.value, engine=engine, ui_origin=run.origin)
    expect(card).to_have_count(0)
