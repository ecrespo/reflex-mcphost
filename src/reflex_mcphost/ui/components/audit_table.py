"""``audit_table()``: the Audit view (API §8, REQ-UI-006; F6-07).

``audit.query(AuditQuery)`` with the REQ-INV-011 filters (time range, Server, FQN,
Consumer, outcome) plus operation; newest first; cursor paging ("Load more"); the first
page is re-queried on ``invocation.finished`` once the view was opened (watcher hook).
Rows show the argument **hash** (REQ-INV-009), never arguments. Text filters apply when
the input loses focus (one query per edit, not per keystroke).
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.ui.models import ANY
from reflex_mcphost.ui.state import HostState

__all__ = ["audit_table"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)
_TEXT_FILTERS = (
    ("since", "Since (ISO 8601)"),
    ("until", "Until (ISO 8601)"),
    ("server_id", "Server"),
    ("fqn", "Tool (server::tool)"),
    ("consumer", "Consumer (e.g. human:ui)"),
)
_HEADERS = (
    "Time",
    "Operation",
    "Consumer",
    "Server",
    "Tool",
    "Outcome",
    "Error",
    "Decision",
    "Rule",
    "Duration (ms)",
    "Input rounds",
    "Attribution",
    "Argument hash",
)


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _cell(value: Any) -> rx.Component:
    return _component(rx.table.cell(rx.text(value, size="1", word_break="break-all")))


def _row(row: Any) -> rx.Component:
    return _component(
        rx.table.row(
            _cell(row["timestamp"].to(str)),
            _cell(row["operation"].to(str)),
            _cell(row["consumer"].to(str)),
            _cell(row["server_id"].to(str)),
            _cell(row["fqn"].to(str)),
            rx.table.cell(
                rx.badge(
                    row["outcome"].to(str),
                    color_scheme=rx.cond(row["outcome"] == "success", "grass", "red"),
                )
            ),
            _cell(row["error_code"].to(str)),
            _cell(row["decision_source"].to(str)),
            _cell(row["rule_id"].to(str)),
            _cell(row["duration_ms"].to(str)),
            _cell(row["input_rounds"].to(str)),
            _cell(row["attribution"].to(str)),
            _cell(row["args_hash"].to(str)),
            data_testid="audit-row",
        )
    )


def _text_filter(key: str, label: str) -> rx.Component:
    return _component(
        rx.input(
            placeholder=label,
            default_value=_S.audit_filters[key].to(str),
            on_blur=lambda value: _S.set_audit_filter(key, value),
            size="1",
        )
    )


def _choice_filter(key: str, options: list[str], label: str) -> rx.Component:
    return _component(
        rx.select(
            [ANY, *options],
            placeholder=label,
            size="1",
            on_change=lambda value: _S.set_audit_filter(key, value),
        )
    )


def audit_table() -> rx.Component:
    """The Audit view (container ``data-testid="audit-table"``)."""
    return _component(
        rx.vstack(
            rx.hstack(
                rx.heading("Audit", size="5"),
                rx.button("Refresh", on_click=_S.load_audit, size="2", variant="soft"),
                align="center",
            ),
            rx.hstack(
                *(_text_filter(key, label) for key, label in _TEXT_FILTERS),
                _choice_filter("outcome", [o.value for o in InvocationOutcome], "Outcome"),
                _choice_filter("operation", [o.value for o in OperationKind], "Operation"),
                wrap="wrap",
            ),
            rx.cond(_S.audit_notice != "", rx.callout(_S.audit_notice, size="1")),
            rx.table.root(
                rx.table.header(rx.table.row(*(rx.table.column_header_cell(h) for h in _HEADERS))),
                rx.table.body(rx.foreach(_S.audit, _row)),
                width="100%",
            ),
            rx.cond(
                _S.audit_cursor != "",
                rx.button("Load more", on_click=_S.load_more_audit, size="2", variant="soft"),
            ),
            on_mount=_S.load_audit,
            width="100%",
            data_testid="audit-table",
        )
    )
