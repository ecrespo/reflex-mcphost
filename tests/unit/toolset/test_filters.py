"""Toolset include/exclude filters (F5-02; API §4.12, delta D5)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.toolset.filters import selected
from reflex_mcphost.core.types import Fqn
from tests.unit.toolset.fakes import Rig, tool

ALL = ["github::search", "github::create_issue", "github2::search", "local::echo", "Git::x"]


@pytest.mark.parametrize(
    ("include", "exclude", "expected"),
    [
        (("*",), (), ALL),
        (["github"], [], ["github::search", "github::create_issue"]),
        (["github::*"], [], ["github::search", "github::create_issue"]),
        (["*::search"], [], ["github::search", "github2::search"]),
        (["github*"], ["github::create_issue"], ["github::search", "github2::search"]),
        (["github"], ["github"], []),
        (["*"], ["*::search", "local"], ["github::create_issue", "Git::x"]),
        (["git"], [], []),  # case-sensitive: "git" is not "Git"
        (["GIT*"], [], []),
        (["Git::*"], [], ["Git::x"]),
        ([], [], []),
    ],
)
def test_filter_table(include: list[str], exclude: list[str], expected: list[str]) -> None:
    """REQ-TLS-003: bare entries are globs on the server id (`github` ≠ `github2`), entries
    with `::` globs on the FQN; exclude wins; matching is case-sensitive."""
    got = [f for f in ALL if selected(Fqn.parse(f), include, exclude)]
    assert got == expected


def test_toolset_applies_filters_before_aliasing() -> None:
    """REQ-TLS-003, REQ-TLS-002: only matching tools are listed and callable; the collision
    domain is the filtered listing (a filtered-out twin does not force hashing)."""
    rig = Rig()
    rig.catalog.entries = [tool("a__b::c"), tool("a::b__c"), tool("other::echo")]
    both = rig.toolset()
    assert all(len(s.name) > len("a__b__c") for s in both.list() if s.fqn.name != "echo")
    only_a = rig.toolset(include=["a"])
    assert [s.name for s in only_a.list()] == ["a__b__c"]
    assert only_a.resolve("a__b__c") == Fqn.parse("a::b__c")
    with pytest.raises(KeyError):
        only_a.resolve("other__echo")
