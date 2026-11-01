"""Invoker pipeline step 4a — re-resolve the tool after consent (F8-09; API §4.8, Art. 3).

A real ``ConsentPolicy`` and ``AuditLog`` (SQLite under ``tmp_path``) with a fake catalog, a
fake ``ready`` supervisor whose session records ``call_tool``, and a prompter that changes
(or removes) the catalog entry while it "waits" before answering ``allow_once``. A changed
``definition_hash`` ends ``denied`` / ``definition_changed`` and a vanished entry
``not_found`` — nothing is sent, no second prompt, exactly one audit record carrying the
step-4 ``decision_source``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mcp_types as types
import pytest

from reflex_mcphost.core.audit import AuditLog, AuditQuery, AuditRecord
from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.inflight import InFlightRegistry
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentTarget,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import UnknownEntryError
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.invoker import InvocationOutcome, Invoker
from reflex_mcphost.core.types import Consumer, Fqn

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
BOT = Consumer.agent("bot")
FQN = "srv::mutable"
OLD_HASH = "sha256:" + "0" * 64
NEW_HASH = "sha256:" + "1" * 64


def _entry(definition_hash: str = OLD_HASH, description: str = "v1") -> ToolEntry:
    return ToolEntry(
        fqn=Fqn(server_id="srv", name="mutable"),
        server_id="srv",
        name="mutable",
        description=description,
        input_schema={"type": "object"},
        definition_hash=definition_hash,
    )


class FakeCatalog:
    def __init__(self) -> None:
        self.entries: dict[str, ToolEntry] = {FQN: _entry()}

    def get_tool(self, fqn: Fqn | str) -> ToolEntry:
        try:
            return self.entries[str(fqn)]
        except KeyError:
            raise UnknownEntryError(str(fqn)) from None


@dataclass
class FakeSession:
    calls: list[str] = field(default_factory=list)

    async def call_tool(self, name: str, arguments: Any, **_kw: Any) -> types.CallToolResult:
        self.calls.append(name)
        return types.CallToolResult(content=[types.TextContent(type="text", text="sent")])


@dataclass
class FakeClient:
    session: FakeSession = field(default_factory=FakeSession)


class FakeSupervisor:
    def __init__(self) -> None:
        self.status = ServerStatus(
            server_id="srv", state=ServerState.READY, since=datetime.now(UTC), era="modern"
        )
        self.definition = ServerDefinition(id="srv", transport=StdioTransport(command="x"))
        self.inflight = InFlightRegistry("srv")
        self.client = FakeClient()

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[FakeClient]:
        yield self.client


@dataclass
class ChangingPrompter:
    """Runs ``while_waiting`` (the rug pull), then answers ``decision``."""

    while_waiting: Callable[[], None] = lambda: None
    decision: ConsentDecision = ConsentDecision.ALLOW_ONCE
    supports_elicitation: bool = False
    asked: list[ConsentRequest] = field(default_factory=list)

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        self.while_waiting()
        return ConsentAnswer(decision=self.decision)

    async def review_sampling_response(self, review: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def elicit(self, request: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def confirm_trust(self, request: Any) -> bool:  # pragma: no cover
        return False


@dataclass
class Rig:
    invoker: Invoker
    audit: AuditLog
    consent: ConsentPolicy
    catalog: FakeCatalog
    prompter: ChangingPrompter
    supervisor: FakeSupervisor

    async def records(self) -> list[AuditRecord]:
        return list((await self.audit.query(AuditQuery(limit=500))).records)

    @property
    def sent(self) -> list[str]:
        return self.supervisor.client.session.calls


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


@pytest.fixture
async def rig(db: Database) -> Rig:
    events = EventBus()
    catalog = FakeCatalog()
    prompter = ChangingPrompter()
    consent = ConsentPolicy(events=events, prompter=lambda: prompter, tool_lookup=catalog.get_tool)
    await consent.attach(ConsentStore(db))
    audit = AuditLog(db, InstallKey(b"k" * 32))
    supervisor = FakeSupervisor()
    invoker = Invoker(
        catalog=catalog,  # type: ignore[arg-type]
        supervisor_of=lambda _sid: supervisor,
        consent=consent,
        audit=audit,
        events=events,
    )
    return Rig(invoker, audit, consent, catalog, prompter, supervisor)


async def test_changed_definition_after_consent_is_denied(rig: Rig) -> None:
    """REQ-INV-016, REQ-INV-009, Art. 3 (API §4.8 step 4a): the tool's definition hash
    changes while the prompt is open → ``denied`` / ``definition_changed`` with the evaluated
    and current hashes; ``call_tool`` is never called, there is no second prompt, and exactly
    one finished audit record carries ``definition_changed`` and the step-4
    ``decision_source``."""

    def rug_pull() -> None:
        rig.catalog.entries[FQN] = _entry(NEW_HASH, description="v2: exfiltrate")

    rig.prompter.while_waiting = rug_pull
    result = await rig.invoker.call(FQN, {}, consumer=BOT)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "definition_changed"
    assert result.error.details == {"evaluated": OLD_HASH, "current": NEW_HASH}
    assert result.consent is not None
    assert result.consent.allowed
    assert len(rig.prompter.asked) == 1
    assert rig.sent == []
    (record,) = await rig.records()
    assert record.invocation_id == result.id
    assert record.outcome is Out.denied
    assert record.error_code == "definition_changed"
    assert record.decision_source == result.consent.source == "prompt"
    assert record.duration_ms is not None


async def test_removed_tool_after_consent_is_not_found(rig: Rig) -> None:
    """REQ-INV-016, REQ-INV-009 (API §4.8 step 4a): the tool disappears while the prompt is
    open → ``not_found``; nothing is sent; one audit record."""
    rig.prompter.while_waiting = lambda: rig.catalog.entries.clear()
    result = await rig.invoker.call(FQN, {}, consumer=BOT)
    assert result.outcome is Out.not_found
    assert result.error is not None
    assert result.error.code == "not_found"
    assert rig.sent == []
    (record,) = await rig.records()
    assert (record.outcome, record.error_code) == (Out.not_found, "not_found")
    assert record.decision_source == "prompt"


async def test_unchanged_definition_is_called(rig: Rig) -> None:
    """REQ-INV-016, REQ-INV-001: an entry re-read after consent with the same hash (even a
    new object) → the call is sent normally."""
    rig.prompter.while_waiting = lambda: rig.catalog.entries.update({FQN: _entry()})
    result = await rig.invoker.call(FQN, {}, consumer=BOT)
    assert result.outcome is Out.success, result.error
    assert rig.sent == ["mutable"]
    (record,) = await rig.records()
    assert record.outcome is Out.success


async def test_stored_allow_rule_behaves_as_before(rig: Rig) -> None:
    """REQ-INV-016, REQ-CNS-001: with a stored ``allow`` rule (no prompt) the call goes
    through step 4a unchanged and is sent; the record keeps the rule id."""
    rule = await rig.consent.grant(
        effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server("srv")
    )
    result = await rig.invoker.call(FQN, {}, consumer=BOT)
    assert result.outcome is Out.success, result.error
    assert rig.prompter.asked == []
    assert rig.sent == ["mutable"]
    (record,) = await rig.records()
    assert (record.decision_source, record.rule_id) == ("rule", rule.id)


async def test_definition_change_with_rule_keeps_rule_id(rig: Rig) -> None:
    """REQ-INV-016, REQ-CNS-007: a change between a rule-based evaluation and the send is
    denied with the evaluated rule id kept in the single record."""
    rule = await rig.consent.grant(
        effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.server("srv")
    )
    real = rig.consent.evaluate

    async def evaluate_then_change(request: Any) -> Any:
        outcome = await real(request)
        rig.catalog.entries[FQN] = _entry(NEW_HASH)
        return outcome

    rig.consent.evaluate = evaluate_then_change  # type: ignore[method-assign]
    result = await rig.invoker.call(FQN, {}, consumer=BOT)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "definition_changed"
    assert rig.sent == []
    (record,) = await rig.records()
    assert (record.decision_source, record.rule_id) == ("rule", rule.id)
    assert record.error_code == "definition_changed"
