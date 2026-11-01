"""Variable expansion in Server definitions (DD-013)."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reflex_mcphost.core.config.expand import (
    UnresolvedVariableError,
    UnsupportedVariableError,
    expand,
)

ENV = {"TOKEN": "abc", "EMPTY": "", "HOME": "/home/me"}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("Bearer ${TOKEN}", "Bearer abc"),
        ("${TOKEN}${TOKEN}", "abcabc"),
        ("${MISSING:-fallback}", "fallback"),
        ("${EMPTY:-fallback}", "fallback"),
        ("${TOKEN:-fallback}", "abc"),
        ("${MISSING:-}", ""),
        ("cost: $5 and $HOME", "cost: $5 and $HOME"),
        ("no variables", "no variables"),
    ],
)
def test_expands_supported_forms(template: str, expected: str) -> None:
    """REQ-REG-004: `${VAR}` and `${VAR:-default}` are substituted from the environment."""
    assert expand(template, ENV) == expected


def test_workspace_folder_expands_to_cwd() -> None:
    """REQ-REG-004: VS Code `${workspaceFolder}` expands to the project directory."""
    assert expand("${workspaceFolder}/src", ENV, cwd=Path("/proj")) == f"{Path('/proj')}/src"


def test_unset_variable_without_default_is_unresolved() -> None:
    """REQ-REG-005: an unset variable with no default names the variable."""
    with pytest.raises(UnresolvedVariableError) as info:
        expand("x ${NOPE} y", ENV)
    assert info.value.name == "NOPE"


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("${input:apiKey}", "input:apiKey"),
        ("${env:TOKEN}", "env:TOKEN"),
        ("${A:-${B}}", "A:-${B"),
        ("${}", ""),
        ("${1BAD}", "1BAD"),
        ("${workspaceFolder}", "workspaceFolder"),  # no cwd known
        ("unterminated ${TOKEN", "TOKEN"),
    ],
)
def test_unsupported_forms_name_the_variable(template: str, name: str) -> None:
    """REQ-REG-005: unsupported forms (VS Code `${input:…}` etc.) are rejected by name."""
    with pytest.raises(UnsupportedVariableError) as info:
        expand(template, ENV)
    assert info.value.name == name


@given(st.text().filter(lambda s: "${" not in s))
def test_text_without_placeholders_is_unchanged(text: str) -> None:
    """REQ-REG-004: expansion is the identity on strings with no `${`."""
    assert expand(text, ENV) == text
