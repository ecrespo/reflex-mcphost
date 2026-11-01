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

Server-provided text is rendered through text components only — never Markdown or HTML
(Art. 5). Sampling and elicitation cards are F6-06; until then they are listed by kind.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from reflex_mcphost.ui.state import HostState

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
            _field("Tool", card["fqn"].to(str)),
            _field("Server", card["server_name"].to(str)),
            _field("Server id", card["server_id"].to(str)),
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
                wrap="wrap",
            ),
            width="100%",
            data_testid="consent-card",
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
