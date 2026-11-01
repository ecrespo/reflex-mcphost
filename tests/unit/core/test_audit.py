"""AuditLog over ``audit_record`` (F3-06, API §4.9, DM §2.3, Q-A1…Q-A5, DD-007)."""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite as sqlite_dialect

from reflex_mcphost.core.audit import (
    AuditLog,
    AuditPage,
    AuditQuery,
    AuditRecordNotFoundError,
    AuditUnavailableError,
    build_query,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, Fqn, new_invocation_id

pytestmark = pytest.mark.anyio

KEY_A = InstallKey(b"a" * 32)
KEY_B = InstallKey(b"b" * 32)
ECHO = Fqn.parse("srv::echo")
ADD = Fqn.parse("other::add")
AGENT = Consumer.agent("bot")
HUMAN = Consumer.human()
ARG_SENTINEL = "raw-argument-value-0123456789"


class Clock:
    def __init__(self, start: int = 1_700_000_000_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, us: int = 1_000) -> None:
        self.now += us


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data")
    yield database
    database.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def audit(db: Database, clock: Clock) -> AuditLog:
    return AuditLog(db, KEY_A, clock=clock)


def _raw(db: Database) -> sqlite3.Connection:
    return sqlite3.connect(db.path, isolation_level=None)


async def _call(
    audit: AuditLog,
    clock: Clock,
    *,
    fqn: Fqn = ECHO,
    consumer: Consumer = AGENT,
    outcome: InvocationOutcome = InvocationOutcome.success,
    arguments: dict[str, Any] | None = None,
) -> str:
    inv = await audit.begin(
        consumer=consumer,
        fqn=fqn,
        arguments=arguments if arguments is not None else {"x": 1},
        decision_source="rule",
        rule_id="rule_01J00000000000000000000000",
    )
    clock.advance(500)
    assert await audit.finish(inv, outcome=outcome, duration_ms=5)
    clock.advance(500)
    return inv


# ---------------------------------------------------------------- begin / finish / record


async def test_begin_finish_writes_one_complete_record(audit: AuditLog, clock: Clock) -> None:
    """REQ-INV-009: one record per attempt with timestamp, Consumer, FQN, argument hash,
    consent decision and source, duration, outcome and input rounds."""
    start = clock.now
    inv = await audit.begin(
        consumer=AGENT,
        fqn=ECHO,
        arguments={"text": "hi"},
        decision_source="prompt",
        rule_id="rule_01J00000000000000000000000",
    )
    assert inv.startswith("inv_")
    with pytest.raises(AuditRecordNotFoundError):
        await audit.get(inv)  # in_progress rows are not visible (DM §2.3)
    clock.advance(42_000)
    assert await audit.finish(
        inv, outcome=InvocationOutcome.tool_error, duration_ms=42, input_rounds=3, error_code="x"
    )
    record = await audit.get(inv)
    assert record.invocation_id == inv
    assert record.operation is OperationKind.TOOL_CALL
    assert record.timestamp == datetime.fromtimestamp(start / 1_000_000, tz=UTC)
    assert record.timestamp.tzinfo is not None
    assert record.consumer == AGENT
    assert record.server_id == "srv"
    assert record.fqn == ECHO
    assert record.args_hash == KEY_A.keyed_hash({"text": "hi"})
    assert record.decision_source == "prompt"
    assert record.rule_id == "rule_01J00000000000000000000000"
    assert record.duration_ms == 42
    assert record.outcome is InvocationOutcome.tool_error
    assert record.error_code == "x"
    assert record.input_rounds == 3
    assert record.attribution == "exact"
    page = await audit.query()
    assert [r.invocation_id for r in page.records] == [inv]


async def test_finish_twice_is_refused_and_logged(
    audit: AuditLog, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-INV-009: exactly one record per attempt; a second finish does not rewrite it."""
    inv = await _call(audit, clock)
    with caplog.at_level(logging.ERROR, logger="reflex_mcphost"):
        assert not await audit.finish(inv, outcome=InvocationOutcome.timeout, duration_ms=1)
    assert (await audit.get(inv)).outcome is InvocationOutcome.success
    assert "not in progress" in caplog.text
    assert not await audit.finish(new_invocation_id(), outcome="success", duration_ms=1)


async def test_record_single_shot_for_early_failures(audit: AuditLog) -> None:
    """REQ-INV-009: denied and invalid attempts get a single finished record."""
    denied = await audit.record(
        consumer=HUMAN,
        fqn=ECHO,
        arguments={"a": 1},
        decision_source="default_deny",
        outcome=InvocationOutcome.denied,
        duration_ms=3,
    )
    invalid = await audit.record(
        consumer=HUMAN,
        fqn=ECHO,
        arguments={"bad": math.nan},  # not canonicalizable → empty hash allowed
        outcome=InvocationOutcome.invalid_arguments,
        duration_ms=0,
    )
    missing = await audit.record(
        consumer=HUMAN, fqn=ADD, arguments=None, outcome="not_found", duration_ms=0
    )
    r_denied = await audit.get(denied)
    assert r_denied.decision_source == "default_deny"
    assert r_denied.args_hash == KEY_A.keyed_hash({"a": 1})
    r_invalid = await audit.get(invalid)
    assert r_invalid.args_hash is None
    assert r_invalid.decision_source == "not_evaluated"
    assert r_invalid.rule_id is None
    assert r_invalid.error_code is None
    r_missing = await audit.get(missing)
    assert r_missing.args_hash == KEY_A.keyed_hash({})  # None arguments hash as {}
    assert r_missing.outcome is InvocationOutcome.not_found
    assert len((await audit.query()).records) == 3


async def test_record_sampling_has_no_fqn_and_no_hash(audit: AuditLog) -> None:
    """REQ-INV-009: sampling attempts are audited with server, no FQN and no hash."""
    inv = await audit.record(
        operation=OperationKind.SAMPLING,
        consumer=AGENT,
        server_id="srv",
        decision_source="prompt",
        outcome=InvocationOutcome.denied,
        error_code="sampling_denied",
        duration_ms=7,
        attribution="unattributed",
    )
    record = await audit.get(inv)
    assert record.operation is OperationKind.SAMPLING
    assert record.fqn is None
    assert record.args_hash is None
    assert record.server_id == "srv"
    assert record.attribution == "unattributed"
    sampling = await audit.query(AuditQuery(operation=OperationKind.SAMPLING))
    assert [r.invocation_id for r in sampling.records] == [inv]


async def test_explicit_invocation_id_and_start_time(audit: AuditLog) -> None:
    """REQ-INV-009: the Invoker's handle id is the record id; timestamp is the start."""
    inv = new_invocation_id()
    assert (
        await audit.begin(
            invocation_id=inv,
            consumer=AGENT,
            fqn=ECHO,
            arguments={},
            decision_source="rule",
            started_at_us=1_000_000,
        )
        == inv
    )
    assert await audit.finish(inv, outcome="success", duration_ms=0)
    assert (await audit.get(inv)).timestamp == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


async def test_input_validation_is_a_programming_error(audit: AuditLog) -> None:
    with pytest.raises(ValueError, match="input_rounds"):
        await audit.record(
            consumer=AGENT, fqn=ECHO, arguments={}, outcome="success", duration_ms=0,
            input_rounds=9,
        )  # fmt: skip
    with pytest.raises(ValueError, match="duration_ms"):
        await audit.record(
            consumer=AGENT, fqn=ECHO, arguments={}, outcome="success", duration_ms=-1
        )
    with pytest.raises(ValueError, match="sampling"):
        await audit.record(
            operation="sampling", consumer=AGENT, fqn=ECHO, outcome="denied", duration_ms=0
        )
    with pytest.raises(ValueError, match="server_id"):
        await audit.record(operation="sampling", consumer=AGENT, outcome="denied", duration_ms=0)
    with pytest.raises(ValueError, match="fqn"):
        await audit.begin(consumer=AGENT, arguments={}, decision_source="rule")
    with pytest.raises(ValueError, match="server_id"):
        await audit.begin(consumer=AGENT, fqn=ECHO, server_id="x", decision_source="rule")
    with pytest.raises(ValueError, match="decision_source"):
        await audit.begin(consumer=AGENT, fqn=ECHO, arguments={}, decision_source="bogus")
    with pytest.raises(ValueError, match="attribution"):
        await audit.begin(
            consumer=AGENT, fqn=ECHO, arguments={}, decision_source="rule", attribution="x"
        )
    inv = await audit.begin(consumer=AGENT, fqn=ECHO, arguments={}, decision_source="rule")
    with pytest.raises(ValueError, match="input_rounds"):
        await audit.finish(inv, outcome="success", duration_ms=0, input_rounds=-1)
    with pytest.raises(ValueError, match="nope"):
        await audit.finish(inv, outcome="nope", duration_ms=0)


# ---------------------------------------------------------------- hashing (DD-007)


async def test_equal_arguments_equal_hash_different_installs_differ(
    db: Database, clock: Clock
) -> None:
    """REQ-INV-010: keyed hash over canonical JSON — equal arguments correlate locally,
    key order and integral floats do not matter, another install's key differs."""
    a = AuditLog(db, KEY_A, clock=clock)
    b = AuditLog(db, KEY_B, clock=clock)
    first = await _call(a, clock, arguments={"n": 1, "s": "x"})
    second = await _call(a, clock, arguments={"s": "x", "n": 1.0})
    other = await _call(b, clock, arguments={"n": 1, "s": "x"})
    different = await _call(a, clock, arguments={"n": 2, "s": "x"})
    h1, h2, h3, h4 = [(await a.get(i)).args_hash for i in (first, second, other, different)]
    assert h1 == h2
    assert h1 is not None
    assert h1.startswith("hmac-sha256:")
    assert len(h1) == len("hmac-sha256:") + 64
    assert h3 != h1
    assert h4 != h1


async def test_raw_arguments_never_reach_the_database(db: Database, audit: AuditLog) -> None:
    """REQ-INV-010: only the keyed hash is stored, never argument values (Art. 5)."""
    clock = Clock()
    log = AuditLog(db, KEY_A, clock=clock)
    await _call(log, clock, arguments={"secret": ARG_SENTINEL})
    await log.record(
        consumer=AGENT,
        fqn=ECHO,
        arguments={"secret": ARG_SENTINEL},
        outcome="denied",
        decision_source="prompt",
        duration_ms=1,
    )
    conn = _raw(db)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert ARG_SENTINEL not in dump
    for path in db.path.parent.iterdir():
        assert ARG_SENTINEL.encode("utf-8") not in path.read_bytes(), path.name


# ---------------------------------------------------------------- failures (REQ-INV-012)


async def test_begin_and_record_raise_audit_unavailable_on_db_failure(
    db: Database, audit: AuditLog, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-INV-012: if the audit record cannot be written, begin/record raise
    AuditUnavailableError (the Invoker maps it to audit_unavailable) and log at ERROR."""

    async def broken(*_args: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "run", broken)
    with caplog.at_level(logging.ERROR, logger="reflex_mcphost"):
        with pytest.raises(AuditUnavailableError) as exc_info:
            await audit.begin(consumer=AGENT, fqn=ECHO, arguments={}, decision_source="rule")
        with pytest.raises(AuditUnavailableError):
            await audit.record(
                consumer=AGENT, fqn=ECHO, arguments={}, outcome="denied", duration_ms=0
            )
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert "audit record could not be written" in caplog.text
    assert all(r.levelno >= logging.ERROR for r in caplog.records)


async def test_begin_with_uncanonicalizable_arguments_fails_closed(audit: AuditLog) -> None:
    """REQ-INV-012: a started record needs an argument hash; without one the call must
    not be sent (fail closed)."""
    with pytest.raises(AuditUnavailableError):
        await audit.begin(
            consumer=AGENT, fqn=ECHO, arguments={"x": math.inf}, decision_source="rule"
        )
    with pytest.raises(AuditUnavailableError):
        await audit.record(
            consumer=AGENT, fqn=ECHO, arguments={"x": object()}, outcome="denied", duration_ms=0
        )
    assert (await audit.query()).records == ()


async def test_constraint_violation_is_audit_unavailable(audit: AuditLog) -> None:
    """REQ-INV-012: a row the DB rejects is an unwritten record."""
    with pytest.raises(AuditUnavailableError):
        await audit.begin(
            invocation_id="inv_short",
            consumer=AGENT,
            fqn=ECHO,
            arguments={},
            decision_source="rule",
        )


async def test_finish_failure_is_logged_not_raised(
    db: Database, audit: AuditLog, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-INV-009: a step-9 failure is logged; the result is still returned."""
    inv = await audit.begin(consumer=AGENT, fqn=ECHO, arguments={}, decision_source="rule")

    async def broken(*_args: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "run", broken)
    with caplog.at_level(logging.ERROR, logger="reflex_mcphost"):
        assert not await audit.finish(inv, outcome="success", duration_ms=1)
    assert "could not finish audit record" in caplog.text


# ---------------------------------------------------------------- write-once (DM §2.3)


async def test_delete_is_blocked_by_trigger(db: Database, audit: AuditLog, clock: Clock) -> None:
    """REQ-INV-009: audit rows cannot be deleted (trigger audit_record_no_delete)."""
    inv = await _call(audit, clock)
    conn = _raw(db)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            conn.execute("DELETE FROM audit_record WHERE invocation_id = ?", (inv,))
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "UPDATE audit_record SET outcome = 'denied' WHERE invocation_id = ?", (inv,)
            )
    finally:
        conn.close()
    assert (await audit.get(inv)).outcome is InvocationOutcome.success


async def test_mark_interrupted_repairs_rows_left_in_progress(db: Database, clock: Clock) -> None:
    """REQ-INV-009: rows left in progress by a previous process become finished
    connection_lost/host_interrupted at start (Q-A5)."""
    previous = AuditLog(db, KEY_A, clock=clock)
    left = await previous.begin(consumer=AGENT, fqn=ECHO, arguments={}, decision_source="rule")
    done = await _call(previous, clock)
    clock.advance(2_500_000)
    fresh = AuditLog(db, KEY_A, clock=clock)
    assert await fresh.mark_interrupted() == 1
    record = await fresh.get(left)
    assert record.outcome is InvocationOutcome.connection_lost
    assert record.error_code == "host_interrupted"
    assert record.duration_ms is not None
    assert record.duration_ms >= 2_500
    assert (await fresh.get(done)).outcome is InvocationOutcome.success
    assert await fresh.mark_interrupted() == 0


async def test_mark_interrupted_db_failure_raises(
    db: Database, audit: AuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "run", broken)
    with pytest.raises(AuditUnavailableError):
        await audit.mark_interrupted()


# ---------------------------------------------------------------- query (Q-A3)


async def test_query_filters_combine_newest_first(audit: AuditLog, clock: Clock) -> None:
    """REQ-INV-011: filters by time range, Server, FQN, Consumer and outcome combine;
    results are newest first."""
    t0 = clock.now
    r1 = await _call(audit, clock, fqn=ECHO, consumer=AGENT)
    r2 = await _call(audit, clock, fqn=ADD, consumer=AGENT, outcome=InvocationOutcome.denied)
    t_mid = clock.now
    r3 = await _call(audit, clock, fqn=ECHO, consumer=HUMAN, outcome=InvocationOutcome.denied)
    r4 = await _call(audit, clock, fqn=ECHO, consumer=AGENT, outcome=InvocationOutcome.denied)
    t_end = clock.now

    async def ids(**kw: Any) -> list[str]:
        return [r.invocation_id for r in (await audit.query(AuditQuery(**kw))).records]

    def ts(us: int) -> datetime:
        return datetime.fromtimestamp(us / 1_000_000, tz=UTC)

    assert await ids() == [r4, r3, r2, r1]
    assert await ids(server_id="srv") == [r4, r3, r1]
    assert await ids(fqn=ADD) == [r2]
    assert await ids(consumer=HUMAN) == [r3]
    assert await ids(outcome=InvocationOutcome.denied) == [r4, r3, r2]
    assert await ids(since=ts(t_mid)) == [r4, r3]
    assert await ids(until=ts(t_mid)) == [r2, r1]
    assert await ids(since=ts(t0), until=ts(t_end)) == [r4, r3, r2, r1]
    assert await ids(server_id="srv", consumer=AGENT, outcome="denied") == [r4]
    assert await ids(fqn=ECHO, since=ts(t_mid), consumer=AGENT) == [r4]
    assert await ids(operation=OperationKind.TOOL_CALL, fqn=ADD) == [r2]
    assert await ids(server_id="srv", outcome="timeout") == []


async def test_query_keyset_pagination_with_equal_timestamps(audit: AuditLog, clock: Clock) -> None:
    """REQ-INV-011: pages are newest first and stable, ties broken by invocation id."""
    created = []
    for _ in range(5):
        created.append(
            await audit.record(
                consumer=AGENT, fqn=ECHO, arguments={}, outcome="success", duration_ms=0
            )
        )  # same clock value: all share started_at_us
    expected = sorted(created, reverse=True)
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = await audit.query(AuditQuery(limit=2, cursor=cursor))
        assert isinstance(page, AuditPage)
        seen.extend(r.invocation_id for r in page.records)
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == expected
    assert pages == 3
    exact = await audit.query(AuditQuery(limit=5))
    assert exact.next_cursor is None


async def test_query_validation(audit: AuditLog) -> None:
    with pytest.raises(ValidationError):
        AuditQuery(limit=0)
    with pytest.raises(ValidationError):
        AuditQuery(limit=501)
    for bad in ("!!!", "bm8tY29sb24", "YWJjOmludl94"):  # garbage, "no-colon", "abc:inv_x"
        with pytest.raises(ValueError, match="cursor"):
            await audit.query(AuditQuery(cursor=bad))


async def test_get_unknown_raises(audit: AuditLog) -> None:
    with pytest.raises(AuditRecordNotFoundError):
        await audit.get(new_invocation_id())


@pytest.mark.parametrize(
    ("kwargs", "index"),
    [
        ({}, "ix_audit_time"),
        ({"outcome": "denied"}, "ix_audit_time"),
        ({"operation": "sampling"}, "ix_audit_time"),
        ({"cursor_set": True}, "ix_audit_time"),
        ({"server_id": "srv"}, "ix_audit_server_time"),
        ({"server_id": "srv", "outcome": "denied"}, "ix_audit_server_time"),
        ({"fqn": ECHO}, "ix_audit_fqn_time"),
        ({"consumer": AGENT}, "ix_audit_consumer_time"),
        ({"consumer": AGENT, "cursor_set": True}, "ix_audit_consumer_time"),
    ],
)
async def test_query_plan_uses_expected_index(
    db: Database, kwargs: dict[str, Any], index: str
) -> None:
    """REQ-INV-011: Q-A3 is built from the set filters so the planner picks an index."""
    kw = dict(kwargs)
    cursor_set = kw.pop("cursor_set", False)
    query = AuditQuery(**kw)
    stmt = build_query(query, (1_700_000_000_000_000, new_invocation_id()) if cursor_set else None)
    sql = str(
        stmt.compile(dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True})
    )
    conn = _raw(db)
    try:
        plan = " ".join(row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}"))
    finally:
        conn.close()
    assert index in plan, plan
    assert "TEMP B-TREE" not in plan, plan  # no sort step: the index gives the order


async def test_default_clock_and_naive_datetimes_are_utc(db: Database) -> None:
    """REQ-INV-011: timestamps are UTC; a naive time-range bound is read as UTC."""
    audit = AuditLog(db, KEY_A)
    before = datetime.now(UTC)
    inv = await audit.record(
        consumer=AGENT, fqn=ECHO, arguments={}, outcome="success", duration_ms=0
    )
    record = await audit.get(inv)
    assert before <= record.timestamp <= datetime.now(UTC)
    naive_since = before.replace(tzinfo=None)
    assert [
        r.invocation_id for r in (await audit.query(AuditQuery(since=naive_since))).records
    ] == [inv]
