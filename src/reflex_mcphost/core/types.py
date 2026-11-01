"""Identifier and Consumer types (API §2.1, §3.3)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, StringConstraints
from ulid import ULID

_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
_ID_RE = re.compile(_ID_PATTERN)
FQN_SEPARATOR = "::"

ServerId = Annotated[str, StringConstraints(pattern=_ID_PATTERN)]
"""A Server id (REQ-REG-012). Cannot contain ``::`` since ``:`` is outside the alphabet."""

ConsumerName = Annotated[str, StringConstraints(pattern=_ID_PATTERN)]


def parse_server_id(value: str) -> str:
    """Validate a Server id, returning it unchanged; raise ``ValueError`` otherwise."""
    # fullmatch, not match: `$` alone would accept a trailing newline.
    if _ID_RE.fullmatch(value) is None:
        raise ValueError(f"invalid server id {value!r}: must match {_ID_PATTERN}")
    return value


class Fqn(BaseModel, frozen=True):
    """Fully qualified catalog name ``<server_id>::<name>`` (REQ-CAT-002)."""

    server_id: ServerId
    name: Annotated[str, StringConstraints(min_length=1)]

    @classmethod
    def parse(cls, value: str) -> Fqn:
        server_id, sep, name = value.partition(FQN_SEPARATOR)
        if not sep or not name:
            raise ValueError(f"invalid FQN {value!r}: expected '<server_id>::<name>'")
        return cls(server_id=parse_server_id(server_id), name=name)

    def __str__(self) -> str:
        return f"{self.server_id}{FQN_SEPARATOR}{self.name}"


class ConsumerKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    APP = "app"


class Consumer(BaseModel, frozen=True):
    """Who is asking: a label for consent and audit (REQ-CNS-015, API §2.1)."""

    kind: ConsumerKind
    name: ConsumerName

    @classmethod
    def human(cls, name: str = "ui") -> Consumer:
        return cls(kind=ConsumerKind.HUMAN, name=name)

    @classmethod
    def agent(cls, name: str) -> Consumer:
        return cls(kind=ConsumerKind.AGENT, name=name)

    @classmethod
    def app(cls, server_id: str) -> Consumer:
        return cls(kind=ConsumerKind.APP, name=server_id)

    @classmethod
    def parse(cls, value: str) -> Consumer:
        kind, sep, name = value.partition(":")
        if not sep:
            raise ValueError(f"invalid consumer {value!r}: expected '<kind>:<name>'")
        try:
            consumer_kind = ConsumerKind(kind)
        except ValueError:
            raise ValueError(f"invalid consumer kind {kind!r}") from None
        if _ID_RE.fullmatch(name) is None:
            raise ValueError(f"invalid consumer name {name!r}: must match {_ID_PATTERN}")
        return cls(kind=consumer_kind, name=name)

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.name}"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def new_invocation_id() -> str:
    return _new_id("inv")


def new_rule_id() -> str:
    return _new_id("rule")


def new_request_id() -> str:
    return _new_id("req")
