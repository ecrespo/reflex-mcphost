"""Reconnection backoff (REQ-CON-005, Tech Design Flow B step 9)."""

from __future__ import annotations

import random

from hypothesis import given
from hypothesis import strategies as st

from reflex_mcphost.core.connection.state import MAX_DELAY_S, MAX_FAILURES, backoff_delay


def test_first_delay_is_one_second_jittered() -> None:
    """REQ-CON-005: backoff starts at 1 s with jitter in [0.5, 1.5]."""
    assert backoff_delay(0, rng=lambda: 0.0) == 0.5
    assert backoff_delay(0, rng=lambda: 1.0) == 1.5
    assert backoff_delay(0, rng=lambda: 0.5) == 1.0


def test_delay_doubles() -> None:
    """REQ-CON-005: the nominal delay doubles each failure."""
    mid = lambda: 0.5  # noqa: E731 — jitter factor 1.0
    assert [backoff_delay(n, rng=mid) for n in range(5)] == [1.0, 2.0, 4.0, 8.0, 16.0]


@given(st.integers(min_value=0, max_value=200), st.floats(min_value=0.0, max_value=1.0))
def test_delay_is_capped_after_jitter(failures: int, u: float) -> None:
    """REQ-CON-005: every delay is capped at 60 s after jitter is applied."""
    delay = backoff_delay(failures, rng=lambda: u)
    assert 0.5 <= delay <= MAX_DELAY_S


def test_default_rng_stays_in_bounds() -> None:
    """REQ-CON-005: the default RNG produces jittered delays within bounds."""
    random.seed(1)
    assert all(0.5 * 2**3 <= backoff_delay(3) <= 1.5 * 2**3 for _ in range(100))


def test_failure_limit_is_ten() -> None:
    """REQ-CON-006: ten consecutive failures is the limit."""
    assert MAX_FAILURES == 10
