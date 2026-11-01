"""``docs/library.md`` agent snippets are the code the integration tests run (F5-05)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "library.md"
TESTS = REPO / "tests" / "integration" / "adapters"


def _blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)


@pytest.mark.parametrize(
    ("marker", "test_file"),
    [
        ("as_pydantic_ai_toolset", "test_pydantic_ai_agent.py"),
        ("as_langchain_tools", "test_langgraph_agent.py"),
    ],
)
def test_snippet_lines_are_in_the_integration_test(marker: str, test_file: str) -> None:
    """REQ-TLS-006, REQ-TLS-007: every code line of the adapter snippet (imports
    excluded) appears verbatim in the integration test that drives it against the fixture
    Servers."""
    (block,) = [b for b in _blocks() if f"import {marker}" in b]
    source = (TESTS / test_file).read_text(encoding="utf-8")
    test_lines = {line.strip() for line in source.splitlines()}
    lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith(("from ", "import ", "#"))
    ]
    assert lines
    missing = [line for line in lines if line not in test_lines]
    assert missing == []
