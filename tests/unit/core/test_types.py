"""Identifier and Consumer types (API §2.1, §3.3)."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from reflex_mcphost.core.types import (
    Consumer,
    ConsumerKind,
    Fqn,
    ServerId,
    new_invocation_id,
    new_request_id,
    new_rule_id,
    parse_server_id,
)

ID_ALPHABET = st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
server_ids = st.text(ID_ALPHABET, min_size=1, max_size=64)
tool_names = st.text(min_size=1).filter(lambda s: s.strip() == s and s != "")


@given(server_ids)
def test_valid_server_ids_accepted(sid: str) -> None:
    """REQ-REG-012: ids matching ^[A-Za-z0-9_.-]{1,64}$ are accepted unchanged."""
    assert parse_server_id(sid) == sid


@given(st.text(max_size=80))
def test_server_id_validation_matches_pattern(sid: str) -> None:
    """REQ-REG-012: any id outside the pattern is rejected with ValueError."""
    ok = re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", sid) is not None
    if ok:
        assert parse_server_id(sid) == sid
    else:
        with pytest.raises(ValueError, match="server id"):
            parse_server_id(sid)


@pytest.mark.parametrize("bad", ["", "a::b", "a:b", "a b", "x" * 65, "ñ", "a/b"])
def test_server_id_rejects(bad: str) -> None:
    """REQ-REG-012: empty, '::', ':', spaces, >64 chars and non-ASCII are rejected."""
    with pytest.raises(ValueError, match="server id"):
        parse_server_id(bad)


def test_server_id_validates_in_models() -> None:
    """REQ-REG-012: ServerId is enforced inside Pydantic models."""

    class M(BaseModel):
        sid: ServerId

    assert M(sid="github").sid == "github"
    with pytest.raises(ValidationError):
        M(sid="git::hub")


@given(server_ids, st.text(min_size=1))
def test_fqn_round_trip(sid: str, name: str) -> None:
    """REQ-CAT-002: str(Fqn) parses back to the same value; name may contain '::'."""
    fqn = Fqn(server_id=sid, name=name)
    assert str(fqn) == f"{sid}::{name}"
    assert Fqn.parse(str(fqn)) == fqn


def test_fqn_splits_on_first_separator() -> None:
    """REQ-CAT-002: parse splits on the first '::'."""
    fqn = Fqn.parse("github::ns::create_issue")
    assert fqn.server_id == "github"
    assert fqn.name == "ns::create_issue"


@pytest.mark.parametrize("bad", ["github", "::tool", "github::", "bad id::tool", ""])
def test_fqn_parse_rejects(bad: str) -> None:
    """REQ-CAT-002: malformed FQNs raise ValueError."""
    with pytest.raises(ValueError, match="invalid"):
        Fqn.parse(bad)


def test_fqn_is_frozen_and_hashable() -> None:
    """REQ-CAT-002: Fqn is an immutable value usable as a dict key."""
    fqn = Fqn.parse("a::b")
    assert {fqn: 1}[Fqn(server_id="a", name="b")] == 1
    with pytest.raises(ValidationError):
        fqn.name = "c"  # type: ignore[misc]


def test_consumer_constructors_and_str() -> None:
    """REQ-CNS-015: human/agent/app constructors and string form kind:name."""
    assert str(Consumer.human()) == "human:ui"
    assert Consumer.agent("triage-bot").kind is ConsumerKind.AGENT
    assert str(Consumer.app("github")) == "app:github"
    assert Consumer.parse("agent:triage-bot") == Consumer.agent("triage-bot")


@pytest.mark.parametrize("bad", ["agent", "robot:x", "agent:", "agent:a b", "human:" + "x" * 65])
def test_consumer_parse_rejects(bad: str) -> None:
    """REQ-CNS-015: unknown kinds and invalid names are rejected."""
    with pytest.raises(ValueError, match="invalid consumer"):
        Consumer.parse(bad)


def test_consumer_name_validated_on_construction() -> None:
    """REQ-CNS-015: Consumer.name follows ^[A-Za-z0-9_.-]{1,64}$."""
    with pytest.raises(ValidationError):
        Consumer(kind=ConsumerKind.HUMAN, name="has:colon")


@given(st.sampled_from(list(ConsumerKind)), server_ids)
def test_consumer_round_trip(kind: ConsumerKind, name: str) -> None:
    """REQ-CNS-015: str(Consumer) parses back to the same value."""
    c = Consumer(kind=kind, name=name)
    assert Consumer.parse(str(c)) == c


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [(new_invocation_id, "inv_"), (new_rule_id, "rule_"), (new_request_id, "req_")],
)
def test_id_generators(factory: object, prefix: str) -> None:
    """API §3.3: ids are <prefix>_ + a 26-char ULID and are unique."""
    assert callable(factory)
    ids = {factory() for _ in range(100)}
    assert len(ids) == 100
    for i in ids:
        assert re.fullmatch(rf"{prefix}[0-9A-HJKMNP-TV-Z]{{26}}", i)
