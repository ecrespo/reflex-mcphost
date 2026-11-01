"""Server state machine (API §4.5 transition table)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.connection.state import ServerState, can_transition

S = ServerState


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (S.DISCONNECTED, S.CONNECTING),
        (S.CONNECTING, S.READY),
        (S.CONNECTING, S.AUTH_REQUIRED),
        (S.CONNECTING, S.FAILED),
        (S.CONNECTING, S.RECONNECTING),
        (S.READY, S.RECONNECTING),
        (S.RECONNECTING, S.READY),
        (S.RECONNECTING, S.FAILED),
        (S.RECONNECTING, S.CONNECTING),
        (S.RECONNECTING, S.AUTH_REQUIRED),
        (S.AUTH_REQUIRED, S.CONNECTING),
        (S.FAILED, S.CONNECTING),
        (S.READY, S.DISCONNECTED),
        (S.RECONNECTING, S.DISCONNECTED),
        (S.READY, S.DISABLED),
        (S.FAILED, S.CONFIG_ERROR),
        (S.CONFIG_ERROR, S.DISCONNECTED),
        (S.DISABLED, S.DISCONNECTED),
    ],
)
def test_allowed_transitions(old: ServerState, new: ServerState) -> None:
    """REQ-CON-010: transitions of the API §4.5 table are allowed."""
    assert can_transition(old, new)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (S.DISCONNECTED, S.READY),
        (S.FAILED, S.READY),
        (S.DISABLED, S.CONNECTING),
        (S.CONFIG_ERROR, S.CONNECTING),
        (S.AUTH_REQUIRED, S.READY),
        (S.READY, S.READY),
    ],
)
def test_forbidden_transitions(old: ServerState, new: ServerState) -> None:
    """REQ-CON-010: transitions outside the table are rejected."""
    assert not can_transition(old, new)
