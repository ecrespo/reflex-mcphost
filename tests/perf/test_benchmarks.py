"""Performance budgets REQ-NFR-001 and REQ-NFR-002 as defined in PRD §7 (F3-11).

Measured with ``time.perf_counter`` (no benchmark plugin). The budgets are asserted on
Linux only — the CI reference platform; elsewhere the measurement runs and the test is
skipped with the p95 in the reason (informational). Deselect with ``-m "not perf"``.

External load can only inflate latency, so a measurement over budget is repeated up to
``ATTEMPTS`` times and the best p95 is the one asserted (a loaded CI runner must not turn
a met budget into a failure; a real regression fails every attempt).
"""

from __future__ import annotations

import statistics
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from reflex_mcphost.core.audit import AuditLog
from reflex_mcphost.core.catalog import Catalog
from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.consent.models import ConsentRequest, ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import UnknownServerError
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.invoker import InvocationOutcome, Invoker
from reflex_mcphost.core.types import Consumer
from tests.fixtures.servers.modern_server import build_server
from tests.unit.core.consent.factories import rule, tool_entry

pytestmark = [pytest.mark.perf, pytest.mark.anyio]
ON_LINUX = sys.platform == "linux"
RULES = 10_000
EVALUATIONS = 1_000
CALLS = 1_000
NFR_001_P95_MS = 5.0
NFR_002_P95_MS = 20.0
ATTEMPTS = 3


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[94]


def check_budget(name: str, p95_ms: float, budget_ms: float) -> None:
    if not ON_LINUX:
        pytest.skip(f"{name} budget asserted on Linux only; here p95 = {p95_ms:.3f} ms")
    assert p95_ms < budget_ms, f"{name}: p95 {p95_ms:.3f} ms ≥ {budget_ms} ms"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


async def test_nfr_001_consent_evaluation_with_10k_rules(db: Database) -> None:
    """REQ-NFR-001: with 10,000 stored decisions loaded, ConsentPolicy.evaluate for a request
    decided by a stored rule has p95 < 5 ms over ≥ 1,000 evaluations (best of up to 3
    measurements)."""
    policy = ConsentPolicy(events=EventBus())
    store = ConsentStore(db)
    await policy.attach(store)
    consumers = [Consumer.agent(f"agent{i}") for i in range(10)] + [None]
    tools = [tool_entry(f"srv{i % 50}::tool{i}") for i in range(RULES)]
    for i, tool in enumerate(tools):  # in memory as after Q-C1 loading (DD-005)
        store.index.put(
            rule(
                "allow" if i % 7 else "deny",
                target=ConsentTarget.tool(tool.fqn),
                consumer=consumers[i % len(consumers)],
                definition_hash=tool.definition_hash,
            )
        )
    assert len(store.index) == RULES
    requests = [
        ConsentRequest(
            operation=OperationKind.TOOL_CALL,
            consumer=consumers[i % len(consumers)] or Consumer.human(),
            server_id=tools[i].server_id,
            server_name=tools[i].server_id,
            tool=tools[i],
        )
        for i in range(0, RULES, RULES // EVALUATIONS)
    ]
    best = float("inf")
    for _ in range(ATTEMPTS):
        samples: list[float] = []
        for request in requests:
            started = time.perf_counter()
            outcome = await policy.evaluate(request)
            samples.append((time.perf_counter() - started) * 1000)
            assert outcome.source == "rule"
        assert len(samples) >= EVALUATIONS
        best = min(best, p95(samples))
        if best < NFR_001_P95_MS:
            break
    check_budget("REQ-NFR-001", best, NFR_001_P95_MS)


class InProcessSupervisor:
    """A ready Server whose Client is the in-process fixture (``Client(server)``)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.definition = ServerDefinition(
            id="bench", transport=StdioTransport(command="in-process"), timeout_s=30
        )
        self.status = ServerStatus(
            server_id="bench",
            state=ServerState.READY,
            since=datetime.now(UTC),
            era="modern",
            protocol_version=client.protocol_version,
            capabilities=client.server_capabilities,
        )

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Client]:
        yield self._client


async def test_nfr_002_host_overhead_per_tools_call(db: Database) -> None:
    """REQ-NFR-002: Invoker.call wall time minus the fixture Server's handler time, for a
    no-op tool allowed by a stored rule over the in-process transport, has p95 < 20 ms over
    ≥ 1,000 calls (consent, validation, audit begin/finish and MRTR loop included; best of up to 3
    measurements)."""
    events = EventBus()
    async with Client(build_server()) as client, anyio.create_task_group() as tg:
        supervisor = InProcessSupervisor(client)

        def supervisor_of(server_id: str) -> Any:
            if server_id != "bench":
                raise UnknownServerError(server_id)
            return supervisor

        catalog = Catalog(events=events, supervisor_of=supervisor_of)
        tg.start_soon(catalog.on_ready, client, supervisor.status)
        with anyio.fail_after(10):
            while not catalog.tools("bench"):  # noqa: ASYNC110 - bounded poll (fail_after) of state that has no event
                await anyio.sleep(0.01)
        policy = ConsentPolicy(events=events, tool_lookup=catalog.get_tool)
        await policy.attach(ConsentStore(db))
        await policy.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.tool("bench::noop"),
            lifetime="session",
        )
        invoker = Invoker(
            catalog=catalog,
            supervisor_of=supervisor_of,
            consent=policy,
            audit=AuditLog(db, InstallKey(b"k" * 32)),
            events=events,
            task_group=tg,
        )
        bot = Consumer.agent("bench")
        best = float("inf")
        for _ in range(ATTEMPTS):
            samples: list[float] = []
            for _ in range(CALLS):
                started = time.perf_counter()
                result = await invoker.call("bench::noop", {}, consumer=bot)
                wall = time.perf_counter() - started
                assert result.outcome is InvocationOutcome.success, result.error
                handler = float((result.structured_content or {})["handler_s"])
                samples.append((wall - handler) * 1000)
            best = min(best, p95(samples))
            if best < NFR_002_P95_MS:
                break
        tg.cancel_scope.cancel()
    check_budget("REQ-NFR-002", best, NFR_002_P95_MS)
