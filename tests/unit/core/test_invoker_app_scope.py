"""Invoker pipeline step 1a — the App scope (F7-08; API §4.8, delta D1, Art. 3).

A real ``ConsentPolicy`` and ``AuditLog`` (SQLite under ``tmp_path``) with a fake catalog,
a fake ``ready`` supervisor and a recording prompter that denies: a call that passes the
App scope reaches consent (the prompt is recorded) and ends ``denied`` / ``consent_denied``
before anything is sent; a call that fails it ends ``denied`` / ``app_scope`` without a
prompt. Either way exactly one audit record is written.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reflex_mcphost.core.audit import AuditLog, AuditQuery, AuditRecord
from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.config.models import ServerDefinition, StdioTransport
from reflex_mcphost.core.connection.inflight import InFlightRegistry
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.consent.models import ConsentAnswer, ConsentDecision, ConsentRequest
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import UnknownEntryError
from reflex_mcphost.core.events import EventBus
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.invoker import InvocationOutcome, InvocationResult, Invoker
from reflex_mcphost.core.types import Consumer, Fqn

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
APP_A = Consumer.app("a")
TOOLS = {
    "t": ("model", "app"),
    "both": ("model", "app"),
    "app_only": ("app",),
    "model_only": ("model",),
}


def _entry(server_id: str, name: str, visibility: tuple[str, ...]) -> ToolEntry:
    return ToolEntry(
        fqn=Fqn(server_id=server_id, name=name),
        server_id=server_id,
        name=name,
        input_schema={"type": "object"},
        definition_hash=f"sha256:{server_id}-{name}",
        visibility=visibility,  # type: ignore[arg-type]
    )


class FakeCatalog:
    def __init__(self) -> None:
        self.entries = {
            f"{sid}::{name}": _entry(sid, name, vis)
            for sid in ("a", "b")
            for name, vis in TOOLS.items()
        }

    def get_tool(self, fqn: Fqn | str) -> ToolEntry:
        try:
            return self.entries[str(fqn)]
        except KeyError:
            raise UnknownEntryError(str(fqn)) from None


class FakeSupervisor:
    def __init__(self, server_id: str) -> None:
        self.status = ServerStatus(
            server_id=server_id, state=ServerState.READY, since=datetime.now(UTC), era="modern"
        )
        self.definition = ServerDefinition(id=server_id, transport=StdioTransport(command="x"))
        self.inflight = InFlightRegistry(server_id)
        self.leased = 0

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Any]:  # pragma: no cover - never reached
        self.leased += 1
        raise AssertionError("the Server must not be called")
        yield


@dataclass
class RecordingPrompter:
    supports_elicitation: bool = False
    asked: list[ConsentRequest] = field(default_factory=list)

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        return ConsentAnswer(decision=ConsentDecision.DENY)

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
    prompter: RecordingPrompter
    supervisors: dict[str, FakeSupervisor]

    async def records(self) -> list[AuditRecord]:
        return list((await self.audit.query(AuditQuery(limit=500))).records)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


@pytest.fixture
async def rig(db: Database) -> Rig:
    events = EventBus()
    catalog = FakeCatalog()
    prompter = RecordingPrompter()
    consent = ConsentPolicy(events=events, prompter=lambda: prompter, tool_lookup=catalog.get_tool)
    await consent.attach(ConsentStore(db))
    audit = AuditLog(db, InstallKey(b"k" * 32))
    supervisors = {sid: FakeSupervisor(sid) for sid in ("a", "b")}
    invoker = Invoker(
        catalog=catalog,  # type: ignore[arg-type]
        supervisor_of=supervisors.__getitem__,
        consent=consent,
        audit=audit,
        events=events,
    )
    return Rig(invoker, audit, prompter, supervisors)


def _assert_app_scope(result: InvocationResult, reason: str) -> None:
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "app_scope"
    assert result.error.details == {"reason": reason}
    assert result.consent is None


@pytest.mark.parametrize(
    ("fqn", "reason"),
    [
        ("b::t", "foreign_server"),
        ("b::app_only", "foreign_server"),
        ("a::model_only", "not_app_visible"),
    ],
)
async def test_app_scope_denies_without_prompt(rig: Rig, fqn: str, reason: str) -> None:
    """REQ-APP-004, REQ-APP-008 (API §4.8 step 1a, Art. 3): an App call of another Server's
    tool or of a tool whose visibility lacks ``"app"`` ends ``denied`` / ``app_scope`` with
    the reason; the prompter is never called, the Server never reached, and exactly one
    finished audit record says ``not_evaluated`` / ``denied`` / ``app_scope``."""
    result = await rig.invoker.call(fqn, {}, consumer=APP_A)
    _assert_app_scope(result, reason)
    assert rig.prompter.asked == []
    assert all(s.leased == 0 for s in rig.supervisors.values())
    (record,) = await rig.records()
    assert record.invocation_id == result.id
    assert record.consumer == APP_A
    assert str(record.fqn) == fqn
    assert record.decision_source == "not_evaluated"
    assert record.outcome is Out.denied
    assert record.error_code == "app_scope"


async def test_app_scope_checked_before_server_state(rig: Rig) -> None:
    """REQ-APP-008 (API §4.8): step 1a precedes step 2 — an app-hidden tool of a Server
    that is not ready is still ``app_scope``, not ``server_unavailable``."""
    sup = rig.supervisors["a"]
    sup.status = sup.status.model_copy(update={"state": ServerState.RECONNECTING})
    _assert_app_scope(
        await rig.invoker.call("a::model_only", {}, consumer=APP_A), "not_app_visible"
    )


@pytest.mark.parametrize("name", ["app_only", "both", "t"])
async def test_app_visible_own_tool_follows_the_pipeline(rig: Rig, name: str) -> None:
    """REQ-APP-004 (API §4.8): an App call of its own Server's App-visible tool goes through
    consent, which asks for Consumer ``app:a`` (here denied → ``consent_denied``)."""
    result = await rig.invoker.call(f"a::{name}", {}, consumer=APP_A)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert result.error.code == "consent_denied"
    (asked,) = rig.prompter.asked
    assert asked.consumer == APP_A
    assert str(asked.consumer) == "app:a"
    (record,) = await rig.records()
    assert record.error_code == "consent_denied"
    assert record.decision_source != "not_evaluated"


@pytest.mark.parametrize("consumer", [Consumer.human(), Consumer.agent("x")])
@pytest.mark.parametrize("fqn", ["a::model_only", "b::t", "a::app_only"])
async def test_human_and_agent_unaffected(rig: Rig, consumer: Consumer, fqn: str) -> None:
    """REQ-APP-008 (API §4.8): step 1a applies only to ``app`` Consumers — ``human:ui`` and
    ``agent:x`` calls of the same tools reach consent as before."""
    result = await rig.invoker.call(fqn, {}, consumer=consumer)
    assert result.error is not None
    assert result.error.code == "consent_denied"
    assert [r.consumer for r in rig.prompter.asked] == [consumer]


async def test_unknown_tool_is_not_found_first(rig: Rig) -> None:
    """API §4.8: step 1 (resolve) runs before step 1a — an unknown FQN is ``not_found``."""
    result = await rig.invoker.call("a::nope", {}, consumer=APP_A)
    assert result.outcome is Out.not_found
    assert rig.prompter.asked == []
