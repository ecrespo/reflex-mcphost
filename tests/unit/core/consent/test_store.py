"""``ConsentStore``: write-through persistence of ``always`` rules (F3-02, DM §2.2, Q-C*)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import select

from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.consent.store import ConsentStore, ConsentStoreError, UnknownRuleError
from reflex_mcphost.core.db import DB_FILENAME, Database, open_database
from reflex_mcphost.core.db_models import ConsentRuleRow
from reflex_mcphost.core.types import Consumer, Fqn
from tests.unit.core.consent.factories import rule, sha, tool_entry

pytestmark = pytest.mark.anyio
FQN = Fqn.parse("srv::echo")
HASH = tool_entry("srv::echo").definition_hash
BOT = Consumer.agent("bot")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def db(data_dir: Path) -> Iterator[Database]:
    database = open_database(data_dir)
    yield database
    database.close()


@pytest.fixture
async def store(db: Database) -> ConsentStore:
    s = ConsentStore(db)
    await s.load()
    return s


def rows(db: Database) -> list[ConsentRuleRow]:
    with db.session() as s:
        return list(s.exec(select(ConsentRuleRow)).all())


def decide(store: ConsentStore, consumer: Consumer = BOT) -> str | None:
    return store.index.evaluate(
        operation=OperationKind.TOOL_CALL,
        server_id="srv",
        fqn=FQN,
        consumer=consumer,
        definition_hash=HASH,
    ).effect


async def reopened(data_dir: Path, db: Database) -> ConsentStore:
    db.close()
    fresh = ConsentStore(open_database(data_dir))
    await fresh.load()
    return fresh


async def test_always_rule_survives_reopen(
    store: ConsentStore, db: Database, data_dir: Path
) -> None:
    """REQ-CNS-006: an ``always`` rule is persisted and applies after a restart."""
    granted = await store.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.tool(FQN),
        consumer=BOT,
        definition_hash=HASH,
    )
    assert granted.origin == "programmatic"
    assert granted.lifetime == "always"
    assert decide(store) == "allow"
    fresh = await reopened(data_dir, db)
    assert fresh.rules() == [granted]
    assert decide(fresh) == "allow"
    assert decide(fresh, Consumer.agent("other")) is None


async def test_session_rules_stay_in_memory(
    store: ConsentStore, db: Database, data_dir: Path
) -> None:
    """REQ-CNS-005: session rules are never written to SQLite."""
    session = await store.add(rule(consumer=BOT, lifetime="session", definition_hash=HASH))
    assert rows(db) == []
    assert store.rules() == [session]
    assert store.rules(include_session=False) == []
    assert decide(store) == "allow"
    fresh = await reopened(data_dir, db)
    assert fresh.rules() == []


async def test_unique_key_updates_in_place(store: ConsentStore, db: Database) -> None:
    """REQ-CNS-014: a second decision for the same key replaces the first (Q-C2 upsert)."""
    first = await store.add(rule(consumer=BOT, definition_hash=HASH))
    second = await store.add(rule("deny", consumer=BOT, definition_hash=HASH))
    assert second.id == first.id
    assert second.created_at == first.created_at
    assert [(r.rule_id, r.effect) for r in rows(db)] == [(first.id, "deny")]
    assert store.rules() == [second]
    assert decide(store) == "deny"


async def test_update_and_revoke_apply_live(
    store: ConsentStore, db: Database, data_dir: Path
) -> None:
    """REQ-CNS-013: edits and revocations apply to the next evaluation without restart."""
    granted = await store.grant(
        effect="allow",
        operation=OperationKind.TOOL_CALL,
        target=ConsentTarget.server("srv"),
        consumer=BOT,
    )
    updated = await store.update(granted.id, effect="deny")
    assert updated.id == granted.id
    assert updated.effect == "deny"
    assert updated.updated_at >= granted.updated_at
    assert decide(store) == "deny"
    assert rows(db)[0].effect == "deny"
    await store.revoke(granted.id)
    assert decide(store) is None
    assert rows(db) == []
    with pytest.raises(UnknownRuleError):
        await store.revoke(granted.id)
    with pytest.raises(UnknownRuleError):
        await store.update(granted.id, effect="allow")
    fresh = await reopened(data_dir, db)
    assert fresh.rules() == []


async def test_update_lifetime_moves_between_memory_and_db(
    store: ConsentStore, db: Database
) -> None:
    """REQ-CNS-005, REQ-CNS-006, REQ-CNS-013: lifetime edits move the rule in or out of the DB."""
    session = await store.add(rule(consumer=BOT, lifetime="session", definition_hash=HASH))
    always = await store.update(session.id, lifetime="always")
    assert always.lifetime == "always"
    assert [r.rule_id for r in rows(db)] == [always.id]
    back = await store.update(always.id, lifetime="session")
    assert back.lifetime == "session"
    assert rows(db) == []
    assert store.rules() == [back]
    same = await store.update(back.id)
    assert same.lifetime == "session"


async def test_session_rule_replacing_persistent_deletes_row(
    store: ConsentStore, db: Database
) -> None:
    """DM §2.2: one rule per key across lifetimes; the replaced persistent row is removed."""
    await store.add(rule(consumer=BOT, definition_hash=HASH))
    session = await store.add(rule("deny", consumer=BOT, lifetime="session", definition_hash=HASH))
    assert rows(db) == []
    assert store.rules() == [session]


async def test_suspension_persisted(store: ConsentStore, db: Database, data_dir: Path) -> None:
    """REQ-CNS-007: suspension of a stale tool rule is written to the DB (Q-C3)."""
    stale = await store.add(rule(consumer=BOT, definition_hash=sha("old")))
    session = await store.add(
        rule(
            target=ConsentTarget.tool("srv::other"),
            lifetime="session",
            definition_hash=sha("old2"),
        )
    )
    await store.suspend([stale, session])
    assert all(r.suspended for r in store.rules())
    row = rows(db)[0]
    assert (row.suspended, row.suspended_reason) == (True, "definition_changed")
    fresh = await reopened(data_dir, db)
    assert fresh.rules()[0].suspended
    # a new answer for the same key replaces (and un-suspends) the rule
    renewed = await fresh.add(rule(consumer=BOT, definition_hash=HASH))
    assert renewed.id == stale.id
    assert not renewed.suspended
    await fresh.suspend([renewed.model_copy(update={"id": "rule_01JAAAAAAAAAAAAAAAAAAAAAAA"})])
    assert not fresh.rules()[0].suspended


async def test_corrupt_row_poisons_its_server(db: Database, data_dir: Path) -> None:
    """REQ-CNS-008: a rule that cannot be loaded marks its Server (or all) unevaluable."""
    raw = sqlite3.connect(data_dir / DB_FILENAME)
    with raw:
        raw.execute(
            "INSERT INTO consent_rule (rule_id, effect, operation, target_kind, server_id, fqn,"
            " consumer_kind, consumer_name, origin, created_at_us, updated_at_us) VALUES"
            " ('rule_01JAAAAAAAAAAAAAAAAAAAAAAA', 'deny', 'tool_call', 'server', 'srv', '',"
            " 'agent', 'not valid!', 'prompt', 1, 1)"
        )
    raw.close()
    store = ConsentStore(db)
    assert await store.load() == 0
    assert store.is_poisoned("srv")
    assert not store.is_poisoned("other")
    raw = sqlite3.connect(data_dir / DB_FILENAME)
    with raw:
        raw.execute("UPDATE consent_rule SET target_kind = 'global', server_id = ''")
    raw.close()
    await store.load()
    assert store.is_poisoned("other")


async def test_db_failure_surfaces_and_keeps_index(
    store: ConsentStore, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-CNS-008: a failed write-through raises and changes nothing in memory (DD-005)."""

    async def broken(*_: Any) -> Any:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "run", broken)
    with pytest.raises(ConsentStoreError):
        await store.add(rule(consumer=BOT, definition_hash=HASH))
    assert store.rules() == []
    with pytest.raises(ConsentStoreError):
        await store.load()


async def test_grant_validates_sampling_target(store: ConsentStore) -> None:
    """REQ-CNS-014: programmatic grants use the same scopes and validation."""
    with pytest.raises(ValueError, match="sampling"):
        await store.grant(
            effect="allow",
            operation=OperationKind.SAMPLING,
            target=ConsentTarget.tool(FQN),
            definition_hash=HASH,
        )
    sampling = await store.grant(
        effect="deny",
        operation=OperationKind.SAMPLING,
        target=ConsentTarget.global_(),
        lifetime="session",
    )
    assert sampling.consumer is None
    assert sampling.lifetime == "session"
