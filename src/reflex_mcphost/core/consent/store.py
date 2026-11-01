"""``ConsentStore``: write-through persistence of ``always`` rules (DM §2.2, DD-005).

Every mutation is serialized by one lock; persistent rules are written to ``consent_rule``
through ``Database.run`` (single DB worker, Art. 8) **before** the in-memory ``RuleIndex``
changes, so a failed write leaves the index untouched and raises ``ConsentStoreError``.
Session rules live in the index only (REQ-CNS-005). One rule per key (``uq_consent_rule_key``):
a new rule for an existing key replaces it in place, keeping its ``rule_id`` (Q-C2).

Rows that cannot be turned back into a ``ConsentRule`` at load time are not dropped
silently: their Server (or every Server, for a global or unreadable row) is *poisoned* and
the policy denies its operations with ``source="error"`` until the row is fixed or revoked
(REQ-CNS-008, fail closed).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import text
from sqlmodel import select

from reflex_mcphost.core.consent.index import RuleIndex
from reflex_mcphost.core.consent.models import (
    ConsentRule,
    ConsentTarget,
    Effect,
    Lifetime,
    RuleOrigin,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.db import Database
from reflex_mcphost.core.db_models import ConsentRuleRow
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.types import Consumer, Fqn

__all__ = ["ALL_SERVERS", "ConsentStore", "ConsentStoreError", "UnknownRuleError"]

ALL_SERVERS = ""
"""Poison marker meaning every Server (a corrupt global or unreadable rule)."""
_log = get_logger(__name__)

_UPSERT = text(
    "INSERT INTO consent_rule (rule_id, effect, operation, target_kind, server_id, fqn,"
    " consumer_kind, consumer_name, origin, definition_hash, suspended, suspended_reason,"
    " created_at_us, updated_at_us)"
    " VALUES (:rule_id, :effect, :operation, :target_kind, :server_id, :fqn,"
    " :consumer_kind, :consumer_name, :origin, :definition_hash, 0, '', :created, :now)"
    " ON CONFLICT (operation, target_kind, server_id, fqn, consumer_kind, consumer_name)"
    " DO UPDATE SET effect = excluded.effect, origin = excluded.origin,"
    " definition_hash = excluded.definition_hash, suspended = 0, suspended_reason = '',"
    " updated_at_us = excluded.updated_at_us"
    " RETURNING rule_id, created_at_us"
)
_SUSPEND = text(
    "UPDATE consent_rule SET suspended = 1, suspended_reason = 'definition_changed',"
    " updated_at_us = :now WHERE rule_id = :rule_id AND suspended = 0"
)
_SET_EFFECT = text(
    "UPDATE consent_rule SET effect = :effect, updated_at_us = :now WHERE rule_id = :rule_id"
)
_DELETE = text("DELETE FROM consent_rule WHERE rule_id = :rule_id")


class ConsentStoreError(MCPHostError):
    """The consent database could not be read or written (REQ-CNS-008)."""


class UnknownRuleError(MCPHostError):
    """``update``/``revoke`` of a ``rule_id`` that does not exist (REQ-CNS-013)."""

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id
        super().__init__(f"unknown consent rule {rule_id!r}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_us(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000)


def _from_us(us: int) -> datetime:
    return datetime.fromtimestamp(us / 1_000_000, tz=UTC)


def _params(rule: ConsentRule) -> dict[str, Any]:
    operation, target_kind, server_id, fqn, consumer_kind, consumer_name = rule.key
    return {
        "rule_id": rule.id,
        "effect": rule.effect,
        "operation": operation,
        "target_kind": target_kind,
        "server_id": server_id,
        "fqn": fqn,
        "consumer_kind": consumer_kind,
        "consumer_name": consumer_name,
        "origin": rule.origin,
        "definition_hash": rule.definition_hash or "",
        "created": _to_us(rule.created_at),
        "now": _to_us(rule.updated_at),
    }


def _from_row(row: ConsentRuleRow) -> ConsentRule:
    if row.target_kind == "tool":
        target = ConsentTarget.tool(Fqn.parse(row.fqn))
    elif row.target_kind == "server":
        target = ConsentTarget.server(row.server_id)
    else:
        target = ConsentTarget.global_()
    consumer = (
        Consumer.parse(f"{row.consumer_kind}:{row.consumer_name}") if row.consumer_kind else None
    )
    return ConsentRule.model_validate(
        {
            "id": row.rule_id,
            "effect": row.effect,
            "operation": row.operation,
            "target": target,
            "consumer": consumer,
            "lifetime": "always",
            "origin": row.origin,
            "definition_hash": row.definition_hash or None,
            "suspended": bool(row.suspended),
            "created_at": _from_us(row.created_at_us),
            "updated_at": _from_us(row.updated_at_us),
        }
    )


class ConsentStore:
    """Persistent + session rules behind one ``RuleIndex`` (API §4.7 management calls)."""

    def __init__(
        self,
        db: Database,
        index: RuleIndex | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db = db
        self.index = index if index is not None else RuleIndex()
        self._clock = clock
        self._lock = anyio.Lock()
        self._poisoned: frozenset[str] = frozenset()

    # ------------------------------------------------------------------ reads

    def rules(self, *, include_session: bool = True) -> list[ConsentRule]:
        """Persistent and (optionally) session rules, newest first (Q-C5)."""
        return self.index.rules(include_session=include_session)

    def is_poisoned(self, server_id: str) -> bool:
        """Whether a corrupt stored rule may cover ``server_id`` (REQ-CNS-008)."""
        return ALL_SERVERS in self._poisoned or server_id in self._poisoned

    # ------------------------------------------------------------------ DB helpers

    async def _db_run(self, fn: Callable[[], Any], what: str) -> Any:
        try:
            return await self._db.run(fn)
        except Exception as exc:
            _log.error("consent store: cannot %s (%s)", what, type(exc).__name__)
            raise ConsentStoreError(f"cannot {what} ({type(exc).__name__})") from exc

    def _exec(self, statement: Any, params: dict[str, Any], *, returning: bool = False) -> Any:
        with self._db.session() as s:
            result = s.execute(statement, params)
            fetched = result.one() if returning else None
            s.commit()
            return fetched

    async def _upsert(self, rule: ConsentRule) -> ConsentRule:
        row = await self._db_run(
            lambda: self._exec(_UPSERT, _params(rule), returning=True), "store a rule"
        )
        rule = rule.model_copy(update={"id": row[0], "created_at": _from_us(row[1])})
        if rule.suspended:  # keep a suspension across lifetime edits
            await self._suspend_row(rule.id, rule.updated_at)
        return rule

    async def _suspend_row(self, rule_id: str, now: datetime) -> None:
        params = {"rule_id": rule_id, "now": _to_us(now)}
        await self._db_run(lambda: self._exec(_SUSPEND, params), "suspend a rule")

    async def _delete(self, rule_id: str) -> None:
        await self._db_run(lambda: self._exec(_DELETE, {"rule_id": rule_id}), "delete a rule")

    # ------------------------------------------------------------------ load

    async def load(self) -> int:
        """Q-C1: replace persistent rules in the index with the DB's; keep session rules.

        Returns the number of rules loaded. Rows that fail validation poison their Server.
        """

        def read() -> list[ConsentRuleRow]:
            with self._db.session() as s:
                return list(s.exec(select(ConsentRuleRow)).all())

        async with self._lock:
            rows: list[ConsentRuleRow] = await self._db_run(read, "load consent rules")
            loaded: list[ConsentRule] = []
            poisoned: set[str] = set()
            for row in rows:
                try:
                    loaded.append(_from_row(row))
                except ValueError as exc:
                    server = row.server_id if row.target_kind != "global" else ALL_SERVERS
                    poisoned.add(server)
                    _log.error(
                        "consent rule %s is corrupt (%s); denying operations on %s",
                        row.rule_id,
                        type(exc).__name__,
                        repr(server) if server else "every server",
                    )
            for rule in self.index.rules(include_session=False):
                self.index.remove(rule.id)
            for rule in loaded:
                self.index.put(rule)
            self._poisoned = frozenset(poisoned)
            return len(loaded)

    # ------------------------------------------------------------------ mutations

    async def add(self, rule: ConsentRule) -> ConsentRule:
        """Insert or replace (same key) a rule; ``always`` rules are written through first."""
        async with self._lock:
            return await self._add(rule)

    async def _add(self, rule: ConsentRule) -> ConsentRule:
        current = self.index.find(rule.key)
        if rule.lifetime == "always":
            rule = await self._upsert(rule)
        elif current is not None and current.lifetime == "always":
            await self._delete(current.id)
        elif current is not None:
            rule = rule.model_copy(update={"id": current.id, "created_at": current.created_at})
        self.index.put(rule)
        return rule

    async def grant(
        self,
        *,
        effect: Effect,
        operation: OperationKind,
        target: ConsentTarget,
        consumer: Consumer | None = None,
        lifetime: Lifetime = "always",
        origin: RuleOrigin = "programmatic",
        definition_hash: str | None = None,
    ) -> ConsentRule:
        """Build, validate and add a rule (REQ-CNS-014). ``ValueError`` if inconsistent."""
        now = self._clock()
        rule = ConsentRule(
            effect=effect,
            operation=operation,
            target=target,
            consumer=consumer,
            lifetime=lifetime,
            origin=origin,
            definition_hash=definition_hash if target.kind == "tool" else None,
            created_at=now,
            updated_at=now,
        )
        return await self.add(rule)

    async def update(
        self,
        rule_id: str,
        *,
        effect: Effect | None = None,
        lifetime: Lifetime | None = None,
    ) -> ConsentRule:
        """Q-C4 edit of effect and/or lifetime, applied to the next evaluation (REQ-CNS-013)."""
        async with self._lock:
            current = self.index.get(rule_id)
            if current is None:
                raise UnknownRuleError(rule_id)
            changed = ConsentRule.model_validate(
                {
                    **current.model_dump(),
                    "effect": effect or current.effect,
                    "lifetime": lifetime or current.lifetime,
                    "updated_at": self._clock(),
                }
            )
            if changed.lifetime == "always" and current.lifetime == "always":
                params = {
                    "rule_id": rule_id,
                    "effect": changed.effect,
                    "now": _to_us(changed.updated_at),
                }
                await self._db_run(lambda: self._exec(_SET_EFFECT, params), "update a rule")
            elif changed.lifetime == "always":
                changed = await self._upsert(changed)
            elif current.lifetime == "always":
                await self._delete(rule_id)
            self.index.put(changed)
            return changed

    async def revoke(self, rule_id: str) -> None:
        """Q-C4 delete; the rule stops applying immediately (REQ-CNS-013)."""
        async with self._lock:
            current = self.index.get(rule_id)
            if current is None:
                raise UnknownRuleError(rule_id)
            if current.lifetime == "always":
                await self._delete(rule_id)
            self.index.remove(rule_id)

    async def suspend(self, rules: Iterable[ConsentRule]) -> None:
        """Q-C3: mark stale tool rules suspended (REQ-CNS-007); persisted for ``always``."""
        async with self._lock:
            for stale in rules:
                current = self.index.get(stale.id)
                if current is None or current.suspended:
                    continue
                now = self._clock()
                if current.lifetime == "always":
                    await self._suspend_row(current.id, now)
                self.index.put(current.model_copy(update={"suspended": True, "updated_at": now}))
