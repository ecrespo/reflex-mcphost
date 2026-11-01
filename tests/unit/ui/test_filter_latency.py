"""Filter inputs keep keystroke → list latency inside the REQ-NFR-004 budget."""

from __future__ import annotations

import reflex as rx
from reflex_components_core.core.debounce import DebounceInput

from reflex_mcphost.ui import catalog_browser, permissions_table, servers_panel
from reflex_mcphost.ui.components import FILTER_DEBOUNCE_MS


def _debounced(component: rx.Component) -> list[DebounceInput]:
    found: list[DebounceInput] = []

    def walk(c: rx.Component) -> None:
        if isinstance(c, DebounceInput):
            found.append(c)
        for child in c.children:
            walk(child)

    walk(component)
    return found


def test_filter_inputs_use_a_short_debounce() -> None:
    """REQ-NFR-004: the per-keystroke filter inputs of the Catalog, Servers and Permissions
    views debounce for FILTER_DEBOUNCE_MS (≤ 50 ms), not Reflex's 300 ms default, so a
    keystroke reaches the backend well inside the 200 ms p95 budget."""
    assert FILTER_DEBOUNCE_MS <= 50
    for view in (catalog_browser, permissions_table, servers_panel):
        inputs = _debounced(view())
        assert inputs, view.__name__
        assert all(str(i.debounce_timeout) == str(FILTER_DEBOUNCE_MS) for i in inputs), (
            view.__name__
        )
