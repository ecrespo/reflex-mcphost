"""Shared pieces of the handler integration tests (F4-01…F4-05): a configurable prompter, a
fake sampling provider and a Host factory over the fixture Servers."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import anyio.lowlevel
import mcp_types as types
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentTarget,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.events import HostEvent
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)
from tests.conftest import MODERN
from tests.integration.test_host import TIMEOUT, Setup, _toml_server
from tests.integration.test_invoker import http_table, wait_tools, write_config

PY_MODERN = [sys.executable, str(MODERN)]


@dataclass
class HandlerPrompter:
    """Answers elicitations from ``answers`` (last one repeats), consent with ``decision``
    and sampling reviews with ``verdict``; records everything it is shown."""

    answers: Sequence[ElicitationResponse] = (ElicitationResponse(action="decline"),)
    decision: ConsentDecision = ConsentDecision.ALLOW_ONCE
    verdict: SamplingVerdict = field(default_factory=lambda: SamplingVerdict(approved=True))
    supports_elicitation: bool = True
    wait_s: float = 0.0
    elicited: list[ElicitationRequest] = field(default_factory=list)
    asked: list[ConsentRequest] = field(default_factory=list)
    reviewed: list[SamplingResponseReview] = field(default_factory=list)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        self.elicited.append(request)
        await anyio.sleep(self.wait_s)
        return self.answers[min(len(self.elicited), len(self.answers)) - 1]

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        await anyio.sleep(self.wait_s)
        return ConsentAnswer(decision=self.decision)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        self.reviewed.append(review)
        return self.verdict

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False

    def sampling_asks(self) -> list[ConsentRequest]:
        return [r for r in self.asked if r.operation is OperationKind.SAMPLING]


@dataclass
class FakeProvider:
    """A sampling provider answering ``text``."""

    text: str = "Hi Ada!"
    calls: list[tuple[types.CreateMessageRequestParams, str]] = field(default_factory=list)

    async def create_message(
        self, params: types.CreateMessageRequestParams, *, server_id: str
    ) -> types.CreateMessageResult:
        self.calls.append((params, server_id))
        return types.CreateMessageResult(
            role="assistant", content=types.TextContent(type="text", text=self.text), model="fake"
        )


def modern_stdio_table(*flags: str, **extra: Any) -> str:
    return _toml_server([*PY_MODERN, *flags], **extra)


def legacy_stdio_table(params: StdioServerParameters, **extra: Any) -> str:
    return _toml_server([params.command, *params.args], **extra)


@pytest.fixture(params=["stdio", "http"])
def modern_table(request: pytest.FixtureRequest) -> str:
    """The modern fixture Server over stdio and Streamable HTTP."""
    if request.param == "http":
        return http_table(request.getfixturevalue("modern_http"))
    return modern_stdio_table()


@pytest.fixture(params=["stdio", "http"])
def legacy_table(request: pytest.FixtureRequest) -> str:
    """The legacy fixture Server over stdio and Streamable HTTP (marked interop)."""
    request.applymarker(pytest.mark.interop)
    if request.param == "http":
        return http_table(request.getfixturevalue("legacy_http"))
    return legacy_stdio_table(request.getfixturevalue("legacy_stdio"))


@asynccontextmanager
async def handler_host(
    setup: Setup,
    servers: dict[str, str],
    *,
    prompter: Any = None,
    provider: Any = None,
    allow_tools: bool = True,
) -> AsyncIterator[Host]:
    """A started Host with every Server ready; tool calls are allowed by a stored rule."""
    host = setup.host(write_config(setup, servers), prompter=prompter, sampling_provider=provider)
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            for server_id in servers:
                await wait_tools(host, server_id)
                if allow_tools:
                    await host.consent.grant(
                        effect="allow",
                        operation=OperationKind.TOOL_CALL,
                        target=ConsentTarget.server(server_id),
                    )
            yield host


def caps_of(text: str) -> set[str]:
    """The capability names a ``client_caps`` result reports."""
    dumped = json.loads(text) or {}
    return {k for k in ("elicitation", "sampling", "roots") if k in dumped}


@asynccontextmanager
async def recording(host: Host) -> AsyncIterator[list[HostEvent]]:
    """Every Host event published while the body runs (collected by a pump task)."""
    got: list[HostEvent] = []
    async with host.events.subscribe(buffer=4096) as sub, anyio.create_task_group() as tg:

        async def pump() -> None:
            async for event in sub:
                got.append(event)

        tg.start_soon(pump)
        yield got
        for _ in range(10):  # let already-published events reach the list
            await anyio.lowlevel.checkpoint()
        tg.cancel_scope.cancel()
