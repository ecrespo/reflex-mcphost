"""``form_fields`` / ``coerce_form``: the schema-generated form's pure logic (F6-05; API §8).

No Reflex: ``ui.models`` is Reflex-free (import-linter contract).
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from reflex_mcphost.ui import models

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Your name", "description": "Shown to others"},
        "color": {"type": "string", "enum": ["red", "green"]},
        "secret": {"type": "string", "format": "password"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "active": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "sizes": {"type": "array", "items": {"enum": ["s", "m", "l"]}},
        "address": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
    "required": ["name"],
}


def _kinds(schema: dict[str, Any]) -> dict[str, str]:
    fields, fallback = models.form_fields(schema)
    assert not fallback
    return {f["name"]: f["kind"] for f in fields}


def test_form_fields_map_the_supported_subset() -> None:
    """REQ-UI-004: string (enum → select, ``format: password`` → password), integer,
    number, boolean, arrays of strings / enums; anything else (a nested object) falls back
    to a per-field JSON text area; titles, descriptions and required markers are kept."""
    assert _kinds(SCHEMA) == {
        "name": "string",
        "color": "enum",
        "secret": "password",
        "count": "integer",
        "ratio": "number",
        "active": "boolean",
        "tags": "string_list",
        "sizes": "enum_list",
        "address": "json",
    }
    fields, _ = models.form_fields(SCHEMA)
    by_name = {f["name"]: f for f in fields}
    assert by_name["name"]["label"] == "Your name"
    assert by_name["name"]["description"] == "Shown to others"
    assert by_name["name"]["required"] is True
    assert by_name["count"]["required"] is False
    assert by_name["color"]["options"] == ["red", "green"]
    assert by_name["sizes"]["options"] == ["s", "m", "l"]


def test_elicitation_titled_options_and_enum_names() -> None:
    """REQ-HSH-010 (form reused for elicitation, F6-06): ``oneOf`` const/title, ``enum`` +
    ``enumNames`` and titled multi-select ``items.anyOf`` become selects with labels."""
    schema = {
        "type": "object",
        "properties": {
            "one": {"type": "string", "oneOf": [{"const": "a", "title": "Alpha"}]},
            "named": {"type": "string", "enum": ["x", "y"], "enumNames": ["Ex", "Why"]},
            "many": {"type": "array", "items": {"anyOf": [{"const": "p", "title": "Pea"}]}},
        },
    }
    fields, _ = models.form_fields(schema)
    by_name = {f["name"]: f for f in fields}
    assert (by_name["one"]["kind"], by_name["one"]["options"]) == ("enum", ["a"])
    assert by_name["one"]["option_labels"] == ["Alpha"]
    assert by_name["named"]["option_labels"] == ["Ex", "Why"]
    assert (by_name["many"]["kind"], by_name["many"]["options"]) == ("enum_list", ["p"])


@pytest.mark.parametrize(
    "schema",
    [
        {"oneOf": [{"type": "object"}]},
        {"type": "array"},
        {"$ref": "#/$defs/x"},
        "not a schema",
        {"type": "object", "properties": ["x"]},
    ],
)
def test_schemas_outside_the_subset_fall_back_to_json(schema: Any) -> None:
    """REQ-UI-004, REQ-INV-002: a top-level schema outside the subset is one JSON text
    area (the Invoker validates anyway)."""
    assert models.form_fields(schema) == ([], True)


@pytest.mark.parametrize(
    ("raw", "args", "errors"),
    [
        ({"name": "Ada"}, {"name": "Ada"}, {}),
        ({}, {}, {"name": "this field is required"}),
        ({"name": "  "}, {}, {"name": "this field is required"}),
        ({"name": "A", "color": "green"}, {"name": "A", "color": "green"}, {}),
        ({"name": "A", "color": "blue"}, {"name": "A"}, {"color": "choose one of the options"}),
        ({"name": "A", "secret": "pw-sentinel"}, {"name": "A", "secret": "pw-sentinel"}, {}),
        ({"name": "A", "count": " 42 "}, {"name": "A", "count": 42}, {}),
        ({"name": "A", "count": "4.5"}, {"name": "A"}, {"count": "must be an integer"}),
        ({"name": "A", "ratio": "0.25"}, {"name": "A", "ratio": 0.25}, {}),
        ({"name": "A", "ratio": "3"}, {"name": "A", "ratio": 3}, {}),
        ({"name": "A", "ratio": "nan"}, {"name": "A"}, {"ratio": "must be a number"}),
        ({"name": "A", "active": "on"}, {"name": "A", "active": True}, {}),
        ({"name": "A", "active": False}, {"name": "A", "active": False}, {}),
        ({"name": "A", "tags": "a\n\n b \n"}, {"name": "A", "tags": ["a", "b"]}, {}),
        ({"name": "A", "tags": ["x", "y"]}, {"name": "A", "tags": ["x", "y"]}, {}),
        ({"name": "A", "sizes": "s\nl"}, {"name": "A", "sizes": ["s", "l"]}, {}),
        ({"name": "A", "sizes": "s\nxl"}, {"name": "A"}, {"sizes": "choose one of the options"}),
        (
            {"name": "A", "address": '{"city": "Oslo"}'},
            {"name": "A", "address": {"city": "Oslo"}},
            {},
        ),
        ({"name": "A", "address": "{nope"}, {"name": "A"}, {"address": "invalid JSON"}),
        ({"name": "A", "unknown": "x"}, {"name": "A"}, {}),
    ],
)
def test_coerce_form_table(
    raw: dict[str, Any], args: dict[str, Any], errors: dict[str, str]
) -> None:
    """REQ-UI-004: raw form values coerce per field kind; empty optional fields are
    omitted; a missing required field and an unparsable value are field errors."""
    assert models.coerce_form(SCHEMA, raw) == (args, errors)


def test_required_boolean_off_is_false() -> None:
    """REQ-UI-004: an unchecked required switch is sent as ``false``."""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    assert models.coerce_form(schema, {}) == ({"ok": False}, {})


def test_json_mode_and_fallback() -> None:
    """REQ-UI-004, REQ-INV-002: the JSON text area (fallback or "edit as JSON") must hold
    an object; it is sent as is (the Invoker validates)."""
    key = models.JSON_FIELD
    assert models.coerce_form({"oneOf": []}, {key: '{"a": 1}'}) == ({"a": 1}, {})
    assert models.coerce_form(SCHEMA, {key: '{"name": 5}'}) == ({"name": 5}, {})
    assert models.coerce_form(SCHEMA, {key: "[1]"}) == ({}, {"": "arguments must be a JSON object"})
    assert models.coerce_form(SCHEMA, {key: "{"}) == ({}, {"": "invalid JSON"})
    assert models.coerce_form({"oneOf": []}, {key: "  "}) == ({}, {})


def test_issue_errors_map_invoker_issues_to_fields() -> None:
    """REQ-UI-004, REQ-INV-002: ``InvocationError.details.issues`` map back to the field of
    their path; a root ``required`` issue names its property; others are form-level."""
    details = {
        "issues": [
            {"path": "/count", "keyword": "type", "message": "value is not of type 'integer'"},
            {"path": "", "keyword": "required", "message": "'name' is a required property"},
            {"path": "", "keyword": "additionalProperties", "message": "extra"},
        ]
    }
    assert models.issue_errors(details) == {
        "count": "value is not of type 'integer'",
        "name": "'name' is a required property",
        "": "extra",
    }
    assert models.issue_errors({"issues": "garbage"}) == {}
    assert models.issue_errors(None) == {}


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


@settings(max_examples=300, deadline=None)
@given(raw=st.dictionaries(st.sampled_from([*SCHEMA["properties"], models.JSON_FIELD]), _JSON))
def test_coercion_never_raises(raw: dict[str, Any]) -> None:
    """REQ-UI-004: coercion never raises on arbitrary raw input — it returns arguments and
    field errors."""
    args, errors = models.coerce_form(SCHEMA, raw)
    assert isinstance(args, dict)
    assert isinstance(errors, dict)


@settings(max_examples=200, deadline=None)
@given(schema=_JSON, raw=st.dictionaries(st.text(max_size=8), _JSON, max_size=4))
def test_coercion_never_raises_on_arbitrary_schemas(schema: Any, raw: dict[str, Any]) -> None:
    """REQ-UI-004, Art. 5: a Server-provided schema of any shape never breaks the form."""
    fields, _ = models.form_fields(schema)
    assert isinstance(fields, list)
    models.coerce_form(schema, raw)
