"""``HostState`` handlers through the pinned seam (F6-02; DD-011).

States are built with ``HostState(_reflex_internal_init=True)`` and handlers run as
``HostState.<handler>.fn(state, ...)`` (pinned in ``tests/contract/test_reflex_surface.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from reflex_mcphost.ui.install import HostRuntime
from reflex_mcphost.ui.state import WATCHERS, HostState


@pytest.fixture
def running() -> Iterator[Any]:
    host = object()
    HostRuntime._begin()
    HostRuntime._publish(host)  # type: ignore[arg-type]
    try:
        yield host
    finally:
        HostRuntime._end()


def test_state_holds_only_view_models() -> None:
    """REQ-UI-007, TD §5.1: HostState's vars are JSON-serializable view models."""
    state = HostState(_reflex_internal_init=True)
    assert state.servers == []
    assert state.tools == []
    assert state.cards == []
    assert state.banner == ""


def test_start_watch_without_a_host_shows_the_banner() -> None:
    """REQ-UI-007: with no running Host ``start_watch`` sets the "Host stopped" banner and
    starts no watcher (no exception)."""
    state = HostState(_reflex_internal_init=True)
    assert HostState.start_watch.fn(state) is None
    assert state.banner == "Host stopped"


def test_start_watch_with_a_host_chains_the_background_watcher(running: Any) -> None:
    """REQ-UI-007, DD-011: ``start_watch`` (page ``on_load``) chains the background
    ``watch`` event unless this token already has a watcher."""
    state = HostState(_reflex_internal_init=True)
    assert HostState.start_watch.fn(state) is HostState.watch
    token = state.router.session.client_token
    with WATCHERS.claim(token) as claimed:
        assert claimed
        assert HostState.start_watch.fn(state) is None
