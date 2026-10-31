"""Server states, the API §4.5 transition table and reconnection backoff (REQ-CON-005/006)."""

from __future__ import annotations

import random
from collections.abc import Callable
from enum import StrEnum

MAX_DELAY_S = 60.0
MAX_FAILURES = 10
JITTER_MIN, JITTER_MAX = 0.5, 1.5


class ServerState(StrEnum):
    DISABLED = "disabled"
    CONFIG_ERROR = "config_error"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    RECONNECTING = "reconnecting"
    AUTH_REQUIRED = "auth_required"
    FAILED = "failed"


S = ServerState
_ALWAYS = frozenset({S.DISABLED, S.CONFIG_ERROR})  # set_enabled(False); reload with a var problem
_TRANSITIONS: dict[ServerState, frozenset[ServerState]] = {
    S.DISABLED: frozenset({S.DISCONNECTED}),
    S.CONFIG_ERROR: frozenset({S.DISCONNECTED}),
    S.DISCONNECTED: frozenset({S.CONNECTING}),
    S.CONNECTING: frozenset({S.READY, S.AUTH_REQUIRED, S.FAILED, S.RECONNECTING, S.DISCONNECTED}),
    S.READY: frozenset({S.RECONNECTING, S.DISCONNECTED}),
    S.RECONNECTING: frozenset({S.READY, S.FAILED, S.CONNECTING, S.DISCONNECTED}),
    S.AUTH_REQUIRED: frozenset({S.CONNECTING, S.DISCONNECTED}),
    S.FAILED: frozenset({S.CONNECTING, S.DISCONNECTED}),
}


def can_transition(old: ServerState, new: ServerState) -> bool:
    if old is new:
        return False
    return new in _ALWAYS or new in _TRANSITIONS[old]


def backoff_delay(failures: int, *, rng: Callable[[], float] = random.random) -> float:
    """Delay before retry number ``failures + 1``: ``min(60, 2**failures * jitter)``, jitter
    uniform in [0.5, 1.5] — the cap applies after jitter (REQ-CON-005)."""
    jitter = JITTER_MIN + (JITTER_MAX - JITTER_MIN) * rng()
    nominal = 2.0 ** min(failures, 16)  # 2**16 already far above the cap
    return min(MAX_DELAY_S, nominal * jitter)
