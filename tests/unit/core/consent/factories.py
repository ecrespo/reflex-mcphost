"""Test factories for consent tests (F3-01…F3-05)."""

from __future__ import annotations

import hashlib
from typing import Any

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.consent.models import (
    ArgumentPreview,
    ConsentRequest,
    ConsentRule,
    ConsentTarget,
    Effect,
    Lifetime,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.types import Consumer, Fqn


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_entry(
    fqn: str = "srv::echo",
    *,
    definition_hash: str | None = None,
    input_schema: dict[str, Any] | None = None,
    description: str | None = "Echo a value",
) -> ToolEntry:
    parsed = Fqn.parse(fqn)
    return ToolEntry(
        fqn=parsed,
        server_id=parsed.server_id,
        name=parsed.name,
        description=description,
        input_schema=input_schema or {"type": "object"},
        definition_hash=definition_hash or sha(f"{fqn}:{description}"),
    )


def rule(
    effect: Effect = "allow",
    *,
    target: ConsentTarget | None = None,
    consumer: Consumer | None = None,
    operation: OperationKind = OperationKind.TOOL_CALL,
    lifetime: Lifetime = "always",
    definition_hash: str | None = None,
    suspended: bool = False,
) -> ConsentRule:
    target = target or ConsentTarget.tool("srv::echo")
    if target.kind == "tool" and definition_hash is None:
        definition_hash = tool_entry(str(target.fqn)).definition_hash
    return ConsentRule(
        effect=effect,
        operation=operation,
        target=target,
        consumer=consumer,
        lifetime=lifetime,
        definition_hash=definition_hash,
        suspended=suspended,
    )


def tool_request(
    tool: ToolEntry | None = None,
    *,
    consumer: Consumer | None = None,
    attribution: str = "exact",
) -> ConsentRequest:
    tool = tool or tool_entry()
    return ConsentRequest(
        operation=OperationKind.TOOL_CALL,
        consumer=consumer or Consumer.agent("bot"),
        server_id=tool.server_id,
        server_name="Fixture Server",
        tool=tool,
        preview=ArgumentPreview(body="{}"),
        attribution=attribution,
    )


def sampling_request(
    server_id: str = "srv",
    *,
    consumer: Consumer | None = None,
    attribution: str = "exact",
) -> ConsentRequest:
    return ConsentRequest(
        operation=OperationKind.SAMPLING,
        consumer=consumer or Consumer.human(),
        server_id=server_id,
        server_name="Fixture Server",
        preview=ArgumentPreview(format="text", body="sampling"),
        attribution=attribution,
    )
