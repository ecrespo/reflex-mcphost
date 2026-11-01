"""``ClientLease``: safe cross-task use of a Client that may be closing (DD-002).

The supervisor creates one lease per ``async with Client`` block and invalidates it when
the block exits; any later use raises ``ServerUnavailableError``. Users hold the lease
through ``acquire()`` so the supervisor can count (and, in F2-09, wait for) them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import anyio
from mcp import Client

from reflex_mcphost.core.errors import ServerUnavailableError

__all__ = ["ClientLease"]


class ClientLease:
    def __init__(
        self, server_id: str, client: Client, *, state_of: Callable[[], str] | None = None
    ) -> None:
        self.server_id = server_id
        self._client = client
        self._state_of = state_of
        self._valid = True
        self._state: str | None = None
        self._active = 0
        self._idle = anyio.Event()
        self._idle.set()

    @property
    def valid(self) -> bool:
        return self._valid

    @property
    def active(self) -> int:
        """Number of users currently inside ``acquire()``."""
        return self._active

    @property
    def client(self) -> Client:
        """The live client; raises ``ServerUnavailableError`` once invalidated."""
        if not self._valid:
            state = self._state or (self._state_of() if self._state_of else "disconnected")
            raise ServerUnavailableError(self.server_id, state)
        return self._client

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Client]:
        client = self.client
        self._active += 1
        self._idle = anyio.Event() if self._active == 1 else self._idle
        try:
            yield client
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def wait_idle(self) -> None:
        """Return once no user is inside ``acquire()``."""
        await self._idle.wait()

    def invalidate(self, state: str | None = None) -> None:
        """Called when the Client block exits. Later errors report ``state``, or else the
        Server's state at the time of use (``state_of``)."""
        self._valid = False
        self._state = str(state) if state is not None else None
