"""``permissions_table()``: stored consent rules (API §8, REQ-UI-006; F6-07).

Rows come from ``consent.rules()`` (persistent and session) through
``HostState.rules_shown`` (filtered, at most 200 plus a "N more" line): effect,
operation, target, Consumer (``any`` when unset), lifetime, origin, ``suspended``
(definition changed, REQ-CNS-007), created / updated. Edit (effect / lifetime →
``consent.update``) and Revoke (``consent.revoke``) apply to the next evaluation without a
restart (REQ-CNS-013). Rules are loaded when the view mounts and re-queried by the tab's
watcher on every ``consent.rules_changed`` (a prompt answered in another tab, an agent's
programmatic grant, a suspension; API §4.11, delta D11), so the view is live without a
reload.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.ui.components.filters import FILTER_DEBOUNCE_MS
from reflex_mcphost.ui.state import HostState

__all__ = ["permissions_table"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)
_HEADERS = (
    "Effect",
    "Operation",
    "Target",
    "Consumer",
    "Lifetime",
    "Origin",
    "Status",
    "Created",
    "Updated",
    "Actions",
)


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _row(row: Any) -> rx.Component:
    rule_id = row["id"].to(str)
    effect = row["effect"].to(str)
    lifetime = row["lifetime"].to(str)
    return _component(
        rx.table.row(
            rx.table.cell(
                rx.select(
                    ["allow", "deny"],
                    value=effect,
                    size="1",
                    on_change=lambda value: _S.update_rule(rule_id, value, lifetime),
                    data_testid="rule-effect",
                )
            ),
            rx.table.cell(rx.text(row["operation"].to(str), size="2")),
            rx.table.cell(rx.text(row["target"].to(str), size="2", word_break="break-all")),
            rx.table.cell(
                rx.text(rx.cond(row["consumer"], row["consumer"].to(str), "any"), size="2")
            ),
            rx.table.cell(
                rx.select(
                    ["session", "always"],
                    value=lifetime,
                    size="1",
                    on_change=lambda value: _S.update_rule(rule_id, effect, value),
                    data_testid="rule-lifetime",
                )
            ),
            rx.table.cell(rx.text(row["origin"].to(str), size="2")),
            rx.table.cell(
                rx.cond(
                    row["suspended"],
                    rx.badge("suspended: definition changed", color_scheme="amber"),
                    rx.badge("active", color_scheme="grass"),
                )
            ),
            rx.table.cell(rx.text(row["created_at"].to(str), size="1")),
            rx.table.cell(rx.text(row["updated_at"].to(str), size="1")),
            rx.table.cell(
                rx.button(
                    "Revoke",
                    color_scheme="red",
                    size="1",
                    on_click=_S.revoke_rule(rule_id),
                    data_testid="rule-revoke",
                )
            ),
            data_testid="rule-row",
        )
    )


def permissions_table() -> rx.Component:
    """The Permissions view (container ``data-testid="permissions-table"``)."""
    return _component(
        rx.vstack(
            rx.hstack(
                rx.heading("Permissions", size="5"),
                rx.input(
                    placeholder="Filter rules",
                    value=_S.rule_filter,
                    on_change=_S.set_rule_filter,
                    debounce_timeout=FILTER_DEBOUNCE_MS,
                    size="2",
                ),
                rx.button("Refresh", on_click=_S.load_rules, size="2", variant="soft"),
                align="center",
                wrap="wrap",
            ),
            rx.cond(_S.permissions_notice != "", rx.callout(_S.permissions_notice, size="1")),
            rx.table.root(
                rx.table.header(rx.table.row(*(rx.table.column_header_cell(h) for h in _HEADERS))),
                rx.table.body(rx.foreach(_S.rules_shown, _row)),
                width="100%",
            ),
            rx.cond(
                _S.rules_more > 0,
                rx.text(_S.rules_more.to_string(), " more — refine the filter", size="1"),
            ),
            on_mount=_S.load_rules,
            width="100%",
            data_testid="permissions-table",
        )
    )
