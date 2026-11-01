"""Fakes for Toolset unit tests: a catalog, Server statuses and an Invoker (F5-01/F5-02)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mcp import types

from reflex_mcphost.core.catalog import ToolEntry, Visibility
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.invoker import InvocationError, InvocationResult
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.toolset import Toolset
from reflex_mcphost.core.types import Consumer, Fqn

HELPER = Consumer.agent("helper")
OBJ: dict[str, Any] = {"type": "object", "properties": {"text": {"type": "string"}}}


def tool(
    fqn: str,
    *,
    description: str | None = "Echo the text.",
    title: str | None = None,
    stale: bool = False,
    visibility: tuple[Visibility, ...] = ("model", "app"),
    schema: dict[str, Any] | None = None,
) -> ToolEntry:
    key = Fqn.parse(fqn)
    return ToolEntry(
        fqn=key,
        server_id=key.server_id,
        name=key.name,
        title=title,
        description=description,
        input_schema=schema if schema is not None else {**OBJ, "properties": {**OBJ["properties"]}},
        definition_hash="sha256:x",
        visibility=visibility,
        stale=stale,
    )


def result(
    outcome: InvocationOutcome,
    *,
    text: str = "",
    structured: dict[str, Any] | None = None,
    error: str | None = None,
    fqn: str = "srv::echo",
) -> InvocationResult:
    content: tuple[types.ContentBlock, ...] = (
        (types.TextContent(type="text", text=text),) if text else ()
    )
    return InvocationResult(
        id="inv_01JTESTTESTTESTTESTTESTTEST",
        fqn=Fqn.parse(fqn),
        consumer=HELPER,
        outcome=outcome,
        content=content,
        structured_content=structured,
        error=InvocationError(code=outcome.value, message=error) if error is not None else None,
        started_at=datetime(2026, 10, 31, tzinfo=UTC),
        duration_ms=1,
    )


@dataclass
class FakeCatalog:
    entries: list[ToolEntry] = field(default_factory=list)

    def tools(self, server_id: str | None = None) -> list[ToolEntry]:
        return [e for e in self.entries if server_id in (None, e.server_id)]


@dataclass
class FakeStatuses:
    states: dict[str, ServerState] = field(default_factory=dict)

    def __call__(self, server_id: str) -> ServerStatus:
        return ServerStatus(
            server_id=server_id,
            state=self.states.get(server_id, ServerState.READY),
            since=datetime(2026, 10, 31, tzinfo=UTC),
        )


@dataclass
class FakeInvoker:
    reply: InvocationResult | None = None
    calls: list[tuple[Fqn | str, Mapping[str, Any] | None, Consumer]] = field(default_factory=list)

    async def call(
        self, fqn: Fqn | str, arguments: Mapping[str, Any] | None, *, consumer: Consumer
    ) -> InvocationResult:
        self.calls.append((fqn, arguments, consumer))
        reply = self.reply or result(InvocationOutcome.success, text="ok", fqn=str(fqn))
        return reply


@dataclass
class Rig:
    catalog: FakeCatalog = field(default_factory=FakeCatalog)
    statuses: FakeStatuses = field(default_factory=FakeStatuses)
    invoker: FakeInvoker = field(default_factory=FakeInvoker)

    def toolset(
        self,
        consumer: Consumer = HELPER,
        *,
        include: tuple[str, ...] | list[str] = ("*",),
        exclude: tuple[str, ...] | list[str] = (),
    ) -> Toolset:
        return Toolset(
            catalog=self.catalog,
            status_of=self.statuses,
            invoker_of=lambda: self.invoker,
            consumer=consumer,
            include=include,
            exclude=exclude,
        )
