"""``RuleIndex``: in-memory consent rules and evaluation steps 0-3 (API §4.7, DD-005).

Pure and synchronous: no I/O. Holds persistent and session rules, one per ``RuleKey``
(DM §2.2: a new rule for an existing key replaces it). ``evaluate`` reports the deciding
rule, if any, and the stale allow tool rules to suspend (step 3); persisting the suspension is
the caller's job (``ConsentStore``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from reflex_mcphost.core.consent.models import (
    Attribution,
    ConsentRule,
    Effect,
    RuleKey,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.types import Consumer, Fqn

__all__ = ["IndexEvaluation", "RuleIndex"]

_TargetKey = tuple[str, str, str, str]  # (operation, target_kind, server_id, fqn)


@dataclass(frozen=True, slots=True)
class IndexEvaluation:
    """Result of steps 0-3. ``effect is None`` means no decisive rule (prompt next)."""

    effect: Effect | None
    rule: ConsentRule | None
    to_suspend: tuple[ConsentRule, ...] = ()
    definition_changed: bool = False


def _target_key(rule: ConsentRule) -> _TargetKey:
    return rule.key[:4]


class RuleIndex:
    """Rules by id, by unique key and by target key (≤ 3 dict lookups per evaluation)."""

    def __init__(self, rules: Iterable[ConsentRule] = ()) -> None:
        self._by_id: dict[str, ConsentRule] = {}
        self._by_key: dict[RuleKey, str] = {}
        self._by_target: dict[_TargetKey, dict[str, ConsentRule]] = {}
        for rule in rules:
            self.put(rule)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[ConsentRule]:
        return iter(tuple(self._by_id.values()))

    # ------------------------------------------------------------------ mutation

    def put(self, rule: ConsentRule) -> ConsentRule | None:
        """Insert or replace ``rule``; return the rule it displaced under the same key."""
        if rule.id in self._by_id:
            self.remove(rule.id)
        replaced_id = self._by_key.get(rule.key)
        replaced = self.remove(replaced_id) if replaced_id is not None else None
        self._by_id[rule.id] = rule
        self._by_key[rule.key] = rule.id
        self._by_target.setdefault(_target_key(rule), {})[rule.id] = rule
        return replaced

    def remove(self, rule_id: str) -> ConsentRule | None:
        rule = self._by_id.pop(rule_id, None)
        if rule is None:
            return None
        del self._by_key[rule.key]
        bucket = self._by_target[_target_key(rule)]
        del bucket[rule_id]
        if not bucket:
            del self._by_target[_target_key(rule)]
        return rule

    def clear(self) -> None:
        self._by_id.clear()
        self._by_key.clear()
        self._by_target.clear()

    # ------------------------------------------------------------------ reads

    def get(self, rule_id: str) -> ConsentRule | None:
        return self._by_id.get(rule_id)

    def find(self, key: RuleKey) -> ConsentRule | None:
        rule_id = self._by_key.get(key)
        return self._by_id[rule_id] if rule_id is not None else None

    def rules(self, *, include_session: bool = True) -> list[ConsentRule]:
        """All rules, newest ``updated_at`` first (Q-C5)."""
        selected = [r for r in self._by_id.values() if include_session or r.lifetime == "always"]
        return sorted(selected, key=lambda r: (r.updated_at, r.id), reverse=True)

    def evaluate(
        self,
        *,
        operation: OperationKind,
        server_id: str,
        fqn: Fqn | None,
        consumer: Consumer,
        definition_hash: str | None,
        attribution: Attribution = "exact",
    ) -> IndexEvaluation:
        """Steps 0-3 of the normative algorithm (API §4.7).

        Step 0 (``unattributed``, REQ-HSH-047): only deny rules count, whatever Consumer
        they are scoped to. Steps 1-2: non-suspended rules matching operation, target and
        Consumer (``None`` or equal); only the highest ``rank`` present decides; deny wins
        within it (REQ-CNS-003, REQ-CNS-015). Step 3: a matching allow tool rule whose
        hash differs from ``definition_hash`` is skipped and returned in ``to_suspend``
        (REQ-CNS-007); with no current hash, allow tool rules are skipped (fail closed).
        Deny tool rules always apply: a definition change never loosens a decision.
        """
        op = operation.value
        keys: list[_TargetKey] = [(op, "global", "", ""), (op, "server", server_id, "")]
        if fqn is not None:
            keys.append((op, "tool", server_id, str(fqn)))
        unattributed = attribution == "unattributed"
        live: list[ConsentRule] = []
        stale: list[ConsentRule] = []
        for key in keys:
            for rule in self._by_target.get(key, {}).values():
                if rule.suspended:
                    continue
                if unattributed:
                    if rule.effect != "deny":
                        continue
                elif rule.consumer is not None and rule.consumer != consumer:
                    continue
                if (
                    rule.target.kind == "tool"
                    and rule.effect == "allow"
                    and rule.definition_hash != definition_hash
                ):
                    if definition_hash is not None:
                        stale.append(rule)
                    continue
                live.append(rule)
        stale.sort(key=lambda r: r.id)
        if not live:
            return IndexEvaluation(None, None, tuple(stale), bool(stale))
        top = max(r.rank for r in live)
        best = sorted((r for r in live if r.rank == top), key=lambda r: (r.effect != "deny", r.id))
        decisive = best[0]
        return IndexEvaluation(decisive.effect, decisive, tuple(stale), bool(stale))
