"""Shared pieces of the UI integration tests (F6-04…F6-07): a real Host served through
``HostState`` handlers, no browser.

``Tab`` is one tab: a plain ``HostState`` that background handlers drive through a proxy
(``async with self`` is a no-op) and that a watcher (``watch_host``) may feed; every write
signals waiters, so tests wait on state changes, never on sleeps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio

from reflex_mcphost.core.host import Host
from reflex_mcphost.ui.state import HostState, WatcherRegistry, watch_host
from tests.integration.test_host import TIMEOUT

__all__ = ["Runtime", "Tab", "watched"]


class Runtime:
    """What ``watch_host`` needs of ``HostRuntime``."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self.phase = "running"


class _Proxy:
    def __init__(self, tab: Tab) -> None:
        object.__setattr__(self, "_tab", tab)

    async def __aenter__(self) -> _Proxy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tab.state, name)

    def __setattr__(self, name: str, value: Any) -> None:
        self._tab.set(name, value)


class Tab:
    """One tab's ``HostState`` with change notification."""

    def __init__(self) -> None:
        self.state = HostState(_reflex_internal_init=True)
        self.proxy = _Proxy(self)
        self._changed = anyio.Event()

    def set(self, name: str, value: Any) -> None:
        setattr(self.state, name, value)
        self._changed.set()
        self._changed = anyio.Event()

    async def commit(self, fields: dict[str, Any]) -> None:
        for name, value in fields.items():
            self.set(name, value)

    async def until(self, predicate: Callable[[HostState], bool], within: float = TIMEOUT) -> None:
        with anyio.fail_after(within):
            while not predicate(self.state):
                await self._changed.wait()

    async def run(self, handler: Any, *args: Any) -> list[Any]:
        """Run a background handler's function on this tab; the events it yielded."""
        result = handler.fn(self.proxy, *args)
        if hasattr(result, "__anext__"):
            return [event async for event in result]
        value = await result
        return [] if value is None else [value]

    def sync(self, handler: Any, *args: Any) -> Any:
        """Run a plain (sync) handler, then signal waiters."""
        value = handler.fn(self.state, *args)
        self._changed.set()
        self._changed = anyio.Event()
        return value

    async def call(self, handler: Any, *args: Any) -> None:
        """Run an async (non-background) handler, then signal waiters."""
        await handler.fn(self.state, *args)
        self._changed.set()
        self._changed = anyio.Event()


@asynccontextmanager
async def watched(host: Host, tab: Tab, **kwargs: Any) -> AsyncIterator[Tab]:
    """Feed ``tab`` from its own watcher until the block ends."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            lambda: watch_host(
                f"tab-{id(tab):x}",
                tab.commit,
                runtime=Runtime(host),
                watchers=WatcherRegistry(),
                **kwargs,
            )
        )
        await tab.until(lambda s: bool(s.servers))
        yield tab
        tg.cancel_scope.cancel()
