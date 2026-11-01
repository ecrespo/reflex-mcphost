"""``PromptRegistry``: pending prompts and their change stream (F6-03; Flow D, delta D2)."""

from __future__ import annotations

import anyio
import pytest

from reflex_mcphost.ui.prompter import PendingPrompt, PromptChange, PromptRegistry

pytestmark = pytest.mark.anyio


async def _wait_pending(registry: PromptRegistry, count: int) -> None:
    with anyio.fail_after(5):
        while len(registry.pending()) < count:
            await anyio.lowlevel.checkpoint()


async def test_two_subscribers_see_added_and_the_first_answer_wins() -> None:
    """REQ-UI-005 (every tab sees a prompt), Flow D step 5: both subscribers receive
    ``added``; the first ``resolve`` wins and returns ``True``, a second returns ``False``;
    the entry is then removed and ``removed`` is published."""
    registry = PromptRegistry()
    answers: list[object] = []
    async with registry.subscribe() as a, registry.subscribe() as b:
        async with anyio.create_task_group() as tg:

            async def ask() -> None:
                answers.append(await registry.ask("consent", "req_1", {"payload": 1}))

            tg.start_soon(ask)
            first_a, first_b = await a.__anext__(), await b.__anext__()
            assert first_a == first_b == PromptChange("added", "req_1")
            [pending] = registry.pending()
            assert isinstance(pending, PendingPrompt)
            assert (pending.kind, pending.request_id, pending.payload) == (
                "consent",
                "req_1",
                {"payload": 1},
            )
            assert registry.get("req_1") is pending
            assert registry.resolve("req_1", "first") is True
            assert registry.resolve("req_1", "second") is False
        assert await a.__anext__() == PromptChange("removed", "req_1")
        assert await b.__anext__() == PromptChange("removed", "req_1")
    assert answers == ["first"]
    assert registry.pending() == []
    assert registry.resolve("req_1", "late") is False
    assert registry.resolve("unknown", "x") is False


async def test_cancelling_the_asker_removes_the_entry() -> None:
    """REQ-CNS-009 (core timeout), Flow D step 5: when the awaiting coroutine is cancelled
    (core timeout, Server disconnected, Host closing) the entry is removed and ``removed``
    published."""
    registry = PromptRegistry()
    async with registry.subscribe() as changes:
        with anyio.move_on_after(0.05):
            await registry.ask("trust", "trust_1", object())
        assert registry.pending() == []
        assert [await changes.__anext__(), await changes.__anext__()] == [
            PromptChange("added", "trust_1"),
            PromptChange("removed", "trust_1"),
        ]


async def test_overflow_delivers_one_dropped_marker() -> None:
    """Flow D step 4: a slow subscriber's stream drops the oldest changes and delivers a
    single ``dropped`` marker (with the count) before the newest ones."""
    registry = PromptRegistry()
    async with registry.subscribe(buffer=2) as slow, anyio.create_task_group() as tg:
        for i in range(5):
            tg.start_soon(registry.ask, "consent", f"req_{i}", i)
        await _wait_pending(registry, 5)
        first = await slow.__anext__()
        assert first.type == "dropped"
        assert first.count == 3
        assert (await slow.__anext__()).type == "added"
        assert (await slow.__anext__()).type == "added"
        tg.cancel_scope.cancel()


async def test_same_request_id_updates_the_entry() -> None:
    """Flow D step 5 (elicitation re-prompt): a second ask with a pending request id
    replaces the payload, publishes ``updated`` and both askers get the answer."""
    registry = PromptRegistry()
    answers: list[object] = []

    async def ask(payload: str) -> None:
        answers.append(await registry.ask("elicitation_form", "el_1", payload))

    async with registry.subscribe() as changes, anyio.create_task_group() as tg:
        tg.start_soon(ask, "first")
        assert await changes.__anext__() == PromptChange("added", "el_1")
        tg.start_soon(ask, "second")
        assert await changes.__anext__() == PromptChange("updated", "el_1")
        assert registry.get("el_1").payload == "second"  # type: ignore[union-attr]
        assert registry.resolve("el_1", "done")
    assert answers == ["done", "done"]
    assert registry.pending() == []
