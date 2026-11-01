"""MCP Apps in the reference app from the wheel, on Chromium, Firefox and WebKit (F7-06b).

Spike S-3 contract assertions 1-7 and 9 (``specs/plans/spikes/S-3.md``; #8, a ``::1``-only
proxy listener, is covered by the F7-03 socket tests — here the dual-stack proxy renders in
every engine), plus REQ-APP-013/014 notices, the App-hidden call, cancel and teardown seen
by the View and ``ui/open-link``. One ``reflex-mcphost run --no-browser`` of the module (the
``apps_server`` fixture as the own Server ``apps``, modern era, warm workspace) serves every
engine; each test gets a fresh browser context. The isolation and forgery assertions are
the S-3 contract and are never relaxed: a failure stops the phase for a Delta Spec.

Observation points, none of which changes what the product does:

- **Transcript.** A context init script runs in the sandbox proxy frame only
  (``http://localhost:<p>/proxy``) and registers the first ``message`` listener there, so it
  sees every message in delivery order: ``down`` (Host UI → proxy → View) and ``up``
  (View → proxy → Host UI). It logs a summary (method, id, error code, ``params.reason``;
  never params) to the console with ``TAG``; the test reads it from the page's console
  events, which survive the View's teardown.
- **Invoker calls.** Every ``Invoker`` attempt writes exactly one audit record (Art. 3), so
  the module's ``audit_record`` table (read-only SQLite connection) counts them.
- **UI secrets.** Each context gets an ``HttpOnly`` and a script-visible cookie for the UI
  origin and a ``localStorage`` key on it; the proxy and the View must see none of them.

Local WebKit without its system libraries: the S-3 workaround (``MCPHOST_E2E_WEBKIT_PRELOAD``
plus ``PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1``, see ``tests/e2e/conftest.py``).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import (
    BrowserContext,
    ConsoleMessage,
    Frame,
    Page,
    Request,
    Route,
    WebSocket,
    expect,
)

from tests.e2e.conftest import E2E, ENGINES, AppRun, Engines, app_run, apps_argv

pytestmark = pytest.mark.e2e
UI_TIMEOUT_MS = 30_000
expect.set_options(timeout=UI_TIMEOUT_MS)
SERVER = "apps"
TIME = "2026-11-01T12:00:00.000Z"
"""``get-time`` of the fixture always returns this time."""
TAG = "__mcphost_e2e__"
SECRET_HTTP = "SECRET-HTTP-7f3a"  # noqa: S105 - a planted marker, not a credential
SECRET_JS = "SECRET-JS-91c2"  # noqa: S105 - a planted marker, not a credential
SECRET_LS = "SECRET-LS-0d4e"  # noqa: S105 - a planted marker, not a credential
SECRETS = (SECRET_HTTP, SECRET_JS, SECRET_LS)
PROXY = '[data-testid="mcp-app-proxy"]'
NOTICE = '[data-testid="mcp-app-notice"]'
SELECT_ATTEMPTS = 5
SELECT_WAIT_MS = 3_000
SETTLE_MS = 1_500
"""How long a forged message gets to (not) cause an effect."""
EVAL_CSP = (
    re.compile(r"\beval\b", re.IGNORECASE),
    re.compile(r"content.security.policy|unsafe-eval|script-src", re.IGNORECASE),
)
"""S-3 #9: the vendored bundle's ``script-src eval`` CSP violations (zod's JIT probe;
Firefox reports two, Chromium and WebKit none) are the only tolerated View console errors
(the App needs no ``'unsafe-eval'``)."""

TRANSCRIPT_SCRIPT = (
    """(() => {
  if (location.hostname !== "localhost" || location.pathname !== "/proxy") return;
  const view = new URLSearchParams(location.search).get("view") || "";
  function summary(data) {
    let d = data;
    if (typeof d === "string") { try { d = JSON.parse(d); } catch (e) { return { raw: true }; } }
    if (d === null || typeof d !== "object") return { raw: true };
    const s = { method: typeof d.method === "string" ? d.method : null,
                id: d.id === undefined ? null : d.id };
    if (d.error && typeof d.error === "object") s.error = d.error.code;
    if ("result" in d) s.result = true;
    if (d.params && typeof d.params === "object" && typeof d.params.reason === "string") {
      s.reason = d.params.reason;
    }
    return s;
  }
  window.addEventListener("message", (event) => {
    const dir = event.source === window.parent ? "down" : "up";
    console.log(\""""
    + TAG
    + """\" + JSON.stringify(Object.assign({ view, dir }, summary(event.data))));
  });
})();"""
)

_VIEW_ISOLATION = """() => {
  const out = { origin: String(self.origin) };
  try { window.localStorage.getItem("x"); out.localStorage = "allowed"; }
  catch (e) { out.localStorage = "threw"; }
  try { out.cookie = String(document.cookie); } catch (e) { out.cookie = "threw"; }
  try { void window.parent.document.title; out.parent = "allowed"; }
  catch (e) { out.parent = "threw"; }
  try { void window.top.document.title; out.top = "allowed"; } catch (e) { out.top = "threw"; }
  return out;
}"""

_PROXY_ISOLATION = """() => {
  const keys = [];
  for (let i = 0; i < window.localStorage.length; i++) keys.push(window.localStorage.key(i));
  const values = keys.map((k) => String(window.localStorage.getItem(k)));
  let parent = "allowed";
  try { void window.parent.document.title; } catch (e) { parent = "threw"; }
  return { cookie: String(document.cookie), keys, values, parent };
}"""


def _forged_call(tag: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"forged-{tag}",
        "method": "tools/call",
        "params": {"name": "app_only_echo", "arguments": {"text": f"forged-{tag}"}},
    }


FORGED_READY: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "ui/notifications/sandbox-proxy-ready",
    "params": {},
}
_POST_BOTH = """(m) => {
  const target = window.opener !== null ? window.opener : window.parent;
  target.postMessage(m, "*");
  target.postMessage(JSON.stringify(m), "*");
}"""


# ------------------------------------------------------------------ fixtures


@dataclass
class Apps:
    """The module's app run and its audit database."""

    run: AppRun
    db: Path

    def audit(self) -> list[dict[str, Any]]:
        """Every audit record, oldest first (read-only connection)."""
        with closing(sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT invocation_id, status, consumer_kind, consumer_name, fqn, outcome, "
                "error_code, decision_source FROM audit_record ORDER BY started_at_us"
            ).fetchall()
        return [dict(r) for r in rows]

    def app_records(self) -> list[dict[str, Any]]:
        return [r for r in self.audit() if r["consumer_kind"] == "app"]


@pytest.fixture(scope="module")
def apps(warm: E2E) -> Iterator[Apps]:
    config = warm.write_config("apps-e2e", {SERVER: apps_argv()})
    with app_run(warm, config) as run:
        yield Apps(run=run, db=config.parent / "data" / "mcphost.db")


@pytest.fixture(scope="module", params=ENGINES)
def engine(request: pytest.FixtureRequest, engines: Engines) -> str:
    """Each engine in turn (the module's tests are grouped per engine)."""
    name = str(request.param)
    engines.get(name)
    return name


@dataclass
class Tab:
    """One Host UI tab with its console (transcript included) and proxy requests."""

    page: Page
    apps: Apps
    console: list[tuple[str, str, str]] = field(default_factory=list)
    proxy_requests: list[Request] = field(default_factory=list)

    def transcript(self, view: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind, text, _ in list(self.console):
            if kind == "log" and text.startswith(TAG):
                entry = json.loads(text[len(TAG) :])
                if entry["view"] == view:
                    out.append(entry)
        return out

    def wait_transcript(
        self, view: str, until: Callable[[list[dict[str, Any]]], bool], timeout: float = 15.0
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while True:
            entries = self.transcript(view)
            if until(entries):
                return entries
            if time.monotonic() > deadline:
                raise AssertionError(f"transcript of {view} never matched: {entries}")
            self.page.wait_for_timeout(100)

    def view_errors(self) -> list[str]:
        """Console errors that do not come from the Host UI's own pages."""
        origin = self.apps.run.origin
        return [
            text
            for kind, text, url in list(self.console)
            if kind == "error" and not url.startswith(origin)
        ]


class Context:
    """A fresh browser context of ``engine`` with the transcript and the UI secrets."""

    def __init__(self, engines: Engines, engine: str, apps: Apps) -> None:
        self.apps = apps
        self.ctx: BrowserContext = engines.get(engine).new_context()
        self.ctx.set_default_timeout(UI_TIMEOUT_MS)
        self.ctx.add_init_script(TRANSCRIPT_SCRIPT)
        origin = apps.run.origin
        self.ctx.add_cookies(
            [
                {"name": "ui_http", "value": SECRET_HTTP, "url": origin, "httpOnly": True},
                {"name": "ui_js", "value": SECRET_JS, "url": origin},
            ]
        )
        self.ctx.route(re.compile(r"^https://example\.com/"), _example)

    def tab(self) -> Tab:
        """A Catalog tab (the UI's ``localStorage`` secret set once it has loaded)."""
        page = self.ctx.new_page()
        tab = Tab(page=page, apps=self.apps)
        page.on("console", lambda m: tab.console.append(_console(m)))
        page.on(
            "request",
            lambda r: tab.proxy_requests.append(r) if "/proxy?view=" in r.url else None,
        )
        page.goto(f"{self.apps.run.origin}/catalog")
        expect(page.locator('[data-testid="catalog-entry"]').first).to_be_visible()
        page.evaluate("(v) => window.localStorage.setItem('ui_secret', v)", SECRET_LS)
        return tab


def _console(message: ConsoleMessage) -> tuple[str, str, str]:
    location = message.location or {}
    return (message.type, message.text, str(location.get("url", "")))


def _example(route: Route) -> None:
    """``https://example.com`` without the network (the opened tab's content is a stub)."""
    route.fulfill(status=200, content_type="text/html", body="<title>example</title><p>ok</p>")


@pytest.fixture
def browser_context(engines: Engines, engine: str, apps: Apps) -> Iterator[Context]:
    context = Context(engines, engine, apps)
    yield context
    context.ctx.close()


# ------------------------------------------------------------------ UI steps


def _select(tab: Tab, name: str) -> None:
    """Filter the Catalog and select ``name``. The filter round trip re-renders the list, so
    a click can land on a node that is being replaced (seen in Firefox): click again until
    the entry's invoke form is shown."""
    page = tab.page
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


def _invoke(tab: Tab, **fields: str) -> None:
    form = tab.page.locator('[data-testid="invoke-form"]')
    for name, value in fields.items():
        form.locator(f'input[name="{name}"]').fill(value)
    form.get_by_role("button", name="Invoke").click()


def _allow(tab: Tab, consumer: str, fqn: str) -> None:
    """Answer the one consent card of ``consumer`` for ``fqn`` with "Allow once"."""
    cards = tab.page.locator('[data-testid="consent-card"]').filter(has_text=consumer)
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text(fqn)
    cards.get_by_role("button", name="Allow once").click()
    expect(cards).to_have_count(0)


def _render(tab: Tab, tool: str, **fields: str) -> None:
    """Select and invoke ``tool`` from the UI and allow the ``human:ui`` call."""
    _select(tab, tool)
    _invoke(tab, **fields)
    _allow(tab, "human:ui", f"{SERVER}::{tool}")


@dataclass
class View:
    proxy_element: Any
    proxy: Frame
    inner_element: Any
    frame: Frame
    view_id: str
    proxy_origin: str


def _view(tab: Tab) -> View:
    """The rendered View's frames (the proxy iframe and its inner ``srcdoc`` frame)."""
    locator = tab.page.locator(PROXY)
    expect(locator).to_be_visible()
    element = locator.element_handle()
    src = element.get_attribute("src") or ""
    parts = urlsplit(src)
    (view_id,) = parse_qs(parts.query)["view"]
    proxy = element.content_frame()
    assert proxy is not None
    inner = proxy.wait_for_selector("iframe", state="attached")
    assert inner is not None
    frame = inner.content_frame()
    assert frame is not None
    return View(element, proxy, inner, frame, view_id, f"{parts.scheme}://{parts.netloc}")


def _probe(tab: Tab, tool: str = "probe", **fields: str) -> View:
    _render(tab, tool, **fields)
    view = _view(tab)
    view.frame.wait_for_function("() => window.probe !== undefined && window.probe.initialized")
    return view


def _down(entries: list[dict[str, Any]], method: str) -> list[dict[str, Any]]:
    return [e for e in entries if e["dir"] == "down" and e["method"] == method]


def _wait_app_records(apps: Apps, count: int, page: Page, timeout: float = 15.0) -> list[Any]:
    """The App audit records once ``count`` exist and all are finished."""
    deadline = time.monotonic() + timeout
    while True:
        records = apps.app_records()
        if len(records) >= count and all(r["status"] == "finished" for r in records):
            return records
        if time.monotonic() > deadline:
            raise AssertionError(f"expected {count} finished App records: {records}")
        page.wait_for_timeout(100)


def _assert_view_console_clean(tab: Tab) -> None:
    """S-3 #9: only ``script-src eval`` CSP errors are tolerated from the View/proxy."""
    others = [text for text in tab.view_errors() if not all(r.search(text) for r in EVAL_CSP)]
    assert others == [], others


# ------------------------------------------------------------------ tests


def test_view_frames_csp_and_handshake(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-001, REQ-APP-002, REQ-APP-003, REQ-APP-006, REQ-APP-011 (S-3 #1, #2, #9): an
    allowed UI invocation of ``get-time`` renders the vendored App in the proxy iframe
    (``sandbox="allow-scripts allow-same-origin"``, ``allow=""``, ``http://localhost:<p>``,
    not the UI origin) whose inner frame is ``srcdoc`` with ``sandbox="allow-scripts"``,
    no permissions, and the CSP ``<meta>`` as the first child of ``<head>`` (``object-src
    'none'``, ``frame-src 'none'``, no ``'unsafe-eval'``); nothing reaches the View before
    ``ui/notifications/initialized``; ``tool-input`` then ``tool-result``, once each; the
    View shows the result; the only View console errors are ``script-src eval``."""
    tab = browser_context.tab()
    _render(tab, "get-time")
    view = _view(tab)
    proxy = view.proxy_element
    assert proxy.get_attribute("sandbox") == "allow-scripts allow-same-origin"
    assert proxy.get_attribute("allow") == ""
    port = urlsplit(view.proxy_origin).port
    assert view.proxy_origin == f"http://localhost:{port}"
    assert port != apps.run.port
    inner = view.inner_element
    assert inner.get_attribute("sandbox") == "allow-scripts"
    assert inner.get_attribute("allow") == ""
    assert inner.get_attribute("src") is None
    assert inner.evaluate("(el) => el.hasAttribute('srcdoc')") is True
    first = view.frame.evaluate(
        "() => { const m = document.head.firstElementChild;"
        " return [m.tagName, m.getAttribute('http-equiv'), m.getAttribute('content')]; }"
    )
    assert first[0] == "META"
    assert first[1] == "Content-Security-Policy"
    csp = first[2]
    assert "object-src 'none'" in csp
    assert "frame-src 'none'" in csp
    assert "unsafe-eval" not in csp
    app = tab.page.frame_locator(PROXY).frame_locator("iframe")
    expect(app.locator("#server-time")).to_have_text(TIME)

    def seeded(entries: list[dict[str, Any]]) -> bool:
        return bool(_down(entries, "ui/notifications/tool-result"))

    entries = tab.wait_transcript(view.view_id, seeded)
    initialized = next(
        i
        for i, e in enumerate(entries)
        if e["dir"] == "up" and e["method"] == "ui/notifications/initialized"
    )
    before = [e for e in entries[:initialized] if e["dir"] == "down"]
    early = [
        e
        for e in before
        if e["method"] is not None and not e["method"].startswith("ui/notifications/sandbox-")
    ]
    assert early == [], before  # replies (the ui/initialize answer) only
    assert len(_down(entries, "ui/notifications/sandbox-resource-ready")) == 1
    methods = [e["method"] for e in entries if e["dir"] == "down" and e["method"]]
    assert methods.count("ui/notifications/tool-input") == 1
    assert methods.count("ui/notifications/tool-result") == 1
    assert methods.index("ui/notifications/tool-input") < methods.index(
        "ui/notifications/tool-result"
    )
    tab.page.wait_for_timeout(500)
    observed = [text for _, text, url in list(tab.console) if url == "about:srcdoc"]
    assert any("Received tool call result" in text for text in observed), observed
    _assert_view_console_clean(tab)


def test_app_button_is_one_consented_audited_invoker_call(
    apps: Apps, browser_context: Context
) -> None:
    """REQ-APP-004 (S-3 #3, Art. 3): clicking the App's "Get Server Time" raises one
    consent card for ``app:apps``; after "Allow once" exactly one ``Invoker`` attempt is
    audited for the Consumer ``app:apps`` (``apps::get-time``, ``success``) and the View
    shows its result."""
    tab = browser_context.tab()
    _render(tab, "get-time")
    app = tab.page.frame_locator(PROXY).frame_locator("iframe")
    expect(app.locator("#server-time")).to_have_text(TIME)
    view = _view(tab)
    view.frame.evaluate("() => { document.getElementById('server-time').textContent = '-'; }")
    before = len(apps.app_records())
    app.locator("#get-time-btn").click()
    _allow(tab, "app:apps", f"{SERVER}::get-time")
    expect(app.locator("#server-time")).to_have_text(TIME)
    records = _wait_app_records(apps, before + 1, tab.page)[before:]
    tab.page.wait_for_timeout(SETTLE_MS)
    assert apps.app_records()[before:] == records
    assert [(r["consumer_name"], r["fqn"], r["outcome"]) for r in records] == [
        (SERVER, f"{SERVER}::get-time", "success")
    ]
    _assert_view_console_clean(tab)


def test_view_and_proxy_are_isolated_from_the_ui_origin(
    apps: Apps, browser_context: Context
) -> None:
    """REQ-APP-002, REQ-APP-011 (S-3 #4, #5; Art. 5, DD-012): inside the View
    ``self.origin === "null"``, ``localStorage`` throws, ``document.cookie`` holds no UI
    cookie (WebKit returns ``""``, others throw), ``parent.document`` and ``top.document``
    throw; inside the proxy frame ``document.cookie`` and ``localStorage`` carry no UI
    cookie or key and ``parent.document`` throws; the proxy request carried no UI cookie."""
    tab = browser_context.tab()
    ui_keys = tab.page.evaluate("() => Object.keys(window.localStorage)")
    assert "ui_secret" in ui_keys
    ui_cookie = tab.page.evaluate("() => document.cookie")
    assert SECRET_JS in ui_cookie  # the seeded UI secrets are really there
    _render(tab, "get-time")
    view = _view(tab)
    app = tab.page.frame_locator(PROXY).frame_locator("iframe")
    expect(app.locator("#server-time")).to_have_text(TIME)
    inside = view.frame.evaluate(_VIEW_ISOLATION)
    assert inside["origin"] == "null"
    assert inside["localStorage"] == "threw"
    assert inside["cookie"] == "threw" or not any(s in inside["cookie"] for s in SECRETS)
    assert (inside["parent"], inside["top"]) == ("threw", "threw")
    proxied = view.proxy.evaluate(_PROXY_ISOLATION)
    assert not any(s in proxied["cookie"] for s in SECRETS), proxied
    assert not set(proxied["keys"]) & set(ui_keys), proxied
    assert not any(s in v for v in proxied["values"] for s in SECRETS), proxied
    assert proxied["parent"] == "threw"
    requests = [r for r in tab.proxy_requests if view.view_id in r.url]
    assert requests, "the proxy request was not observed"
    for request in requests:
        headers = request.all_headers()
        assert not any(s in value for value in headers.values() for s in SECRETS), headers


def test_forged_messages_never_reach_the_invoker(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-003, REQ-APP-004 (S-3 #6; Art. 3, Art. 5): a ``tools/call`` forged by a
    sibling iframe on the proxy origin, by a popup through ``window.opener`` (proxy origin)
    and by the View posting straight to ``top`` never reaches the Invoker (no consent card,
    no audit record); forged ``sandbox-proxy-ready`` messages (sibling page, sibling post,
    View through its proxy) give no second ``sandbox-resource-ready``; the View's own
    channel still works afterwards (one consented ``app_only_echo``)."""
    tab = browser_context.tab()
    page = tab.page
    view = _probe(tab)
    cards = page.locator('[data-testid="consent-card"]').filter(has_text="app:apps")
    before = len(apps.app_records())
    proxy_url = f"{view.proxy_origin}/proxy?view=forger"
    # (a) a sibling iframe on the proxy origin (the real proxy page: it also posts its own
    # sandbox-proxy-ready to the UI)
    page.evaluate(
        "(url) => { const f = document.createElement('iframe'); f.id = 'forger';"
        " f.src = url; document.body.appendChild(f); }",
        proxy_url,
    )
    sibling = page.locator("#forger").element_handle().content_frame()
    assert sibling is not None
    sibling.wait_for_load_state()
    assert sibling.evaluate("() => self.origin") == view.proxy_origin
    sibling.evaluate(_POST_BOTH, _forged_call("sibling"))
    sibling.evaluate(_POST_BOTH, FORGED_READY)
    # (b) a popup on the proxy origin posting to window.opener
    with browser_context.ctx.expect_page() as popped:
        page.evaluate("(url) => { window.__forger = window.open(url, 'forger'); }", proxy_url)
    popup = popped.value
    popup.wait_for_load_state()
    assert popup.evaluate("() => self.origin") == view.proxy_origin
    assert popup.evaluate("() => window.opener !== null") is True
    popup.evaluate(_POST_BOTH, _forged_call("popup"))
    popup.evaluate(_POST_BOTH, FORGED_READY)
    # (c) the View posting straight to top (origin "null"), and a forged proxy-ready through
    # its own proxy (relayed, and ignored by the bridge)
    view.frame.evaluate("(m) => window.probe.postToTop(m)", _forged_call("top"))
    view.frame.evaluate("(m) => window.probe.postToTop(JSON.stringify(m))", _forged_call("top2"))
    view.frame.evaluate("(m) => window.parent.postMessage(m, '*')", FORGED_READY)
    page.wait_for_timeout(SETTLE_MS)
    expect(cards).to_have_count(0)
    assert len(apps.app_records()) == before
    entries = tab.transcript(view.view_id)
    assert len(_down(entries, "ui/notifications/sandbox-resource-ready")) == 1, entries
    assert [e for e in entries if str(e["id"]).startswith("forged-")] == []
    popup.close()
    # positive control: the legitimate channel is alive
    view.frame.locator("#call-app-only").click()
    _allow(tab, "app:apps", f"{SERVER}::app_only_echo")
    records = _wait_app_records(apps, before + 1, page)[before:]
    page.wait_for_timeout(SETTLE_MS)
    assert len(apps.app_records()) == before + 1
    assert [(r["fqn"], r["outcome"]) for r in records] == [(f"{SERVER}::app_only_echo", "success")]
    replies = view.frame.evaluate("() => Object.values(window.probe.replies)")
    assert [("result" in r, "error" in r) for r in replies[-1:]] == [(True, False)]


def test_localhost_ui_gets_the_notice_and_no_app(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-002, REQ-NFR-011 (S-3 #7, DD-017): opening the UI as
    ``http://localhost:<port>`` gets ``403`` with the "open http://127.0.0.1:<port>" notice,
    no backend websocket and no App frame."""
    run = apps.run
    page = browser_context.ctx.new_page()
    sockets: list[WebSocket] = []
    page.on("websocket", lambda ws: sockets.append(ws))
    response = page.goto(f"http://localhost:{run.port}/catalog")
    assert response is not None
    assert response.status == 403
    expect(page.locator("body")).to_contain_text(f"Open http://127.0.0.1:{run.port}/")
    page.wait_for_timeout(SETTLE_MS)
    assert sockets == []
    assert page.locator(PROXY).count() == 0
    assert page.locator("iframe").count() == 0


def test_resource_limit_and_csp_notices(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-013, REQ-APP-014, REQ-APP-011: ``ui://too-big`` (5 MiB + 1) is not rendered
    and its notice names ``too_large``; ``ui://bad-csp`` renders with the external-domains
    notice and the dropped-entries notice listing every invalid entry, none of which is in
    the View's CSP."""
    tab = browser_context.tab()
    page = tab.page
    _render(tab, "too_big_view")
    notice = page.locator(NOTICE)
    expect(notice).to_contain_text("The App was not rendered: too_large")
    expect(page.locator(PROXY)).to_have_count(0)
    _render(tab, "bad_csp_view")
    view = _view(tab)
    texts = page.locator(NOTICE).all_inner_texts()
    external = [t for t in texts if "external domains" in t]
    dropped = [t for t in texts if "Left out of the App's content security policy" in t]
    assert len(external) == 1, texts
    assert len(dropped) == 1, texts
    assert "https://cdn.example.com" in external[0]
    assert "wss://rt.example.com" in external[0]
    invalid = (
        "resourceDomains: *",
        "resourceDomains: https:",
        "'unsafe-eval'",
        "https://127.0.0.1",
        "https://x.example;script-src *",
        "http://api.example.com",
        "https://localhost",
    )
    for entry in invalid:
        assert entry in dropped[0], (entry, dropped[0])
    csp = view.frame.evaluate("() => document.head.firstElementChild.getAttribute('content')")
    assert "https://cdn.example.com" in csp
    assert "wss://rt.example.com" in csp
    for entry in ("127.0.0.1", "localhost", "unsafe-eval", "http://api.example.com", "x.example"):
        assert entry not in csp, (entry, csp)


def test_app_hidden_tool_is_refused_and_audited(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-008, REQ-APP-004: the probe's ``tools/call model_only_echo`` (visibility
    ``["model"]``) gets ``-32602``, raises no consent card and is audited once as
    ``denied`` / ``app_scope`` (``not_evaluated``) for ``app:apps``."""
    tab = browser_context.tab()
    view = _probe(tab)
    before = len(apps.app_records())
    reply = view.frame.evaluate("() => window.probe.callTool('model_only_echo', {text: 'x'})")
    assert reply["error"]["code"] == -32602, reply
    records = _wait_app_records(apps, before + 1, tab.page)[before:]
    tab.page.wait_for_timeout(500)
    expect(tab.page.locator('[data-testid="consent-card"]')).to_have_count(0)
    assert [(r["fqn"], r["outcome"], r["error_code"], r["decision_source"]) for r in records] == [
        (f"{SERVER}::model_only_echo", "denied", "app_scope", "not_evaluated")
    ]


def test_cancel_and_teardown_reach_the_view(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-009, REQ-APP-010, REQ-APP-001, REQ-APP-006: cancelling a running UI
    invocation of ``slow_view`` renders its View, which gets ``tool-input`` and then
    ``tool-cancelled {reason: "cancelled"}`` (no ``tool-result``); invoking again tears
    it down with ``ui/resource-teardown {reason: "replaced"}`` (answered by the View); the
    new View's Close button tears it down with ``{reason: "closed"}`` and removes it."""
    tab = browser_context.tab()
    page = tab.page
    _render(tab, "slow_view", seconds="30")
    page.locator('[data-testid="invoke-cancel"]').click()
    expect(page.locator('[data-testid="invoke-outcome"]')).to_contain_text("cancelled")
    view = _view(tab)
    view.frame.wait_for_function("() => window.probe !== undefined && window.probe.initialized")
    events = view.frame.wait_for_function(
        "() => window.probe.events.some((e) => e.method === 'ui/notifications/tool-cancelled')"
        " && window.probe.events"
    ).json_value()
    seeded = [e for e in events if e["method"].startswith("ui/notifications/tool-")]
    assert [(e["method"], (e["params"] or {}).get("reason")) for e in seeded] == [
        ("ui/notifications/tool-input", None),
        ("ui/notifications/tool-cancelled", "cancelled"),
    ]
    first = view.view_id
    _invoke(tab, seconds="0.1")

    def torn(entries: list[dict[str, Any]]) -> bool:
        teardown = _down(entries, "ui/resource-teardown")
        return bool(teardown) and _up_reply(entries, teardown[0]["id"])

    entries = tab.wait_transcript(first, torn)
    assert [e.get("reason") for e in _down(entries, "ui/resource-teardown")] == ["replaced"]
    _allow(tab, "human:ui", f"{SERVER}::slow_view")
    expect(page.locator('[data-testid="invoke-outcome"]')).to_contain_text("success")
    second = _view(tab)
    assert second.view_id != first
    tab.wait_transcript(second.view_id, lambda e: bool(_down(e, "ui/notifications/tool-result")))
    page.locator('[data-testid="mcp-app-close"]').click()
    entries = tab.wait_transcript(second.view_id, lambda e: bool(_down(e, "ui/resource-teardown")))
    assert [e.get("reason") for e in _down(entries, "ui/resource-teardown")] == ["closed"]
    expect(page.locator(PROXY)).to_have_count(0)


def _up_reply(entries: list[dict[str, Any]], request_id: Any) -> bool:
    return any(
        e["dir"] == "up" and e["method"] is None and e["id"] == request_id and e.get("result")
        for e in entries
    )


def test_open_link_confirmation_and_noopener_tab(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-015: ``ui/open-link javascript:…`` is refused with an error and shows no
    confirmation; ``https://example.com`` shows the confirmation with the full URL;
    Decline → error ``-32000``; Approve → a new tab on that URL whose ``window.opener`` is
    ``null`` and an ``ok`` reply to the View."""
    tab = browser_context.tab()
    page = tab.page
    view = _probe(tab)
    link = page.locator('[data-testid="mcp-app-link"]')
    refused = view.frame.evaluate("() => window.probe.openLink('javascript:alert(1)')")
    assert "error" in refused, refused
    expect(link).to_have_count(0)
    _open_link(view, "https://example.com")
    expect(page.locator('[data-testid="mcp-app-link-url"]')).to_have_text("https://example.com")
    page.locator('[data-testid="mcp-app-link-decline"]').click()
    reply = _link_reply(view)
    assert reply["error"]["code"] == -32000, reply
    expect(link).to_have_count(0)
    _open_link(view, "https://example.com")
    expect(page.locator('[data-testid="mcp-app-link-url"]')).to_have_text("https://example.com")
    with browser_context.ctx.expect_page() as opened:
        page.locator('[data-testid="mcp-app-link-approve"]').click()
    new_tab = opened.value
    new_tab.wait_for_load_state()
    assert new_tab.url.startswith("https://example.com")
    assert new_tab.evaluate("() => window.opener") is None
    reply = _link_reply(view)
    assert "result" in reply, reply
    assert "error" not in reply, reply
    expect(link).to_have_count(0)


def _open_link(view: View, url: str) -> None:
    """Start the probe's ``ui/open-link`` without waiting for its (human) answer."""
    view.frame.evaluate(
        "(url) => { window.__link = null;"
        " window.probe.openLink(url).then((r) => { window.__link = r; }); }",
        url,
    )


def _link_reply(view: View) -> dict[str, Any]:
    reply = view.frame.wait_for_function("() => window.__link").json_value()
    assert isinstance(reply, dict)
    return reply


# ------------------------------------------------------------------ self-navigation (F8-13)

NAVIGATED_NOTICE = "The App navigated away and was closed."
FORGED_NAVIGATED: dict[str, Any] = {"type": "view-navigated"}


def _wait_started(apps: Apps, fqn: str, page: Page, timeout: float = 15.0) -> dict[str, Any]:
    """The App audit record of ``fqn`` once it exists (begun, not yet finished)."""
    deadline = time.monotonic() + timeout
    while True:
        found = [r for r in apps.app_records() if r["fqn"] == fqn]
        if found:
            return found[-1]
        if time.monotonic() > deadline:
            raise AssertionError(f"no App record for {fqn}: {apps.app_records()}")
        page.wait_for_timeout(100)


def test_a_view_that_navigates_its_frame_is_discarded(apps: Apps, browser_context: Context) -> None:
    """REQ-APP-010, REQ-APP-012, REQ-APP-002 (API §7 Lifecycle, TD §6.1, delta D7): the probe
    View navigates its own frame; the proxy reports it, the View is removed without
    ``ui/resource-teardown``, the notice "The App navigated away and was closed." is shown,
    the View's pending App call ends ``cancelled`` (one audit record) and nothing more from
    that frame reaches the bridge (no further message up, no consent card)."""
    tab = browser_context.tab()
    page = tab.page
    view = _probe(tab)
    before = len(apps.app_records())
    view.frame.evaluate("() => { window.probe.callTool('slow', {seconds: 30}); }")
    _allow(tab, "app:apps", f"{SERVER}::slow")
    _wait_started(apps, f"{SERVER}::slow", page)
    view.frame.locator("#navigate").click()
    expect(page.locator(PROXY)).to_have_count(0)
    expect(page.locator(NOTICE)).to_contain_text(NAVIGATED_NOTICE)
    records = _wait_app_records(apps, before + 1, page)[before:]
    assert [(r["fqn"], r["outcome"]) for r in records] == [(f"{SERVER}::slow", "cancelled")]
    page.wait_for_timeout(SETTLE_MS)
    assert len(apps.app_records()) == before + 1
    expect(page.locator('[data-testid="consent-card"]')).to_have_count(0)
    entries = tab.transcript(view.view_id)
    assert _down(entries, "ui/resource-teardown") == [], entries
    assert _down(entries, "ui/notifications/tool-cancelled") == [], entries


def test_a_forged_view_navigated_from_a_sibling_is_ignored(
    apps: Apps, browser_context: Context
) -> None:
    """REQ-APP-002, REQ-APP-010 (delta D7): a ``{"type": "view-navigated"}`` posted to the
    Host UI by a sibling iframe on the proxy origin (object and JSON text) fails the
    relay's source check and is ignored: the View stays, no notice, its channel still
    answers (``resources/read`` of its own Server)."""
    tab = browser_context.tab()
    page = tab.page
    view = _probe(tab)
    page.evaluate(
        "(url) => { const f = document.createElement('iframe'); f.id = 'nav-forger';"
        " f.src = url; document.body.appendChild(f); }",
        f"{view.proxy_origin}/proxy?view=forger",
    )
    sibling = page.locator("#nav-forger").element_handle().content_frame()
    assert sibling is not None
    sibling.wait_for_load_state()
    sibling.evaluate(_POST_BOTH, FORGED_NAVIGATED)
    page.wait_for_timeout(SETTLE_MS)
    expect(page.locator(PROXY)).to_have_count(1)
    assert NAVIGATED_NOTICE not in " ".join(page.locator(NOTICE).all_inner_texts())
    reply = view.frame.evaluate("() => window.probe.readResource('ui://probe')")
    assert "result" in reply, reply
