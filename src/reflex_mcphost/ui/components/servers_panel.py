"""``servers_panel()``: the Servers view (API §8, REQ-UI-003; F6-04, delta D7).

Rows come from ``HostState.servers_shown`` (``ui.models.server_rows``, at most 200 plus a
"N more" line): state badge and reason (``auth_required``, ``config_error``,
``untrusted_project_server``, …), era and protocol version, origin, transport, declared
capabilities, enabled, env / header **keys** (never values, REQ-REG-007), shadowed
definitions and config problems. Actions call one core method each through
``HostState``: connect, disconnect, reconnect, authorize (the authorization URL is opened
with an external redirect and never stored, REQ-AUTH-008; a URL that is not an openable
http(s) address is refused with a fixed notice and the flow is cancelled, delta D8), sign
out, enable / disable (REQ-REG-010), reload (REQ-REG-013) and the deprecated log level
(REQ-HSH-031). The log view shows the selected Server's last 200 lines as plain text
(REQ-HSH-030).

Server-provided text (names, reasons, log lines) is rendered through text components only.
"""

from __future__ import annotations

from typing import Any, get_args

import reflex as rx

from reflex_mcphost.core.config.models import LogLevel
from reflex_mcphost.ui.components.filters import FILTER_DEBOUNCE_MS
from reflex_mcphost.ui.state import HostState

__all__ = ["servers_panel"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)
LOG_LEVELS: list[str] = list(get_args(LogLevel))


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _state_badge(row: Any) -> rx.Component:
    color = rx.match(
        row["state"],
        ("ready", "grass"),
        ("failed", "red"),
        ("auth_required", "amber"),
        ("disabled", "gray"),
        ("disconnected", "gray"),
        "blue",
    )
    return _component(
        rx.vstack(
            rx.badge(row["state"].to(str), color_scheme=color, data_testid="server-state"),
            rx.cond(row["reason"], rx.text(row["reason"].to(str), size="1")),
            spacing="1",
        )
    )


def _action(label: str, row: Any, action: str, **props: Any) -> rx.Component:
    return _component(
        rx.button(
            label,
            size="1",
            variant="soft",
            on_click=_S.server_action(row["id"], action),
            data_testid=f"server-{action}",
            **props,
        )
    )


def _actions(row: Any) -> rx.Component:
    server_id = row["id"].to(str)
    return _component(
        rx.hstack(
            _action("Connect", row, "connect"),
            _action("Disconnect", row, "disconnect"),
            _action("Reconnect", row, "reconnect"),
            rx.cond(
                row["enabled"] == False,  # noqa: E712 - a Reflex Var comparison
                _action("Enable", row, "enable"),
                _action("Disable", row, "disable", color_scheme="gray"),
            ),
            rx.cond(
                row["transport"] == "http",
                rx.hstack(
                    rx.button(
                        "Authorize",
                        size="1",
                        on_click=_S.authorize(server_id),
                        data_testid="server-authorize",
                    ),
                    _action("Sign out", row, "sign_out", color_scheme="red"),
                ),
            ),
            rx.button(
                "Logs",
                size="1",
                variant="outline",
                on_click=_S.select_server(server_id),
                data_testid="server-logs",
            ),
            rx.select(
                LOG_LEVELS,
                placeholder="Log level (deprecated)",
                size="1",
                on_change=lambda level: _S.set_log_level(server_id, level),
            ),
            wrap="wrap",
            spacing="1",
        )
    )


def _origin(item: Any) -> rx.Component:
    return _component(
        rx.text(item["origin_kind"].to(str), " ", item["origin_path"].to(str), size="1")
    )


def _row(row: Any) -> rx.Component:
    return _component(
        rx.table.row(
            rx.table.cell(
                rx.vstack(
                    rx.text(row["id"].to(str), weight="bold"),
                    rx.cond(row["server_name"], rx.text(row["server_name"].to(str), size="1")),
                    spacing="1",
                )
            ),
            rx.table.cell(_state_badge(row)),
            rx.table.cell(
                rx.text(row["era"].to(str), " ", row["protocol_version"].to(str), size="2")
            ),
            rx.table.cell(
                rx.text(row["origin_kind"].to(str), size="2"),
                rx.text(row["origin_path"].to(str), size="1", word_break="break-all"),
            ),
            rx.table.cell(
                rx.text(row["transport"].to(str), size="2"),
                rx.text(row["command"].to(str), row["destination"].to(str), size="1"),
            ),
            rx.table.cell(rx.text(row["capabilities"].to(list[str]).join(", "), size="1")),
            rx.table.cell(
                rx.cond(
                    row["enabled"] == False,  # noqa: E712 - a Reflex Var comparison
                    rx.badge("disabled", color_scheme="gray"),
                    rx.badge("enabled", color_scheme="grass"),
                )
            ),
            rx.table.cell(
                rx.text(row["env_keys"].to(list[str]).join(", "), size="1"),
                rx.text(row["header_keys"].to(list[str]).join(", "), size="1"),
            ),
            rx.table.cell(
                rx.foreach(row["shadowed"].to(list[dict[str, Any]]), _origin),
                rx.foreach(
                    row["problems"].to(list[str]),
                    lambda problem: rx.text(problem, size="1", color="var(--red-11)"),
                ),
            ),
            rx.table.cell(_actions(row)),
            data_testid="server-row",
        )
    )


def _log_line(line: Any) -> rx.Component:
    return _component(
        rx.text(
            line["timestamp"].to(str),
            " ",
            line["level"].to(str),
            " ",
            line["logger"].to(str),
            ": ",
            line["text"].to(str),
            font_family="monospace",
            size="1",
            white_space="pre-wrap",
            word_break="break-all",
        )
    )


def _log_view() -> rx.Component:
    return _component(
        rx.vstack(
            rx.heading("Log: ", _S.server_selected, size="3"),
            rx.scroll_area(
                rx.vstack(rx.foreach(_S.server_logs, _log_line), spacing="0"),
                max_height="320px",
                type="auto",
            ),
            width="100%",
            data_testid="server-log",
        )
    )


_HEADERS = (
    "Server",
    "State",
    "Era / protocol",
    "Origin",
    "Transport",
    "Capabilities",
    "Enabled",
    "Env / header keys",
    "Shadowed / problems",
    "Actions",
)


def servers_panel() -> rx.Component:
    """The Servers view (container ``data-testid="servers-panel"``)."""
    return _component(
        rx.vstack(
            rx.hstack(
                rx.heading("Servers", size="5"),
                rx.input(
                    placeholder="Filter servers",
                    value=_S.server_filter,
                    on_change=_S.set_server_filter,
                    debounce_timeout=FILTER_DEBOUNCE_MS,
                    size="2",
                ),
                rx.button("Reload config", on_click=_S.reload_servers, size="2"),
                align="center",
                wrap="wrap",
            ),
            rx.cond(
                _S.server_notice != "",
                rx.callout(_S.server_notice, size="1", data_testid="server-notice"),
            ),
            rx.table.root(
                rx.table.header(rx.table.row(*(rx.table.column_header_cell(h) for h in _HEADERS))),
                rx.table.body(rx.foreach(_S.servers_shown, _row)),
                width="100%",
            ),
            rx.cond(
                _S.servers_more > 0,
                rx.text(_S.servers_more.to_string(), " more — refine the filter", size="1"),
            ),
            rx.cond(_S.server_selected != "", _log_view()),
            width="100%",
            data_testid="servers-panel",
        )
    )
