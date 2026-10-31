"""``AuthorizationFlow``: one OAuth authorization in progress (API §4.5, Flow E).

Returned by ``ConnectionManager.authorize``. ``authorization_url`` is what the UI opens
(the CLI prints it; core never opens a browser) — ``None`` when the attempt succeeded
without a browser step. ``wait()`` returns once the Server is ``ready`` or raises
``AuthorizationError`` with a reason code once it is back in ``auth_required`` (or left
the attempt otherwise). ``cancel()`` aborts it: nothing is redeemed.
"""

from __future__ import annotations

import anyio

from reflex_mcphost.core.errors import AuthorizationError, AuthorizationReason

__all__ = ["DEFAULT_FLOW_DEADLINE_S", "AuthorizationFlow"]

DEFAULT_FLOW_DEADLINE_S = 300.0
"""REQ-AUTH-006: the whole flow must complete within 5 minutes."""
CANCEL_WAIT_S = 5.0


class AuthorizationFlow:
    """A running authorization; created by the Host (``ConnectionManager.authorize``)."""

    def __init__(self, server_id: str, *, deadline_s: float = DEFAULT_FLOW_DEADLINE_S) -> None:
        self.server_id = server_id
        self.authorization_url: str | None = None
        self.deadline = anyio.current_time() + deadline_s
        """Absolute ``anyio`` time by which the flow must be completed."""
        self.failure: AuthorizationReason | None = None
        """Reason recorded by the controller when the attempt's OAuth flow failed."""
        self._settled = anyio.Event()  # URL known or flow finished
        self._done = anyio.Event()
        self._cancelled = anyio.Event()
        self._reason: AuthorizationReason | None = None

    def __repr__(self) -> str:  # the URL carries `state`: never shown in repr (Art. 5)
        return f"AuthorizationFlow(server_id={self.server_id!r}, done={self.done})"

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def cancelled(self) -> anyio.Event:
        return self._cancelled

    @property
    def finished(self) -> anyio.Event:
        return self._done

    @property
    def settled(self) -> anyio.Event:
        """Set once the authorization URL is known or the flow finished."""
        return self._settled

    async def wait(self) -> None:
        """Return on success; raise ``AuthorizationError`` (reason code) otherwise."""
        await self._done.wait()
        if self._reason is not None:
            raise AuthorizationError(self.server_id, self._reason)

    async def cancel(self) -> None:
        """Abort the flow (reason ``cancelled``); returns once it ended (bounded)."""
        if self.done:
            return
        self._cancelled.set()
        with anyio.move_on_after(CANCEL_WAIT_S):
            await self._done.wait()

    # ------------------------------------------------------------------ Host-internal

    def set_url(self, url: str) -> None:
        self.authorization_url = url
        self._settled.set()

    def finish(self, reason: AuthorizationReason | None) -> None:
        """Resolve the flow: ``None`` = success, else the failure reason."""
        if self.done:
            return
        self._reason = reason
        self._done.set()
        self._settled.set()
