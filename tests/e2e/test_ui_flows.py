"""Playwright flows in the reference app from the wheel (F6-09b; REQ-UI-004, REQ-UI-005).

Headless Chromium against one ``reflex-mcphost run --no-browser`` of the module (the modern
stdio fixture as the own Server ``modern``, warm workspace). The default consent decision
is *ask* and every flow answers "Allow once", so each invocation asks again.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from tests.e2e.conftest import E2E, AppRun, app_run, modern_argv

pytestmark = pytest.mark.e2e
UI_TIMEOUT_MS = 30_000
WATCHER_STOP_S = 2.0
"""S-5 #6: closing a tab ends its ``on_load`` watcher chain within 2 s."""
expect.set_options(timeout=UI_TIMEOUT_MS)


@pytest.fixture(scope="module")
def flows(warm: E2E) -> Iterator[AppRun]:
    config = warm.write_config("flows", {"modern": modern_argv()})
    with app_run(warm, config) as run:
        yield run


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    ctx = browser.new_context()
    ctx.set_default_timeout(UI_TIMEOUT_MS)
    yield ctx
    ctx.close()


def _catalog(context: BrowserContext, run: AppRun) -> Page:
    page = context.new_page()
    page.goto(f"{run.origin}/catalog")
    expect(page.locator('[data-testid="catalog-entry"]').first).to_be_visible()
    return page


def _open_tool(page: Page, name: str) -> None:
    page.get_by_placeholder("Filter").fill(name)
    entry = page.locator('[data-testid="catalog-entry"]').filter(
        has=page.get_by_text(name, exact=True)
    )
    entry.first.click()
    expect(page.locator('[data-testid="invoke-form"]')).to_be_visible()


def _allow_once(page: Page, fqn: str) -> None:
    card = page.locator('[data-testid="consent-card"]').filter(has_text=fqn)
    expect(card).to_be_visible()
    card.get_by_role("button", name="Allow once").click()
    expect(card).to_have_count(0)


def _audit_rows(page: Page) -> int:
    page.locator('[data-testid="nav-Audit"]').click()
    expect(page.locator('[data-testid="audit-table"]')).to_be_visible()
    page.get_by_role("button", name="Refresh").click()
    page.wait_for_timeout(500)
    return page.locator('[data-testid="audit-row"]').filter(has_text="modern::echo").count()


def test_consent_allow_once_shows_the_result_and_the_audit_row(
    flows: AppRun, context: BrowserContext
) -> None:
    """REQ-UI-004, REQ-UI-005: invoking ``echo`` from its schema-generated form raises a
    consent card in the side panel naming the asking Consumer; "Allow once" lets the call
    run, the result panel shows the echo and the Audit view gains the row."""
    page = _catalog(context, flows)
    before = _audit_rows(page)
    page.locator('[data-testid="nav-Catalog"]').click()
    _open_tool(page, "echo")
    page.locator('[data-testid="invoke-form"] input[name="text"]').fill("hello-e2e")
    page.locator('[data-testid="invoke-form"]').get_by_role("button", name="Invoke").click()
    card = page.locator('[data-testid="consent-card"]').filter(has_text="modern")
    expect(card).to_be_visible()
    expect(card).to_contain_text("human:ui")
    _allow_once(page, "modern")
    result = page.locator('[data-testid="invoke-result"]')
    expect(result).to_contain_text("hello-e2e")
    expect(page.locator('[data-testid="invoke-outcome"]')).to_be_visible()
    deadline = time.monotonic() + 10
    while (after := _audit_rows(page)) <= before and time.monotonic() < deadline:
        pass
    assert after == before + 1


def test_elicitation_form_shows_field_errors_then_accepts(
    flows: AppRun, context: BrowserContext
) -> None:
    """REQ-UI-005, REQ-UI-004: the ``elicit_form`` round renders a form card; an invalid
    value keeps the card with a field error (nothing sent), a valid answer reaches the
    Server and the result shows the accepted content."""
    page = _catalog(context, flows)
    _open_tool(page, "elicit_form")
    page.locator('[data-testid="invoke-form"]').get_by_role("button", name="Invoke").click()
    _allow_once(page, "modern")
    card = page.locator('[data-testid="elicitation-card"]')
    expect(card).to_be_visible()
    form = card.locator('[data-testid="elicitation-form"]')
    form.locator('input[name="name"]').fill("Ada")
    form.locator('input[name="age"]').fill("abc")
    form.get_by_role("button", name="Submit").click()
    expect(card).to_contain_text("must be an integer")
    form.locator('input[name="age"]').fill("36")
    form.get_by_role("button", name="Submit").click()
    expect(card).to_have_count(0)
    result = page.locator('[data-testid="invoke-result"]')
    expect(result).to_contain_text("accept")
    expect(result).to_contain_text("Ada")


def test_answer_in_one_tab_removes_the_card_from_the_other(
    flows: AppRun, context: BrowserContext
) -> None:
    """REQ-UI-005: a pending prompt is shown in every tab; answering it in tab A removes
    the card from tab B (first answer wins, one PromptRegistry per backend)."""
    page_a = _catalog(context, flows)
    page_b = _catalog(context, flows)
    _open_tool(page_a, "echo")
    page_a.locator('[data-testid="invoke-form"] input[name="text"]').fill("two-tabs")
    page_a.locator('[data-testid="invoke-form"]').get_by_role("button", name="Invoke").click()
    card_b = page_b.locator('[data-testid="consent-card"]').filter(has_text="modern")
    expect(card_b).to_be_visible()
    _allow_once(page_a, "modern")
    expect(card_b).to_have_count(0)
    expect(page_a.locator('[data-testid="invoke-result"]')).to_contain_text("two-tabs")


def test_closing_a_tab_stops_its_watcher(flows: AppRun, context: BrowserContext) -> None:
    """REQ-UI-005 (S-5 #6): closing tab B ends its ``on_load`` watcher chain: the backend
    logs ``watcher stopped`` for B's token within 2 s."""
    page_a = _catalog(context, flows)
    page_b = _catalog(context, flows)
    token = page_b.evaluate("() => window.sessionStorage.getItem('token')")
    assert isinstance(token, str)
    assert token, "no Reflex token in tab B"
    assert token != page_a.evaluate("() => window.sessionStorage.getItem('token')")
    flows.wait_for(re.compile(rf"watcher started \(…{re.escape(token[-6:])}\)"), 10)
    since = len(flows.lines)
    page_b.close()
    started = time.monotonic()
    flows.wait_for(
        re.compile(rf"watcher stopped \(…{re.escape(token[-6:])}\)"), WATCHER_STOP_S, since
    )
    assert time.monotonic() - started < WATCHER_STOP_S
