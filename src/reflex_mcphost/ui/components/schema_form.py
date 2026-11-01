"""``schema_form``: a form generated from a JSON Schema's view model (F6-05; REQ-UI-004).

The fields come from ``ui.models.form_fields`` (a Var: the form is rendered on the client
from state, so the same builder serves the catalog's invoke form and the elicitation cards
inside ``rx.foreach``). Field kinds: ``string`` → text input, ``password`` → password input,
``enum`` → select, ``integer`` / ``number`` → text input (coerced in the backend),
``boolean`` → switch, ``string_list`` / ``enum_list`` → one value per line, ``json`` → a
JSON text area. A schema outside the subset (``json_fallback``) — or the "edit as JSON"
mode — is one JSON text area named ``ui.models.JSON_FIELD``. Raw values are coerced by
``ui.models.coerce_form`` in the event handler, never in the browser.

Titles, descriptions and option labels are Server-provided: they are rendered as text
children only (React escapes them) — never Markdown or HTML (Art. 5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import reflex as rx

from reflex_mcphost.ui.models import JSON_FIELD

__all__ = ["field_control", "schema_form"]

ErrorOf = Callable[[Any], Any]
"""Field name Var → its error message Var (``""`` / falsy: no error)."""


def _component(value: object) -> rx.Component:
    """Narrow a Reflex factory result (typed ``Any`` by Reflex's lazy namespace)."""
    if not isinstance(value, rx.Component):
        raise TypeError(f"expected a Reflex component, got {type(value).__name__}")
    return value


def _select(field: Any, name: Any) -> rx.Component:
    options = field["options"].to(list[str])
    labels = field["option_labels"].to(list[str])
    return _component(
        rx.select.root(
            rx.select.trigger(placeholder="Choose…"),
            rx.select.content(
                rx.foreach(options, lambda value, i: rx.select.item(labels[i], value=value))
            ),
            name=name,
        )
    )


def _lines_area(field: Any, name: Any) -> rx.Component:
    hint = rx.cond(
        field["kind"] == "enum_list",
        "One per line: " + field["options"].to(list[str]).join(", "),
        "One value per line",
    )
    return _component(rx.text_area(name=name, placeholder=hint, rows="3", width="100%"))


def field_control(field: Any) -> rx.Component:
    """The input of one field view model (``ui.models.form_fields`` row)."""
    name = field["name"].to(str)
    return _component(
        rx.match(
            field["kind"],
            ("password", rx.input(name=name, type="password", width="100%")),
            ("enum", _select(field, name)),
            ("integer", rx.input(name=name, input_mode="numeric", width="100%")),
            ("number", rx.input(name=name, input_mode="decimal", width="100%")),
            ("boolean", rx.switch(name=name)),
            ("string_list", _lines_area(field, name)),
            ("enum_list", _lines_area(field, name)),
            (
                "json",
                rx.text_area(
                    name=name, placeholder="JSON", font_family="monospace", rows="4", width="100%"
                ),
            ),
            rx.input(name=name, width="100%"),
        )
    )


def _field(field: Any, error_of: ErrorOf) -> rx.Component:
    name = field["name"].to(str)
    error = error_of(name)
    return _component(
        rx.vstack(
            rx.hstack(
                rx.text(field["label"].to(str), weight="bold", size="2"),
                rx.cond(field["required"], rx.text("*", color="var(--red-11)", size="2")),
                spacing="1",
            ),
            rx.cond(
                field["description"].to(str) != "",
                rx.text(field["description"].to(str), size="1", white_space="pre-wrap"),
            ),
            field_control(field),
            rx.cond(error, rx.text(error, size="1", color="var(--red-11)")),
            width="100%",
            spacing="1",
        )
    )


def schema_form(
    fields: Any,
    on_submit: Any,
    *,
    error_of: ErrorOf,
    json_mode: Any = False,
    actions: tuple[rx.Component, ...] = (),
    submit_label: str = "Submit",
    testid: str = "schema-form",
) -> rx.Component:
    """A form over ``fields`` (a Var of field view models); ``json_mode`` (a Var or bool)
    replaces the fields with one JSON text area. ``actions`` are rendered next to the
    submit button (e.g. Decline / Cancel). The form-level error is ``error_of("")``."""
    form_error = error_of("")
    return _component(
        rx.form(
            rx.vstack(
                rx.cond(
                    json_mode,
                    rx.text_area(
                        name=JSON_FIELD,
                        placeholder="{ }",
                        font_family="monospace",
                        rows="6",
                        width="100%",
                    ),
                    rx.foreach(fields, lambda field: _field(field, error_of)),
                ),
                rx.cond(form_error, rx.text(form_error, size="1", color="var(--red-11)")),
                rx.hstack(rx.button(submit_label, type="submit"), *actions, wrap="wrap"),
                width="100%",
                spacing="3",
            ),
            on_submit=on_submit,
            reset_on_submit=False,
            width="100%",
            data_testid=testid,
        )
    )
