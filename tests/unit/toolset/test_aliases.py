"""Agent-facing aliases (F5-01; API §4.12 alias algorithm, delta 2026-10-phase5-prep D2)."""

from __future__ import annotations

import hashlib
import random
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from reflex_mcphost.core.toolset import aliases
from reflex_mcphost.core.toolset.aliases import assign_aliases, sanitize
from reflex_mcphost.core.types import Fqn

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

server_ids = st.from_regex(r"\A[A-Za-z0-9_.-]{1,64}\Z")
names = st.text(min_size=1, max_size=200)
fqn_sets = st.lists(
    st.builds(lambda s, n: Fqn(server_id=s, name=n), server_ids, names), max_size=12
)


def _h(fqn: str) -> str:
    return hashlib.sha256(fqn.encode("utf-8")).hexdigest()[:8]


@settings(max_examples=500, deadline=None)
@given(fqn_sets, st.randoms(use_true_random=False))
def test_aliases_are_valid_distinct_and_order_independent(
    fqns: list[Fqn], rnd: random.Random
) -> None:
    """REQ-TLS-002: for arbitrary FQN sets every alias matches ^[A-Za-z0-9_-]{1,64}$, the
    aliases are pairwise distinct, the map is identical for any permutation of the input,
    and each alias maps back to its FQN."""
    mapping = assign_aliases(fqns)
    assert set(mapping) == set(fqns)
    assert all(ALIAS_RE.fullmatch(a) for a in mapping.values())
    assert len(set(mapping.values())) == len(mapping)
    shuffled = list(fqns)
    rnd.shuffle(shuffled)
    assert assign_aliases(shuffled) == mapping
    back = {alias: fqn for fqn, alias in mapping.items()}
    assert all(back[mapping[f]] == f for f in fqns)


def test_plain_alias() -> None:
    """REQ-TLS-002: a safe, short, unique candidate is used as is."""
    fqn = Fqn.parse("modern::echo")
    assert assign_aliases([fqn]) == {fqn: "modern__echo"}


def test_double_underscore_ambiguity_is_hashed() -> None:
    """REQ-TLS-002: `a__b::c` and `a::b__c` share a candidate, so both are hashed."""
    one, two = Fqn.parse("a__b::c"), Fqn.parse("a::b__c")
    mapping = assign_aliases([one, two])
    assert mapping[one] == f"a__b__c_{_h('a__b::c')}"
    assert mapping[two] == f"a__b__c_{_h('a::b__c')}"
    assert mapping[one] != mapping[two]


@pytest.mark.parametrize("name", ["get.item", "x::y", "grüßen", "with space"])
def test_unsafe_names_are_hashed(name: str) -> None:
    """REQ-TLS-002: a name with `.`, `::`, non-ASCII or spaces is sanitized and hashed."""
    fqn = Fqn(server_id="srv", name=name)
    alias = assign_aliases([fqn])[fqn]
    assert alias == f"srv__{sanitize(name)}_{_h(str(fqn))}"
    assert ALIAS_RE.fullmatch(alias)


def test_unsafe_server_id_is_hashed() -> None:
    """REQ-TLS-002: a `.` in the server id (valid ServerId) is not alias-safe."""
    fqn = Fqn.parse("my.srv::echo")
    assert assign_aliases([fqn])[fqn] == f"my_srv__echo_{_h('my.srv::echo')}"


def test_length_boundary() -> None:
    """REQ-TLS-002: a 64-char candidate stays plain, a 65-char one is hashed (≤ 64)."""
    plain = Fqn(server_id="s", name="n" * 61)  # "s__" + 61 = 64
    long = Fqn(server_id="s", name="n" * 62)
    mapping = assign_aliases([plain, long])
    assert mapping[plain] == "s__" + "n" * 61
    assert mapping[long] == ("s__" + "n" * 62)[:55] + "_" + _h(str(long))
    assert len(mapping[long]) == 64


def test_plain_candidate_spelled_like_a_hashed_alias_is_hashed_too() -> None:
    """REQ-TLS-002 (delta D2 adversarial case): a Server names a tool after another tool's
    hashed alias; both end up hashed and distinct, nothing is shadowed."""
    victim = Fqn.parse("github::get.issue")
    victim_alias = f"github__get_issue_{_h(str(victim))}"
    attacker = Fqn(server_id="github", name=victim_alias.removeprefix("github__"))
    mapping = assign_aliases([victim, attacker])
    assert mapping[victim] == victim_alias
    assert mapping[attacker] != victim_alias
    assert mapping[attacker] == f"{victim_alias}"[:55] + "_" + _h(str(attacker))


def test_chained_adversarial_candidates_reach_a_fixpoint() -> None:
    """REQ-TLS-002 (D2 step 3): hashing one plain tool can expose another; iterate."""
    first = Fqn.parse("s::a.b")
    second_alias = f"s__a_b_{_h(str(first))}"
    second = Fqn(server_id="s", name=second_alias.removeprefix("s__"))
    third = Fqn(server_id="s", name=(second_alias[:55] + "_" + _h(str(second)))[3:])
    mapping = assign_aliases([first, second, third])
    assert len(set(mapping.values())) == 3
    assert mapping[first] == second_alias


def test_equal_hashes_get_ordinal_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-TLS-002 (D2 step 5): if two hashed aliases are still equal, the first by
    str(fqn) keeps it and the k-th gets alias[:61] + "_" + f"{k:02d}", skipping values in
    use."""
    monkeypatch.setattr(aliases, "_digest", lambda fqn: "deadbeef")
    # Same candidate "s__x_1" → all hashed → same (forced) hash; str order: " " < "." < ":".
    a, b, c = Fqn.parse("s::x 1"), Fqn.parse("s::x.1"), Fqn.parse("s::x:1")
    squatter = Fqn(server_id="s", name="x_1_deadbeef_01")  # plain, = b's first try
    mapping = assign_aliases([c, b, a, squatter])
    assert mapping[a] == "s__x_1_deadbeef"
    assert mapping[squatter] == "s__x_1_deadbeef_01"
    assert mapping[b] == "s__x_1_deadbeef_02"
    assert mapping[c] == "s__x_1_deadbeef_03"


def test_duplicates_in_input_collapse() -> None:
    """REQ-TLS-002: the same FQN listed twice is one tool, not a collision."""
    fqn = Fqn.parse("modern::echo")
    assert assign_aliases([fqn, fqn]) == {fqn: "modern__echo"}


def test_sanitize() -> None:
    """REQ-TLS-002: every character outside [A-Za-z0-9_-] becomes `_`."""
    assert sanitize("a.b:c-d_e é") == "a_b_c-d_e__"
