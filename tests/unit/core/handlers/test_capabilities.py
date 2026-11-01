"""``CapabilityPlan`` / ``HandlerSet`` (F4-05; DD-003) and the legacy ``InFlightRegistry``
(REQ-HSH-045/047)."""

from __future__ import annotations

from typing import Any

import pytest

from reflex_mcphost.core.connection.inflight import InFlightRegistry
from reflex_mcphost.core.handlers.capabilities import CapabilityPlan, HandlerSet
from reflex_mcphost.core.mrtr import ELICITATION, ROOTS, SAMPLING, PausableDeadline
from reflex_mcphost.core.types import Consumer

BOT = Consumer.agent("bot")


class Source:
    def __init__(self, name: str) -> None:
        self.name = name

    def as_callback(self, attribute: Any) -> Any:
        return f"{self.name}-callback"


HANDLERS = HandlerSet(elicitation=Source("e"), sampling=Source("s"), roots=Source("r"))


def test_empty_plan_declares_nothing() -> None:
    """REQ-CON-003, REQ-HSH-006, REQ-HSH-014: with nothing enabled no callback is
    registered (registering is declaring) and no input method is declared."""
    plan = CapabilityPlan()
    assert plan.methods == frozenset()
    assert HANDLERS.client_kwargs(plan, lambda m, k: None) == {}  # type: ignore[arg-type,return-value]


@pytest.mark.parametrize(
    ("plan", "methods", "keys"),
    [
        (CapabilityPlan(elicitation=True), {ELICITATION}, {"elicitation_callback"}),
        (CapabilityPlan(sampling=True), {SAMPLING}, {"sampling_callback"}),
        (CapabilityPlan(roots=True), {ROOTS}, {"list_roots_callback"}),
        (CapabilityPlan(log_level="debug"), set(), {"log_level"}),
        (
            CapabilityPlan(elicitation=True, sampling=True, roots=True),
            {ELICITATION, SAMPLING, ROOTS},
            {"elicitation_callback", "sampling_callback", "list_roots_callback"},
        ),
    ],
)
def test_plan_declares_exactly_the_enabled_set(
    plan: CapabilityPlan, methods: set[str], keys: set[str]
) -> None:
    """REQ-CON-003, REQ-HSH-006, REQ-HSH-014 (DD-003): each enabled feature registers exactly
    its callback; log_level is passed through."""
    assert plan.methods == methods
    assert set(HANDLERS.client_kwargs(plan, lambda m, k: None)) == keys  # type: ignore[arg-type,return-value]


def test_declared_feature_without_handler_registers_nothing() -> None:
    """REQ-HSH-014 (fail closed): no handler, no callback."""
    assert HandlerSet().client_kwargs(CapabilityPlan(elicitation=True), lambda m, k: None) == {}  # type: ignore[arg-type,return-value]


def test_single_entry_is_attributed() -> None:
    """REQ-HSH-045: with exactly one request in flight a callback is attributed to it, with
    its deadline; a refusal flags the entry."""
    registry = InFlightRegistry("srv")
    deadline = PausableDeadline(5)
    with registry.track(consumer=BOT, deadline=deadline, invocation_id="inv_1") as entry:
        ctx = registry.attribute(SAMPLING, "4")
        assert (ctx.consumer, ctx.attribution, ctx.deadline, ctx.invocation_id) == (
            BOT,
            "exact",
            deadline,
            "inv_1",
        )
        assert (ctx.server_id, ctx.method, ctx.key) == ("srv", SAMPLING, "4")
        assert ctx.on_refused is not None
        ctx.on_refused()
        assert entry.sampling_denied
    assert len(registry) == 0


@pytest.mark.parametrize("count", [0, 2])
def test_zero_or_several_entries_are_unattributed(count: int) -> None:
    """REQ-HSH-047: zero or several in-flight requests → unattributed (no consumer, no
    deadline, no refusal flag)."""
    registry = InFlightRegistry("srv")
    tracks = [registry.track(consumer=BOT) for _ in range(count)]
    for t in tracks:
        t.__enter__()
    ctx = registry.attribute(ELICITATION, "1")
    assert (ctx.consumer, ctx.attribution, ctx.deadline, ctx.on_refused) == (
        None,
        "unattributed",
        None,
        None,
    )
    for t in tracks:
        t.__exit__(None, None, None)
    assert len(registry) == 0
