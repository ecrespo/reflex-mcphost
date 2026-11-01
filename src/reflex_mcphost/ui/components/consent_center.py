"""``consent_center()``: pending prompts as a non-blocking side panel (API §8, REQ-UI-005).

A right-hand drawer with ``modal=False`` (never a page-blocking dialog), open while
``HostState.cards`` is non-empty, with one card per pending prompt from the
``PromptRegistry``. Consent cards name the asking Consumer, the FQN, the Server, the tool
description, the argument preview (already redacted by core) and a "definition changed"
banner; they offer the answers, targets and deny lifetimes of the card (only
``allow_once`` / ``deny`` once for unattributed requests) and state REQ-CNS-007 when a
server or global target is chosen. Trust cards show command, resolved command, args, cwd
and env **names** (risky ones flagged) or the URL template, destination and header
**names**.

Sampling consent cards (REQ-HSH-002, *deprecated, compatibility*) show the messages,
system prompt, model preferences and max tokens, with Approve / Edit (message texts →
``ConsentAnswer.edited_sampling``) / Reject; sampling-response review cards (REQ-HSH-004)
show the completion with Approve / Edit / Reject. Elicitation form cards render a
``schema_form`` over the requested schema with Submit / Decline / Cancel and keep their
field errors (REQ-HSH-013); url cards show the full URL and the Server, and only "Open and
accept" opens it (external redirect, REQ-HSH-015). Unattributed legacy requests carry a
badge (REQ-HSH-047).

Server-provided text is rendered through text components only — never Markdown or HTML
(Art. 5).
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.ui.components.catalog_browser import block_view
from reflex_mcphost.ui.components.schema_form import schema_form
from reflex_mcphost.ui.state import CARD_FIELD, HostState

__all__ = ["consent_center"]

_S: Any = HostState  # Reflex vars and event handlers are not typed for mypy (class-level Vars)


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


_LABELS = {
    "allow_once": "Allow once",
    "allow_session": "Allow for this session",
    "allow_always": "Always allow",
    "deny": "Deny",
}


def _card_input(card_id: Any) -> rx.Component:
    """The hidden ``CARD_FIELD`` of a card form (its ``on_submit`` cannot take the card)."""
    return _component(rx.el.input(type="hidden", name=CARD_FIELD, value=card_id))


def _field(label: str, value: Any) -> rx.Component:
    return _component(
        rx.hstack(
            rx.text(label, weight="bold", size="2"),
            rx.text(value, size="2", white_space="pre-wrap", word_break="break-word"),
            spacing="2",
            align="start",
        )
    )


def _answer_button(card: Any, choice: Any) -> rx.Component:
    label = rx.match(
        choice,
        *_LABELS.items(),
        choice,
    )
    return _component(
        rx.button(
            label,
            size="2",
            color_scheme=rx.cond(choice == "deny", "red", "grass"),
            on_click=_S.answer_card(card["id"], choice),
            data_testid="consent-answer",
        )
    )


def _consent_card(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    chosen = _S.chosen_targets[card_id]
    notice = card["scope_notice"].to(dict[str, str])
    return _component(
        rx.vstack(
            rx.hstack(
                rx.badge(card["asking"].to(str), color_scheme="blue"),
                rx.cond(card["unattributed"], rx.badge("unattributed", color_scheme="orange")),
            ),
            _field("Server", card["server_name"].to(str)),
            _field("Server id", card["server_id"].to(str)),
            rx.cond(card["is_sampling"], _sampling_section(card), _tool_section(card)),
            rx.hstack(
                rx.text("Scope", size="2"),
                rx.select(
                    card["targets"].to(list[str]),
                    default_value=card["targets"].to(list[str])[0],
                    on_change=lambda value: _S.choose_target(card_id, value),
                    size="1",
                ),
                rx.text("Deny for", size="2"),
                rx.select(
                    card["deny_lifetimes"].to(list[str]),
                    default_value="once",
                    on_change=lambda value: _S.choose_deny_lifetime(card_id, value),
                    size="1",
                ),
            ),
            rx.cond(
                chosen == "server",
                rx.text(notice["server"], size="1"),
                rx.cond(
                    chosen == "global",
                    rx.text(notice["global"], size="1"),
                ),
            ),
            rx.hstack(
                rx.foreach(card["answers"].to(list[str]), lambda c: _answer_button(card, c)),
                rx.cond(
                    card["is_sampling"],
                    rx.button(
                        "Edit",
                        size="2",
                        variant="soft",
                        on_click=_S.toggle_edit(card_id),
                        data_testid="sampling-edit",
                    ),
                ),
                wrap="wrap",
            ),
            rx.cond(_S.editing.contains(card_id), _sampling_edit_form(card)),
            width="100%",
            data_testid="consent-card",
        )
    )


def _tool_section(card: Any) -> rx.Component:
    return _component(
        rx.vstack(
            _field("Tool", card["fqn"].to(str)),
            rx.text(card["description"].to(str), size="2", white_space="pre-wrap"),
            rx.cond(
                card["definition_changed"],
                rx.callout(
                    "This tool's definition changed since it was last allowed.",
                    color_scheme="amber",
                    size="1",
                ),
            ),
            rx.text("Arguments", weight="bold", size="2"),
            rx.box(
                rx.text(card["preview"].to(str), font_family="monospace", white_space="pre-wrap"),
                width="100%",
                padding="2",
                background="var(--gray-a3)",
            ),
            width="100%",
        )
    )


def _sampling_section(card: Any) -> rx.Component:
    return _component(
        rx.vstack(
            rx.badge("Sampling request (deprecated, compatibility)", color_scheme="purple"),
            rx.text("Messages", weight="bold", size="2"),
            rx.foreach(card["messages"].to(list[dict[str, Any]]), block_view),
            _field("System prompt", card["system_prompt"].to(str)),
            _field("Max tokens", card["max_tokens"].to(str)),
            _field("Temperature", card["temperature"].to(str)),
            _field("Model hints", card["model_hints"].to(list[str]).join(", ")),
            _field("Priorities", card["priorities"].to(str)),
            _field("Include context", card["include_context"].to(str)),
            width="100%",
            data_testid="sampling-consent",
        )
    )


def _message_editor(message: Any) -> rx.Component:
    return _component(
        rx.cond(
            message["kind"] == "text",
            rx.vstack(
                rx.badge(message["role"].to(str), size="1"),
                rx.text_area(
                    name=message["field"].to(str),
                    default_value=message["text"].to(str),
                    width="100%",
                ),
                width="100%",
            ),
        )
    )


def _sampling_edit_form(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    return _component(
        rx.form(
            rx.vstack(
                _card_input(card_id),
                rx.foreach(card["messages"].to(list[dict[str, Any]]), _message_editor),
                rx.button("Approve edited (once)", type="submit"),
                width="100%",
            ),
            on_submit=_S.approve_edited_sampling_form,
            reset_on_submit=False,
            width="100%",
        )
    )


def _origin_badges(card: Any) -> rx.Component:
    return _component(
        rx.hstack(
            rx.badge(card["server_name"].to(str), color_scheme="blue"),
            rx.cond(card["unattributed"], rx.badge("unattributed", color_scheme="orange")),
        )
    )


def _elicitation_buttons(card_id: Any) -> tuple[rx.Component, ...]:
    return (
        rx.button(
            "Decline",
            type="button",
            color_scheme="red",
            variant="soft",
            on_click=_S.answer_elicitation(card_id, "decline"),
            data_testid="elicitation-decline",
        ),
        rx.button(
            "Cancel",
            type="button",
            variant="soft",
            on_click=_S.answer_elicitation(card_id, "cancel"),
            data_testid="elicitation-cancel",
        ),
    )


def _elicitation_form_card(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    ui_errors = _S.elicitation_errors[card_id].to(dict[str, str])
    core_errors = card["errors"].to(dict[str, str])
    return _component(
        rx.vstack(
            _origin_badges(card),
            _field("Server id", card["server_id"].to(str)),
            rx.text(card["message"].to(str), size="2", white_space="pre-wrap"),
            schema_form(
                card["fields"].to(list[dict[str, Any]]),
                _S.submit_elicitation_form,
                error_of=lambda name: rx.cond(ui_errors[name], ui_errors[name], core_errors[name]),
                json_mode=card["json_fallback"].to(bool),
                actions=_elicitation_buttons(card_id),
                testid="elicitation-form",
                hidden=(_card_input(card_id),),
            ),
            width="100%",
            data_testid="elicitation-card",
        )
    )


def _elicitation_url_card(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    return _component(
        rx.vstack(
            _origin_badges(card),
            _field("Server id", card["server_id"].to(str)),
            rx.text(card["message"].to(str), size="2", white_space="pre-wrap"),
            rx.cond(
                card["url_refused"],
                rx.callout(
                    "The Server's URL is not an http(s) address and cannot be opened.",
                    color_scheme="red",
                    size="1",
                ),
            ),
            rx.text("This Server asks you to open:", weight="bold", size="2"),
            rx.text(
                card["url"].to(str),
                font_family="monospace",
                size="2",
                word_break="break-all",
                data_testid="elicitation-url",
            ),
            rx.hstack(
                rx.button(
                    "Open and accept",
                    disabled=card["url_refused"].to(bool),
                    on_click=_S.answer_elicitation(card_id, "accept"),
                    data_testid="elicitation-open",
                ),
                *_elicitation_buttons(card_id),
                wrap="wrap",
            ),
            width="100%",
            data_testid="elicitation-card",
        )
    )


def _review_card(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    completion = card["completion"].to(dict[str, Any])
    return _component(
        rx.vstack(
            rx.badge("Review the sampling result", color_scheme="purple"),
            _field("Server id", card["server_id"].to(str)),
            _field("Model", card["model"].to(str)),
            _field("Stop reason", card["stop_reason"].to(str)),
            block_view(completion),
            rx.hstack(
                rx.button("Approve", on_click=_S.answer_review(card_id, True)),
                rx.cond(
                    card["editable"],
                    rx.button("Edit", variant="soft", on_click=_S.toggle_edit(card_id)),
                ),
                rx.button("Reject", color_scheme="red", on_click=_S.answer_review(card_id, False)),
                wrap="wrap",
            ),
            rx.cond(
                _S.editing.contains(card_id),
                rx.form(
                    rx.vstack(
                        _card_input(card_id),
                        rx.text_area(
                            name="completion",
                            default_value=completion["text"].to(str),
                            width="100%",
                        ),
                        rx.button("Approve edited", type="submit"),
                        width="100%",
                    ),
                    on_submit=_S.submit_review_edit_form,
                    reset_on_submit=False,
                    width="100%",
                ),
            ),
            width="100%",
            data_testid="review-card",
        )
    )


def _env_name(item: Any) -> rx.Component:
    return _component(
        rx.badge(
            item["name"].to(str),
            color_scheme=rx.cond(item["risky"], "red", "gray"),
            variant=rx.cond(item["risky"], "solid", "soft"),
        )
    )


def _trust_card(card: Any) -> rx.Component:
    card_id = card["id"].to(str)
    return _component(
        rx.vstack(
            rx.badge("Trust this project Server?", color_scheme="orange"),
            _field("Server id", card["server_id"].to(str)),
            _field("Defined in", card["origin_path"].to(str)),
            rx.cond(
                card["transport"] == "http",
                rx.vstack(
                    _field("URL (as written)", card["url_template"].to(str)),
                    _field("Destination", card["destination"].to(str)),
                    _field("Header names", card["header_keys"].to(list[str]).join(", ")),
                ),
                rx.vstack(
                    _field("Command", card["command"].to(str)),
                    _field("Runs", card["resolved_command"].to(str)),
                    _field("Arguments", card["args"].to(list[str]).join(" ")),
                    _field("Working directory", card["cwd"].to(str)),
                    rx.text("Environment variable names", weight="bold", size="2"),
                    rx.hstack(
                        rx.foreach(card["env"].to(list[dict[str, Any]]), _env_name), wrap="wrap"
                    ),
                ),
            ),
            rx.hstack(
                rx.button("Trust", on_click=_S.answer_trust(card_id, True)),
                rx.button(
                    "Don't trust",
                    color_scheme="red",
                    on_click=_S.answer_trust(card_id, False),
                ),
            ),
            width="100%",
            data_testid="trust-card",
        )
    )


def _pending_card(card: Any) -> rx.Component:
    return _component(
        rx.vstack(
            rx.badge(card["kind"].to(str)),
            _field("Server id", card["server_id"].to(str)),
            width="100%",
        )
    )


def _card(card: Any) -> rx.Component:
    return _component(
        rx.card(
            rx.match(
                card["kind"],
                ("consent", _consent_card(card)),
                ("trust", _trust_card(card)),
                ("elicitation_form", _elicitation_form_card(card)),
                ("elicitation_url", _elicitation_url_card(card)),
                ("sampling_review", _review_card(card)),
                _pending_card(card),
            ),
            width="100%",
        )
    )


def consent_center() -> rx.Component:
    """The pending-prompts side panel (non-blocking; one per page)."""
    return _component(
        rx.drawer.root(
            rx.drawer.portal(
                rx.drawer.content(
                    rx.scroll_area(
                        rx.vstack(
                            rx.heading("Pending requests", size="4"),
                            rx.cond(
                                _S.prompt_error != "",
                                rx.callout(_S.prompt_error, color_scheme="red", size="1"),
                            ),
                            rx.foreach(_S.cards, _card),
                            spacing="3",
                            padding="4",
                        ),
                        height="100%",
                    ),
                    position="fixed",
                    top="0",
                    right="0",
                    height="100vh",
                    width="min(420px, 100vw)",
                    background="var(--color-panel-solid)",
                    data_testid="consent-center",
                ),
            ),
            open=_S.cards.length() > 0,
            modal=False,
            dismissible=False,
            direction="right",
        )
    )
