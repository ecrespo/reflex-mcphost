"""REQ-NFR-003 / REQ-NFR-004 measured in Chromium against the wheel (F6-09b; TD §8, PRD §7).

The budgets are the PRD's and are never relaxed here: a miss fails the test and opens a
Delta Spec (bounded rendering, page size or the PRD target). The measured numbers are
printed in the terminal summary ("e2e measurements") for the execution log.

REQ-NFR-004 timing is taken in the page: a capture-phase ``keydown`` listener stamps
``performance.now()`` when the filter keystroke happens, a ``MutationObserver`` stamps the
first DOM mutation after which the Catalog list shows the expected rows (first entry and the
"N more" line); the interval covers the event round trip to the backend, the filtering, the
state delta and React's DOM update.
"""

from __future__ import annotations

import math
import time

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.conftest import _free_port
from tests.e2e.conftest import E2E, MEASUREMENTS, START_TIMEOUT, AppRun, app_run, modern_argv

pytestmark = pytest.mark.e2e
WARM_START_BUDGET_S = 10.0  # REQ-NFR-003
WARM_RUNS = 5
LIST_BUDGET_MS = 200.0  # REQ-NFR-004 (p95)
SERVERS = 50
TOOLS_PER_SERVER = 40  # 50 x 40 = 2,000 tool entries (plus each Server's resources/prompts)
INTERACTIONS = 20
CATALOG_LOAD_TIMEOUT_MS = 240_000

_INSTALL = """([first, more]) => {
  const root = document.querySelector('[data-testid="catalog-browser"]');
  const probe = { t0: null, t1: null };
  window.__nfr = probe;
  const ok = () => {
    const entry = root.querySelector('[data-testid="catalog-entry"] p');
    return entry !== null && entry.textContent === first
      && root.textContent.includes(more + ' more');
  };
  const onKey = () => { if (probe.t0 === null) probe.t0 = performance.now(); };
  document.addEventListener('keydown', onKey, { capture: true, once: true });
  const observer = new MutationObserver(() => {
    const now = performance.now();
    if (probe.t0 !== null && probe.t1 === null && ok()) {
      probe.t1 = now;
      observer.disconnect();
    }
  });
  observer.observe(root, { childList: true, subtree: true, characterData: true });
}"""


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_warm_start_renders_the_servers_view_within_10_s(warm: E2E, browser: Browser) -> None:
    """REQ-NFR-003: on a warm cache (second and later starts of the same version), from
    ``reflex-mcphost run --no-browser`` process start to ``[data-testid="servers-panel"]``
    visible takes < 10 s in every one of 5 runs."""
    config = warm.write_config("nfr-003", {"modern": modern_argv()})
    times: list[float] = []
    for _ in range(WARM_RUNS):
        run = AppRun(warm, config, _free_port()).start()
        try:
            run.wait_ready(START_TIMEOUT)
            page = browser.new_page()
            page.goto(f"{run.origin}/")
            page.locator('[data-testid="servers-panel"]').wait_for(state="visible", timeout=60_000)
            times.append(time.monotonic() - run.started_at)
            page.close()
        finally:
            run.stop()
    MEASUREMENTS.append(
        "REQ-NFR-003 warm start → servers-panel (s): "
        + ", ".join(f"{t:.2f}" for t in times)
        + f" (max {max(times):.2f}, budget < {WARM_START_BUDGET_S:.0f})"
    )
    assert all(t < WARM_START_BUDGET_S for t in times), times


def _measure(page: Page, key: str, first: str, more: int) -> float:
    page.evaluate(_INSTALL, [first, str(more)])
    page.keyboard.press(key)
    page.wait_for_function("() => window.__nfr.t1 !== null", timeout=10_000)
    elapsed = page.evaluate("() => window.__nfr.t1 - window.__nfr.t0")
    assert isinstance(elapsed, int | float)
    return float(elapsed)


def test_catalog_filter_updates_within_200_ms_at_p95(warm: E2E, browser: Browser) -> None:
    """REQ-NFR-004: with 50 configured Servers and 2,000 catalog entries, the p95 over 20
    filter keystrokes of keystroke → Catalog list DOM update is < 200 ms (Chromium)."""
    servers = {f"s{i:02d}": modern_argv("--tools", str(TOOLS_PER_SERVER)) for i in range(SERVERS)}
    config = warm.write_config("nfr-004", servers)
    total = SERVERS * TOOLS_PER_SERVER
    with app_run(warm, config) as run:
        page = browser.new_page()
        page.goto(f"{run.origin}/catalog")
        browser_root = page.locator('[data-testid="catalog-browser"]')
        expect(browser_root).to_contain_text(f"{total - 200} more", timeout=CATALOG_LOAD_TIMEOUT_MS)
        box = page.get_by_placeholder("Filter")
        box.click()
        box.fill("tool_00")  # every generated tool: the first 200 rows, "1800 more"
        expect(browser_root).to_contain_text(f"{total - 200} more")
        per_digit = SERVERS * 10  # tool_00d0 … tool_00d9 on every Server
        samples: list[float] = []
        for i in range(INTERACTIONS // 2):
            digit = str(1 + i % 3)
            samples.append(_measure(page, digit, f"tool_00{digit}0", per_digit - 200))
            samples.append(_measure(page, "Backspace", "tool_0000", total - 200))
        page.close()
    result = p95(samples)
    MEASUREMENTS.append(
        f"REQ-NFR-004 filter keystroke → Catalog list DOM update ({SERVERS} Servers, "
        f"{total} tools, {len(samples)} interactions, ms): p95 {result:.1f}, "
        f"median {sorted(samples)[len(samples) // 2]:.1f}, max {max(samples):.1f} "
        f"(budget p95 < {LIST_BUDGET_MS:.0f}); samples " + ", ".join(f"{s:.0f}" for s in samples)
    )
    assert len(samples) == INTERACTIONS
    assert result < LIST_BUDGET_MS, samples
