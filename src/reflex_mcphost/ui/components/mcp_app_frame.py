"""``mcp_app_frame()``: the tab's MCP App View (API §8, §7; F7-04, DD-012).

``McpAppRelay`` wraps the static relay (``ui/assets/mcp_app_relay.js``): it renders the
sandbox proxy iframe (``sandbox="allow-scripts allow-same-origin"``, ``allow=""``) on the
proxy origin, forwards the proxy window's messages (source **and** origin checked, ≤ 1 MiB)
as ``on_message(view_id, json_text)`` and posts the ``outbox`` entries to the proxy window,
acknowledging them with ``on_ack(view_id, seq)`` (delta D4: a data channel; no script and no
DOM are ever built from Server content).

``mcp_app_frame()`` shows, from ``HostState.app_view``: the notices (MCP Apps disabled, not
rendered, external domains, dropped CSP entries) as plain text, the ``ui/open-link``
confirmation with the full URL as plain text (Approve / Decline), the relay and a Close
button. ``catalog_browser`` renders it in the result panel.

Test hooks (``data-testid``): ``mcp-app-frame``, ``mcp-app-notice``, ``mcp-app-proxy`` (the
proxy iframe), ``mcp-app-link``, ``mcp-app-link-url``, ``mcp-app-link-approve``,
``mcp-app-link-decline``, ``mcp-app-close``.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.ui.assets import relay_library
from reflex_mcphost.ui.state import HostState

__all__ = ["McpAppRelay", "mcp_app_frame"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)


class McpAppRelay(rx.Component):
    """The relay component (``McpAppRelay`` of ``mcp_app_relay.js``)."""

    tag = "McpAppRelay"

    view_id: rx.Var[str]
    proxy_src: rx.Var[str]
    proxy_origin: rx.Var[str]
    outbox: rx.Var[list[dict[str, Any]]]
    height: rx.Var[int]
    border: rx.Var[bool]

    # Reflex's documented event-spec form (a call inside the subscript) is not a static type.
    on_message: rx.EventHandler[rx.event.passthrough_event_spec(str, str)]  # type: ignore[type-arg,valid-type]
    on_ack: rx.EventHandler[rx.event.passthrough_event_spec(str, int)]  # type: ignore[type-arg,valid-type]

    @classmethod
    def create(cls, *children: Any, **props: Any) -> McpAppRelay:
        """The relay, with its ``library`` resolved now (page compile), not at import."""
        props.setdefault("library", relay_library())
        component = super().create(*children, **props)
        if not isinstance(component, McpAppRelay):  # pragma: no cover - Reflex contract
            raise TypeError("McpAppRelay.create did not return the relay")
        return component


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _plain(*children: Any, **props: Any) -> rx.Component:
    props.setdefault("word_break", "break-word")
    return _component(rx.text(*children, white_space="pre-wrap", **props))


def _notice(text: Any) -> rx.Component:
    return _component(
        rx.callout.root(
            rx.callout.text(text.to(str), white_space="pre-wrap", word_break="break-all"),
            size="1",
            color_scheme="amber",
            data_testid="mcp-app-notice",
        )
    )


def _link_confirmation() -> rx.Component:
    view = _S.app_view
    return _component(
        rx.vstack(
            _plain(view["link_text"].to(str), size="2", weight="bold"),
            _plain(
                view["link"].to(str),
                size="2",
                font_family="monospace",
                word_break="break-all",
                data_testid="mcp-app-link-url",
            ),
            rx.hstack(
                rx.button(
                    "Open in a new tab",
                    on_click=_S.answer_app_link(True),
                    data_testid="mcp-app-link-approve",
                ),
                rx.button(
                    "Decline",
                    variant="soft",
                    color_scheme="gray",
                    on_click=_S.answer_app_link(False),
                    data_testid="mcp-app-link-decline",
                ),
            ),
            padding="2",
            border="1px solid var(--amber-a7)",
            width="100%",
            data_testid="mcp-app-link",
        )
    )


def _relay() -> rx.Component:
    view = _S.app_view
    return McpAppRelay.create(
        key=view["view_id"].to(str),
        view_id=view["view_id"].to(str),
        proxy_src=view["src"].to(str),
        proxy_origin=view["origin"].to(str),
        outbox=_S.app_outbox,
        height=view["height"].to(int),
        border=view["border"].to(bool),
        on_message=_S.on_app_message,
        on_ack=_S.on_app_ack,
    )


def mcp_app_frame() -> rx.Component:
    """The tab's MCP App View or its notice (container ``data-testid="mcp-app-frame"``)."""
    view = _S.app_view
    return _component(
        rx.cond(
            view["status"],
            rx.vstack(
                rx.foreach(view["notices"].to(list[str]), _notice),
                rx.cond(view["link"].to(str) != "", _link_confirmation()),
                rx.cond(
                    view["status"].to(str) == "rendered",
                    rx.vstack(
                        _relay(),
                        rx.button(
                            "Close the App",
                            size="1",
                            variant="soft",
                            on_click=_S.close_app,
                            data_testid="mcp-app-close",
                        ),
                        width="100%",
                    ),
                ),
                width="100%",
                data_testid="mcp-app-frame",
            ),
        )
    )
