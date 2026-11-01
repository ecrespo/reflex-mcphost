"""``catalog_browser()``: the Catalog view and invoke flow (API §8, REQ-UI-004; F6-05).

Tabs Tools / Resources / Templates / Prompts list ``HostState.catalog_shown`` (filtered in
the backend with ``ui.models.page`` on every keystroke, at most 200 rows plus a "N more"
line — REQ-NFR-004). A tool's details show its description and annotations as untrusted
plain-text hints (REQ-CAT-011), the input / output schema as JSON text and a
``schema_form``; Invoke runs ``host.invoker.start(..., consumer=Consumer.human())`` in a
background event with progress, Cancel and a result panel: text blocks, images as
``data:`` URLs of an allowed MIME type, audio as a player, resource links and embedded
resources as text, ``structuredContent`` as JSON text, the outcome badge and the error.
Resources are read, templates expanded from a variables form and prompts fetched with an
arguments form. Tools with a UI resource show the plain result (the View is Phase 7).

Every Server-provided string is rendered as a text child (React escapes it) — never
Markdown or HTML (Art. 5).
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.ui.components.filters import FILTER_DEBOUNCE_MS
from reflex_mcphost.ui.components.schema_form import schema_form
from reflex_mcphost.ui.state import HostState

__all__ = ["block_view", "blocks_view", "catalog_browser"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)
_TABS = (
    ("tools", "Tools"),
    ("resources", "Resources"),
    ("templates", "Templates"),
    ("prompts", "Prompts"),
)


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _plain(*children: Any, **props: Any) -> rx.Component:
    return _component(rx.text(*children, white_space="pre-wrap", word_break="break-word", **props))


def _mono(value: Any) -> rx.Component:
    return _component(
        rx.box(
            _plain(value, font_family="monospace", size="1"),
            width="100%",
            padding="2",
            background="var(--gray-a3)",
        )
    )


def block_view(block: Any) -> rx.Component:
    """One content block view row (``ui.models.content_row``)."""
    return _component(
        rx.vstack(
            rx.cond(block["role"] != "", rx.badge(block["role"].to(str), size="1")),
            rx.match(
                block["kind"],
                ("text", _plain(block["text"].to(str), size="2")),
                (
                    "image",
                    rx.image(
                        src=block["src"].to(str), alt=block["mime_type"].to(str), max_width="100%"
                    ),
                ),
                ("audio", rx.el.audio(src=block["src"].to(str), controls=True)),
                ("resource_link", _plain(block["text"].to(str), size="2")),
                _plain(block["text"].to(str), size="1", color="var(--gray-11)"),
            ),
            width="100%",
            spacing="1",
            data_testid="content-block",
        )
    )


def blocks_view(blocks: Any) -> rx.Component:
    """A list of content block view rows."""
    return _component(rx.vstack(rx.foreach(blocks, block_view), width="100%"))


def _entry(row: Any) -> rx.Component:
    return _component(
        rx.box(
            rx.hstack(
                rx.text(row["name"].to(str), weight="bold", size="2"),
                rx.badge(row["server_id"].to(str), size="1"),
                rx.cond(row["stale"], rx.badge("stale", color_scheme="amber", size="1")),
                wrap="wrap",
            ),
            rx.cond(row["title"], _plain(row["title"].to(str), size="1")),
            on_click=_S.select_entry(row["server_id"], row["id"]),
            cursor="pointer",
            padding="2",
            width="100%",
            data_testid="catalog-entry",
        )
    )


def _hint(item: Any) -> rx.Component:
    return _component(rx.badge(item["name"].to(str), "=", item["value"].to(str), variant="outline"))


def _invocation() -> rx.Component:
    inv = _S.invocation
    result = inv["result"].to(dict[str, Any])
    return _component(
        rx.cond(
            inv["id"],
            rx.vstack(
                rx.cond(
                    inv["running"],
                    rx.hstack(
                        rx.spinner(),
                        rx.text("Running…"),
                        rx.button(
                            "Cancel",
                            color_scheme="red",
                            size="1",
                            on_click=_S.cancel_invocation,
                            data_testid="invoke-cancel",
                        ),
                    ),
                ),
                rx.foreach(
                    inv["progress"].to(list[dict[str, Any]]),
                    lambda p: rx.text(p["text"].to(str), size="1", data_testid="invoke-progress"),
                ),
                rx.cond(
                    result["outcome"],
                    rx.vstack(
                        rx.badge(
                            result["outcome"].to(str),
                            color_scheme=rx.cond(result["ok"], "grass", "red"),
                            data_testid="invoke-outcome",
                        ),
                        rx.cond(
                            result["error_code"] != "",
                            _plain(
                                result["error_code"].to(str),
                                ": ",
                                result["error_message"].to(str),
                                size="2",
                            ),
                        ),
                        blocks_view(result["blocks"].to(list[dict[str, Any]])),
                        rx.cond(result["structured"] != "", _mono(result["structured"].to(str))),
                        width="100%",
                    ),
                ),
                width="100%",
                data_testid="invoke-result",
            ),
        )
    )


def _tool_detail() -> rx.Component:
    sel = _S.catalog_selected
    return _component(
        rx.vstack(
            rx.heading(sel["fqn"].to(str), size="3"),
            rx.text("Description (provided by the Server)", size="1", weight="bold"),
            _plain(sel["description"].to(str), size="2"),
            rx.text("Hints (untrusted)", size="1", weight="bold"),
            rx.hstack(rx.foreach(sel["annotations"].to(list[dict[str, Any]]), _hint), wrap="wrap"),
            rx.cond(
                sel["has_ui"],
                rx.callout("This tool has a UI view; the plain result is shown.", size="1"),
            ),
            rx.text("Input schema", size="1", weight="bold"),
            _mono(sel["input_schema"].to(str)),
            rx.cond(sel["output_schema"] != "", _mono(sel["output_schema"].to(str))),
            schema_form(
                sel["fields"].to(list[dict[str, Any]]),
                _S.invoke_tool,
                error_of=lambda name: _S.form_errors[name],
                json_mode=_S.json_mode | sel["json_fallback"].to(bool),
                actions=(
                    rx.button(
                        rx.cond(_S.json_mode, "Use the form", "Edit as JSON"),
                        type="button",
                        variant="soft",
                        on_click=_S.toggle_json_mode,
                    ),
                ),
                submit_label="Invoke",
                testid="invoke-form",
            ),
            _invocation(),
            width="100%",
        )
    )


def _read_form(inputs: Any, label: str) -> rx.Component:
    return _component(
        rx.form(
            rx.vstack(
                inputs,
                rx.button(label, type="submit"),
                width="100%",
            ),
            on_submit=_S.read_selected,
            reset_on_submit=False,
            width="100%",
        )
    )


def _read_result() -> rx.Component:
    res = _S.read_result
    return _component(
        rx.cond(
            res["kind"],
            rx.vstack(
                _plain(res["title"].to(str), size="1"),
                rx.cond(res["error"] != "", rx.callout(res["error"].to(str), color_scheme="red")),
                blocks_view(res["blocks"].to(list[dict[str, Any]])),
                width="100%",
                data_testid="read-result",
            ),
        )
    )


def _resource_detail() -> rx.Component:
    sel = _S.catalog_selected
    return _component(
        rx.vstack(
            rx.heading(sel["uri"].to(str), size="3"),
            _plain(sel["description"].to(str), size="2"),
            rx.text(sel["mime_type"].to(str), size="1"),
            _read_form(rx.fragment(), "Read"),
            width="100%",
        )
    )


def _template_detail() -> rx.Component:
    sel = _S.catalog_selected
    variables = rx.foreach(
        sel["variables"].to(list[str]),
        lambda name: rx.input(name=name, placeholder=name, width="100%"),
    )
    return _component(
        rx.vstack(
            rx.heading(sel["uri_template"].to(str), size="3"),
            _plain(sel["description"].to(str), size="2"),
            _read_form(variables, "Read"),
            width="100%",
        )
    )


def _argument(arg: Any) -> rx.Component:
    return _component(
        rx.vstack(
            rx.hstack(
                rx.text(arg["name"].to(str), weight="bold", size="2"),
                rx.cond(arg["required"], rx.text("*", color="var(--red-11)")),
            ),
            rx.cond(arg["description"], _plain(arg["description"].to(str), size="1")),
            rx.input(name=arg["name"].to(str), width="100%"),
            width="100%",
            spacing="1",
        )
    )


def _prompt_detail() -> rx.Component:
    sel = _S.catalog_selected
    return _component(
        rx.vstack(
            rx.heading(sel["fqn"].to(str), size="3"),
            _plain(sel["description"].to(str), size="2"),
            _read_form(rx.foreach(sel["arguments"].to(list[dict[str, Any]]), _argument), "Get"),
            width="100%",
        )
    )


def _detail() -> rx.Component:
    return _component(
        rx.vstack(
            rx.cond(
                _S.form_errors[""],
                rx.callout(_S.form_errors[""], color_scheme="red", size="1"),
            ),
            rx.match(
                _S.catalog_selected["tab"],
                ("tools", _tool_detail()),
                ("resources", _resource_detail()),
                ("templates", _template_detail()),
                ("prompts", _prompt_detail()),
                rx.text("Select an entry.", size="2"),
            ),
            _read_result(),
            width="100%",
            data_testid="catalog-detail",
        )
    )


def catalog_browser() -> rx.Component:
    """The Catalog view (container ``data-testid="catalog-browser"``)."""
    return _component(
        rx.vstack(
            rx.hstack(
                rx.heading("Catalog", size="5"),
                rx.input(
                    placeholder="Filter",
                    value=_S.catalog_filter,
                    on_change=_S.set_catalog_filter,
                    debounce_timeout=FILTER_DEBOUNCE_MS,
                    size="2",
                ),
                align="center",
            ),
            rx.tabs.root(
                rx.tabs.list(*(rx.tabs.trigger(label, value=value) for value, label in _TABS)),
                value=_S.catalog_tab,
                on_change=_S.set_catalog_tab,
            ),
            rx.grid(
                rx.scroll_area(
                    rx.vstack(
                        rx.foreach(_S.catalog_shown, _entry),
                        rx.cond(
                            _S.catalog_more > 0,
                            rx.text(
                                _S.catalog_more.to_string(), " more — refine the filter", size="1"
                            ),
                        ),
                        spacing="1",
                    ),
                    max_height="70vh",
                    type="auto",
                ),
                _detail(),
                columns="minmax(220px, 1fr) 2fr",
                gap="4",
                width="100%",
            ),
            width="100%",
            data_testid="catalog-browser",
        )
    )
