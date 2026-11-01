"""Consent value types (API §4.7): targets, rules, requests, answers and previews.

A rule says *who* (a Consumer, or any), *which operation*, *on which target*, *allow or
deny* and *for how long*. Targets rank ``tool > server > global`` (REQ-CNS-003); rules
scoped to a Consumer outrank rules for any Consumer (REQ-CNS-015). ``lifetime`` is
``session`` (memory only, REQ-CNS-005) or ``always`` (``ConsentStore``, REQ-CNS-006);
``allow_once`` answers never become rules (REQ-CNS-004).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self, TypeAlias

from mcp_types import CreateMessageRequestParams
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.consent.outcome import ConsentOutcome, ConsentSource, OperationKind
from reflex_mcphost.core.prompter import SamplingReview
from reflex_mcphost.core.types import (
    Consumer,
    ConsumerKind,
    Fqn,
    ServerId,
    new_request_id,
    new_rule_id,
    parse_server_id,
)

__all__ = [
    "TARGET_SPECIFICITY",
    "ArgumentPreview",
    "ArgumentPreviewHook",
    "Attribution",
    "ConsentAnswer",
    "ConsentDecision",
    "ConsentOutcome",
    "ConsentRequest",
    "ConsentRule",
    "ConsentSource",
    "ConsentTarget",
    "DenyLifetime",
    "Effect",
    "Lifetime",
    "OperationKind",
    "RuleKey",
    "RuleOrigin",
    "TargetKind",
]

TargetKind = Literal["tool", "server", "global"]
Effect = Literal["allow", "deny"]
Lifetime = Literal["session", "always"]
DenyLifetime = Literal["once", "session", "always"]
RuleOrigin = Literal["prompt", "programmatic"]
Attribution = Literal["exact", "unattributed"]
RuleKey: TypeAlias = tuple[str, str, str, str, str, str]
"""``(operation, target_kind, server_id, fqn, consumer_kind, consumer_name)`` with ``''``
for absent parts — the unique key of ``consent_rule`` (DM §2.2)."""

TARGET_SPECIFICITY: Mapping[str, int] = {"tool": 3, "server": 2, "global": 1}
_RULE_ID_RE = re.compile(r"rule_[0-9A-HJKMNP-TV-Z]{26}")
_HASH_PREFIX = "sha256:"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ConsentDecision(StrEnum):
    """The four answers of a consent prompt (PRD vocabulary, API §4.7 mapping table)."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class ConsentTarget(BaseModel):
    """What a rule covers: one tool, every tool of a Server, or everything."""

    model_config = ConfigDict(frozen=True)

    kind: TargetKind
    server_id: ServerId | None = None
    fqn: Fqn | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "tool":
            if self.fqn is None or self.server_id != self.fqn.server_id:
                raise ValueError("a tool target needs an fqn and its server_id")
        elif self.kind == "server":
            if self.server_id is None or self.fqn is not None:
                raise ValueError("a server target needs a server_id and no fqn")
        elif self.server_id is not None or self.fqn is not None:
            raise ValueError("a global target has neither server_id nor fqn")
        return self

    @classmethod
    def tool(cls, fqn: Fqn | str) -> ConsentTarget:
        parsed = fqn if isinstance(fqn, Fqn) else Fqn.parse(fqn)
        return cls(kind="tool", server_id=parsed.server_id, fqn=parsed)

    @classmethod
    def server(cls, server_id: str) -> ConsentTarget:
        return cls(kind="server", server_id=parse_server_id(server_id))

    @classmethod
    def global_(cls) -> ConsentTarget:
        return cls(kind="global")

    @property
    def specificity(self) -> int:
        """``tool=3, server=2, global=1`` (REQ-CNS-003)."""
        return TARGET_SPECIFICITY[self.kind]

    def __str__(self) -> str:
        if self.kind == "tool":
            return f"tool {self.fqn}"
        return f"server {self.server_id}" if self.kind == "server" else "global"


class ConsentRule(BaseModel):
    """One stored decision (API §4.7). ``definition_hash`` is the tool definition hash at
    grant time for tool targets (REQ-CNS-007) and ``None`` otherwise."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_rule_id)
    effect: Effect
    operation: OperationKind
    target: ConsentTarget
    consumer: Consumer | None = None
    lifetime: Lifetime = "always"
    origin: RuleOrigin = "programmatic"
    definition_hash: str | None = None
    suspended: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("id")
    @classmethod
    def _rule_id(cls, value: str) -> str:
        if _RULE_ID_RE.fullmatch(value) is None:
            raise ValueError("rule id must be 'rule_' followed by a ULID")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.operation is OperationKind.SAMPLING and self.target.kind == "tool":
            raise ValueError("a tool target is invalid for sampling (use a server target)")
        if self.target.kind == "tool":
            if self.definition_hash is None or not self.definition_hash.startswith(_HASH_PREFIX):
                raise ValueError("a tool rule needs the tool's 'sha256:' definition hash")
        elif self.definition_hash is not None:
            raise ValueError("only tool rules carry a definition hash")
        return self

    @property
    def key(self) -> RuleKey:
        consumer = self.consumer
        return (
            self.operation.value,
            self.target.kind,
            self.target.server_id or "",
            str(self.target.fqn) if self.target.fqn is not None else "",
            consumer.kind.value if consumer is not None else "",
            consumer.name if consumer is not None else "",
        )

    @property
    def rank(self) -> tuple[int, int]:
        """``(target specificity, consumer specificity)``, compared lexicographically."""
        return (self.target.specificity, 2 if self.consumer is not None else 1)


class ArgumentPreview(BaseModel):
    """What the consent prompt shows of the arguments (REQ-CNS-011/012)."""

    model_config = ConfigDict(frozen=True)

    format: Literal["json", "markdown", "text"] = "json"
    body: str
    redacted_fields: tuple[str, ...] = ()


class ConsentRequest(BaseModel):
    """An operation no rule decided, presented to the Consumer (REQ-CNS-002).

    ``tool`` is required for ``tool_call`` and absent for ``sampling``. For an
    ``unattributed`` request (REQ-HSH-047) ``consumer`` is the human Consumer, the party
    asked.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_request_id)
    operation: OperationKind
    consumer: Consumer
    server_id: ServerId
    server_name: str
    tool: ToolEntry | None = None
    preview: ArgumentPreview = ArgumentPreview(format="text", body="")
    definition_changed: bool = False
    sampling: SamplingReview | None = None
    attribution: Attribution = "exact"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.operation is OperationKind.TOOL_CALL:
            if self.tool is None or self.tool.server_id != self.server_id:
                raise ValueError("a tool_call request needs the Server's tool entry")
        elif self.tool is not None:
            raise ValueError("a sampling request has no tool")
        if self.attribution == "unattributed" and self.consumer.kind is not ConsumerKind.HUMAN:
            raise ValueError("an unattributed request asks the human Consumer")
        return self

    @property
    def fqn(self) -> Fqn | None:
        return self.tool.fqn if self.tool is not None else None


class ConsentAnswer(BaseModel):
    """The Consumer's answer to a prompt (API §4.7 mapping table)."""

    model_config = ConfigDict(frozen=True)

    decision: ConsentDecision
    target_kind: TargetKind = "tool"
    deny_lifetime: DenyLifetime = "once"
    edited_sampling: CreateMessageRequestParams | None = None


ArgumentPreviewHook: TypeAlias = Callable[
    [ToolEntry, Mapping[str, Any]], ArgumentPreview | Awaitable[ArgumentPreview]
]
"""Library-supplied preview renderer (REQ-CNS-011); sync or async."""
