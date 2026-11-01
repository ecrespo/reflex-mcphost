"""``schema_form``: the generated form component (F6-05; REQ-UI-004)."""

from __future__ import annotations

from typing import Any

import pytest

rx = pytest.importorskip("reflex")

from reflex_mcphost.ui.components.schema_form import schema_form  # noqa: E402
from reflex_mcphost.ui.models import JSON_FIELD  # noqa: E402
from reflex_mcphost.ui.state import HostState  # noqa: E402

_S: Any = HostState


def _render(**kwargs: Any) -> str:
    form = schema_form(
        _S.catalog_selected["fields"].to(list[dict[str, Any]]),
        _S.invoke_tool,
        error_of=lambda name: _S.form_errors[name],
        **kwargs,
    )
    return str(form)


def test_controls_per_field_kind() -> None:
    """REQ-UI-004: the form maps each field kind to its control — password input,
    select with labelled options, switch, one-per-line text areas, JSON text area, text
    input — plus required markers, descriptions and per-field errors."""
    rendered = _render()
    assert "RadixFormRoot" in rendered
    for case in (
        '"password"',
        '"enum"',
        '"boolean"',
        '"string_list"',
        '"enum_list"',
        '"json"',
        "RadixThemesSwitch",
        "RadixThemesSelect.Item",
        "RadixThemesTextArea",
        "RadixThemesTextField.Root",
        "option_labels",
        "required",
        "description",
        "form_errors",
    ):
        assert case in rendered, case


def test_json_mode_is_one_text_area() -> None:
    """REQ-UI-004, REQ-INV-002: the JSON mode (or fallback) is one text area named
    ``JSON_FIELD``; extra actions render next to the submit button."""
    rendered = _render(json_mode=True, actions=(rx.button("Decline", type="button"),))
    assert JSON_FIELD in rendered
    assert "Decline" in rendered


def test_untrusted_text_is_plain() -> None:
    """Art. 5, REQ-CAT-011: titles, descriptions and option labels are text children —
    no Markdown and no raw HTML."""
    rendered = _render()
    for forbidden in ("Markdown", "dangerouslySetInnerHTML", "innerHTML"):
        assert forbidden not in rendered
