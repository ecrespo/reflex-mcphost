"""Write-once audit trail over ``audit_record`` (API §4.9, DM §2.3, Q-A1…Q-A5, DD-007).

One record per invocation attempt (Constitution Art. 3, REQ-INV-009): the Invoker calls
``begin`` at pipeline step 5 (row ``in_progress``) and ``finish`` at step 9; attempts that
fail before step 5 get a single finished row through ``record``. ``begin``/``record``
failures raise ``AuditUnavailableError`` so the Invoker returns ``audit_unavailable`` and
sends nothing (REQ-INV-012); a ``finish`` failure is logged and reported as ``False``.

Arguments are never stored: only ``InstallKey.keyed_hash(arguments)`` (REQ-INV-010, Art. 5).
``None`` arguments hash as ``{}`` (the wire treats an omitted ``arguments`` as empty).
All DB work runs through ``Database.run`` (Art. 8). ``query``/``get`` see finished rows only.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field
from sqlalchemy import func, tuple_, update
from sqlmodel import col, select
from sqlmodel.sql.expression import SelectOfScalar

from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.db import Database
from reflex_mcphost.core.db_models import AuditRecordRow
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.types import Consumer, ConsumerKind, Fqn, ServerId, new_invocation_id

DecisionSource = Literal[
    "rule", "prompt", "default_deny", "error", "timeout", "no_prompter", "not_evaluated"
]
Attribution = Literal["exact", "unattributed"]
MAX_INPUT_ROUNDS = 8
INTERRUPTED_ERROR_CODE = "host_interrupted"
_HASHLESS_OUTCOMES = frozenset({InvocationOutcome.not_found, InvocationOutcome.invalid_arguments})
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_log = get_logger("core.audit")


class AuditUnavailableError(MCPHostError):
    """The audit record could not be written; the attempt must cause no effect (Art. 3)."""

    def __init__(self, invocation_id: str, reason: str) -> None:
        self.invocation_id = invocation_id
        self.reason = reason
        super().__init__(f"audit record {invocation_id} could not be written ({reason})")


class AuditRecordNotFoundError(MCPHostError, LookupError):
    def __init__(self, invocation_id: str) -> None:
        self.invocation_id = invocation_id
        super().__init__(f"no finished audit record {invocation_id!r}")


class AuditQuery(BaseModel, frozen=True):
    since: datetime | None = None
    until: datetime | None = None
    server_id: ServerId | None = None
    fqn: Fqn | None = None
    consumer: Consumer | None = None
    outcome: InvocationOutcome | None = None
    operation: OperationKind | None = None
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None


class AuditRecord(BaseModel, frozen=True):
    invocation_id: str
    operation: OperationKind
    timestamp: datetime
    consumer: Consumer
    server_id: ServerId
    fqn: Fqn | None
    args_hash: str | None
    decision_source: str
    rule_id: str | None
    duration_ms: int | None
    outcome: InvocationOutcome
    error_code: str | None
    input_rounds: int
    attribution: Attribution


class AuditPage(BaseModel, frozen=True):
    records: tuple[AuditRecord, ...]
    next_cursor: str | None


def _now_us() -> int:
    return time.time_ns() // 1000


def _to_us(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (value - _EPOCH) // timedelta(microseconds=1)


def _from_us(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)


def encode_cursor(started_at_us: int, invocation_id: str) -> str:
    raw = f"{started_at_us}:{invocation_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        ts, sep, inv = raw.partition(":")
        if not sep or not inv.startswith("inv_"):
            raise ValueError(raw)
        return int(ts), inv
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ValueError("invalid audit cursor") from None


def build_query(
    query: AuditQuery, cursor: tuple[int, str] | None
) -> SelectOfScalar[AuditRecordRow]:
    """Q-A3 with only the set filters, so SQLite can pick the matching index."""
    row = AuditRecordRow
    stmt = select(row).where(row.status == "finished")
    if query.since is not None:
        stmt = stmt.where(row.started_at_us >= _to_us(query.since))
    if query.until is not None:
        stmt = stmt.where(row.started_at_us < _to_us(query.until))
    if query.server_id is not None:
        stmt = stmt.where(row.server_id == query.server_id)
    if query.fqn is not None:
        stmt = stmt.where(row.fqn == str(query.fqn))
    if query.consumer is not None:
        stmt = stmt.where(
            row.consumer_kind == query.consumer.kind.value,
            row.consumer_name == query.consumer.name,
        )
    if query.outcome is not None:
        stmt = stmt.where(row.outcome == query.outcome.value)
    if query.operation is not None:
        stmt = stmt.where(row.operation == query.operation.value)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(col(row.started_at_us), col(row.invocation_id)) < tuple_(cursor[0], cursor[1])
        )
    return stmt.order_by(col(row.started_at_us).desc(), col(row.invocation_id).desc()).limit(
        query.limit + 1
    )


def _to_record(row: AuditRecordRow) -> AuditRecord:
    if row.outcome is None:  # unreachable: finished rows only (DM CHECK)
        raise AuditRecordNotFoundError(row.invocation_id)
    return AuditRecord(
        invocation_id=row.invocation_id,
        operation=OperationKind(row.operation),
        timestamp=_from_us(row.started_at_us),
        consumer=Consumer(kind=ConsumerKind(row.consumer_kind), name=row.consumer_name),
        server_id=row.server_id,
        fqn=Fqn.parse(row.fqn) if row.fqn else None,
        args_hash=row.args_hash or None,
        decision_source=row.decision_source,
        rule_id=row.rule_id or None,
        duration_ms=row.duration_ms,
        outcome=InvocationOutcome(row.outcome),
        error_code=row.error_code or None,
        input_rounds=row.input_rounds,
        attribution="unattributed" if row.attribution == "unattributed" else "exact",
    )


def _check_rounds(input_rounds: int) -> None:
    if not 0 <= input_rounds <= MAX_INPUT_ROUNDS:
        raise ValueError(f"input_rounds must be within 0..{MAX_INPUT_ROUNDS}, got {input_rounds}")


def _check_duration(duration_ms: int) -> None:
    if duration_ms < 0:
        raise ValueError(f"duration_ms must be >= 0, got {duration_ms}")


class AuditLog:
    """Audit repository; the only writer of ``audit_record`` (DM §2.3 write-once rule)."""

    def __init__(
        self, db: Database, key: InstallKey, *, clock: Callable[[], int] = _now_us
    ) -> None:
        self.db = db
        self._key = key
        self._clock = clock

    # ------------------------------------------------------------------ writers

    async def begin(
        self,
        *,
        consumer: Consumer,
        decision_source: DecisionSource | str,
        fqn: Fqn | None = None,
        server_id: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        operation: OperationKind | str = OperationKind.TOOL_CALL,
        rule_id: str | None = None,
        attribution: Attribution | str = "exact",
        invocation_id: str | None = None,
        started_at_us: int | None = None,
    ) -> str:
        """Q-A1: insert an ``in_progress`` row (pipeline step 5); return its id."""
        row = self._new_row(
            consumer=consumer,
            decision_source=decision_source,
            fqn=fqn,
            server_id=server_id,
            arguments=arguments,
            operation=OperationKind(operation),
            rule_id=rule_id,
            attribution=attribution,
            invocation_id=invocation_id,
            started_at_us=started_at_us,
            outcome=None,
        )
        return await self._insert(row)

    async def record(
        self,
        *,
        consumer: Consumer,
        outcome: InvocationOutcome | str,
        duration_ms: int,
        decision_source: DecisionSource | str = "not_evaluated",
        fqn: Fqn | None = None,
        server_id: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        operation: OperationKind | str = OperationKind.TOOL_CALL,
        rule_id: str | None = None,
        error_code: str | None = None,
        input_rounds: int = 0,
        attribution: Attribution | str = "exact",
        invocation_id: str | None = None,
        started_at_us: int | None = None,
    ) -> str:
        """Q-A1 single-shot: one finished row for an attempt that never reached step 5."""
        _check_duration(duration_ms)
        _check_rounds(input_rounds)
        row = self._new_row(
            consumer=consumer,
            decision_source=decision_source,
            fqn=fqn,
            server_id=server_id,
            arguments=arguments,
            operation=OperationKind(operation),
            rule_id=rule_id,
            attribution=attribution,
            invocation_id=invocation_id,
            started_at_us=started_at_us,
            outcome=InvocationOutcome(outcome),
        )
        row.status = "finished"
        row.error_code = error_code or ""
        row.duration_ms = duration_ms
        row.input_rounds = input_rounds
        row.finished_at_us = self._clock()
        return await self._insert(row)

    async def finish(
        self,
        invocation_id: str,
        *,
        outcome: InvocationOutcome | str,
        duration_ms: int,
        input_rounds: int = 0,
        error_code: str | None = None,
    ) -> bool:
        """Q-A2 (step 9). ``False`` (logged at ERROR) when the row could not be finished;
        the Invoker still returns its result (API §4.8)."""
        result = InvocationOutcome(outcome)
        _check_duration(duration_ms)
        _check_rounds(input_rounds)
        now = self._clock()
        log = ContextAdapter(_log, invocation_id=invocation_id)

        def write() -> int:
            with self.db.session() as s:
                stmt = (
                    update(AuditRecordRow)
                    .where(
                        col(AuditRecordRow.invocation_id) == invocation_id,
                        col(AuditRecordRow.status) == "in_progress",
                    )
                    .values(
                        status="finished",
                        outcome=result.value,
                        error_code=error_code or "",
                        finished_at_us=now,
                        duration_ms=duration_ms,
                        input_rounds=input_rounds,
                    )
                )
                count = s.exec(stmt).rowcount
                s.commit()
                return count

        try:
            count = await self.db.run(write)
        except Exception as exc:
            log.error("could not finish audit record (%s)", type(exc).__name__)
            return False
        if count != 1:
            log.error("could not finish audit record: not in progress")
            return False
        return True

    async def mark_interrupted(self) -> int:
        """Q-A5: rows left ``in_progress`` by a previous process → ``connection_lost`` /
        ``host_interrupted``. Called once at Host start, before any new invocation."""
        now = self._clock()

        def repair() -> int:
            with self.db.session() as s:
                row = AuditRecordRow
                stmt = (
                    update(row)
                    .where(col(row.status) == "in_progress")
                    .values(
                        status="finished",
                        outcome=InvocationOutcome.connection_lost.value,
                        error_code=INTERRUPTED_ERROR_CODE,
                        finished_at_us=now,
                        duration_ms=func.max(0, (now - col(row.started_at_us)) // 1000),
                    )
                )
                count = s.exec(stmt).rowcount
                s.commit()
                return count

        try:
            count = await self.db.run(repair)
        except Exception as exc:
            _log.error("could not repair interrupted audit records (%s)", type(exc).__name__)
            raise AuditUnavailableError("*", type(exc).__name__) from exc
        if count:
            _log.warning("marked %d interrupted invocation(s) as connection_lost", count)
        return count

    # ------------------------------------------------------------------ readers

    async def query(self, query: AuditQuery | None = None) -> AuditPage:
        """Q-A3: finished records, newest first, keyset-paginated (REQ-INV-011)."""
        q = query if query is not None else AuditQuery()
        cursor = decode_cursor(q.cursor) if q.cursor is not None else None
        stmt = build_query(q, cursor)

        def fetch() -> list[AuditRecordRow]:
            with self.db.session() as s:
                return list(s.exec(stmt).all())

        rows = await self.db.run(fetch)
        more = len(rows) > q.limit
        rows = rows[: q.limit]
        next_cursor = (
            encode_cursor(rows[-1].started_at_us, rows[-1].invocation_id) if more else None
        )
        return AuditPage(records=tuple(_to_record(r) for r in rows), next_cursor=next_cursor)

    async def get(self, invocation_id: str) -> AuditRecord:
        """Q-A4: one finished record."""

        def fetch() -> AuditRecordRow | None:
            with self.db.session() as s:
                stmt = select(AuditRecordRow).where(
                    AuditRecordRow.invocation_id == invocation_id,
                    AuditRecordRow.status == "finished",
                )
                return s.exec(stmt).first()

        row = await self.db.run(fetch)
        if row is None:
            raise AuditRecordNotFoundError(invocation_id)
        return _to_record(row)

    # ------------------------------------------------------------------ internals

    def _new_row(
        self,
        *,
        consumer: Consumer,
        decision_source: str,
        fqn: Fqn | None,
        server_id: str | None,
        arguments: Mapping[str, Any] | None,
        operation: OperationKind,
        rule_id: str | None,
        attribution: str,
        invocation_id: str | None,
        started_at_us: int | None,
        outcome: InvocationOutcome | None,
    ) -> AuditRecordRow:
        if decision_source not in get_args(DecisionSource):
            raise ValueError(f"invalid decision_source {decision_source!r}")
        if attribution not in get_args(Attribution):
            raise ValueError(f"invalid attribution {attribution!r}")
        inv = invocation_id or new_invocation_id()
        if operation is OperationKind.SAMPLING:
            if fqn is not None or arguments is not None:
                raise ValueError("sampling records carry no fqn and no arguments")
            if not server_id:
                raise ValueError("sampling records need a server_id")
            target_server, fqn_text, args_hash = server_id, "", ""
        else:
            if fqn is None:
                raise ValueError("tool_call records need an fqn")
            if server_id is not None and server_id != fqn.server_id:
                raise ValueError("server_id must match the fqn's server_id")
            target_server, fqn_text = fqn.server_id, str(fqn)
            args_hash = self._hash(inv, arguments, outcome)
        return AuditRecordRow(
            invocation_id=inv,
            status="in_progress",
            operation=operation.value,
            started_at_us=started_at_us if started_at_us is not None else self._clock(),
            consumer_kind=consumer.kind.value,
            consumer_name=consumer.name,
            server_id=target_server,
            fqn=fqn_text,
            args_hash=args_hash,
            decision_source=decision_source,
            rule_id=rule_id or "",
            outcome=outcome.value if outcome is not None else None,
            attribution=attribution,
        )

    def _hash(
        self,
        invocation_id: str,
        arguments: Mapping[str, Any] | None,
        outcome: InvocationOutcome | None,
    ) -> str:
        try:
            return self._key.keyed_hash(dict(arguments) if arguments is not None else {})
        except (TypeError, ValueError) as exc:
            if outcome in _HASHLESS_OUTCOMES:
                return ""  # DM §2.3: non-JSON input that never passed validation
            ContextAdapter(_log, invocation_id=invocation_id).error(
                "audit record could not be written: arguments not canonicalizable (%s)",
                type(exc).__name__,
            )
            raise AuditUnavailableError(invocation_id, "arguments not canonicalizable") from exc

    async def _insert(self, row: AuditRecordRow) -> str:
        invocation_id = row.invocation_id  # the row expires on commit

        def write() -> None:
            with self.db.session() as s:
                s.add(row)
                s.commit()

        try:
            await self.db.run(write)
        except Exception as exc:
            ContextAdapter(_log, invocation_id=invocation_id).error(
                "audit record could not be written (%s); attempt not executed",
                type(exc).__name__,
            )
            raise AuditUnavailableError(invocation_id, type(exc).__name__) from exc
        return invocation_id
