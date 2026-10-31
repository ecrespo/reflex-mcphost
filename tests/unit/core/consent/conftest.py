"""Shared fixtures for consent policy tests (F3-03…F3-05)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import anyio
import pytest

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
)
from reflex_mcphost.core.consent.policy import ConsentPolicy
from reflex_mcphost.core.consent.store import ConsentStore
from reflex_mcphost.core.db import Database, open_database
from reflex_mcphost.core.errors import UnknownEntryError
from reflex_mcphost.core.events import EventBus, HostEvent
from reflex_mcphost.core.prompter import TrustRequest
from reflex_mcphost.core.types import Fqn


class FakePrompter:
    """Answers consent prompts from a script; records every request."""

    supports_elicitation = False

    def __init__(
        self,
        *answers: ConsentAnswer | object,
        error: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.answers = list(answers) or [ConsentAnswer(decision=ConsentDecision.ALLOW_ONCE)]
        self.error = error
        self.hang = hang
        self.requests: list[ConsentRequest] = []

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.requests.append(request)
        if self.hang:
            await anyio.sleep_forever()
        if self.error is not None:
            raise self.error
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer  # type: ignore[return-value]

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False


def answer(decision: str, **kwargs: object) -> ConsentAnswer:
    return ConsentAnswer(decision=ConsentDecision(decision), **kwargs)  # type: ignore[arg-type]


class Env:
    """A policy over a real SQLite store, a fake catalog and a swappable prompter."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db: Database = open_database(data_dir)
        self.events = EventBus()
        self.prompter: object | None = None
        self.tools: dict[str, ToolEntry] = {}
        self.policy = self.make_policy()

    def make_policy(self) -> ConsentPolicy:
        return ConsentPolicy(
            events=self.events,
            prompter=lambda: self.prompter,  # type: ignore[arg-type,return-value]
            tool_lookup=self.lookup,
        )

    def lookup(self, fqn: Fqn) -> ToolEntry:
        try:
            return self.tools[str(fqn)]
        except KeyError:
            raise UnknownEntryError(str(fqn)) from None

    async def attach(self, timeout_s: float = 5.0, pattern: str | None = None) -> ConsentPolicy:
        store = ConsentStore(self.db)
        if pattern is None:
            await self.policy.attach(store, timeout_s=timeout_s)
        else:
            await self.policy.attach(store, timeout_s=timeout_s, secret_arg_pattern=pattern)
        return self.policy

    async def restart(self) -> ConsentPolicy:
        self.db.close()
        self.db = open_database(self.data_dir)
        self.policy = self.make_policy()
        return await self.attach()

    async def collect(self, fn: Callable[[], object]) -> tuple[object, list[HostEvent]]:
        got: list[HostEvent] = []
        async with self.events.subscribe() as sub:
            result = await fn()  # type: ignore[misc]
            with anyio.move_on_after(0.05):
                async for event in sub:
                    got.append(event)
        return result, got


@pytest.fixture
def env_sync(tmp_path: Path) -> Iterator[Env]:
    env = Env(tmp_path / "data")
    yield env
    env.db.close()


@pytest.fixture
async def env(env_sync: Env) -> Env:
    await env_sync.attach()
    return env_sync
