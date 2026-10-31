"""``InFlightRegistry``: legacy attribution of server-initiated requests (REQ-HSH-045/047).

Legacy SDK callbacks run in the session's task and cannot see the caller's ``ContextVar``
(spike S-1), and the wire carries no link to the originating request. Each supervisor
keeps the Host requests in flight on its connection: the Invoker registers every legacy
``tools/call`` leg (with its ``PausableDeadline``) and the Catalog every legacy read. A
callback is attributed to the single entry when exactly one is in flight; otherwise it is
unattributed (``consumer=None``, ``attribution="unattributed"``): no stored or session allow
applies, the human Consumer is asked once-only, and the audit record says so.

An attributed prompter wait pauses that invocation's deadline (DD-004 legacy); unattributed
waits are not excluded (documented limitation). A refused sampling of an attributed tool
call is flagged on the entry so the Invoker can end the call ``denied/sampling_denied``
whatever ``CallToolResult`` the Server sends (Tech Design Flow C note).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from reflex_mcphost.core.mrtr import InputContext, PausableDeadline
from reflex_mcphost.core.types import Consumer

__all__ = ["InFlightEntry", "InFlightRegistry"]


@dataclass(eq=False)
class InFlightEntry:
    """One Host request in flight on a legacy connection."""

    consumer: Consumer
    deadline: PausableDeadline | None = None
    invocation_id: str | None = None
    sampling_denied: bool = False

    def flag_sampling_denied(self) -> None:
        self.sampling_denied = True


class InFlightRegistry:
    """The Host requests in flight on one Server's connection. Not thread-safe."""

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self._entries: list[InFlightEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    @contextmanager
    def track(
        self,
        *,
        consumer: Consumer,
        deadline: PausableDeadline | None = None,
        invocation_id: str | None = None,
    ) -> Iterator[InFlightEntry]:
        """Register a request for the body's duration."""
        entry = InFlightEntry(consumer, deadline, invocation_id)
        self._entries.append(entry)
        try:
            yield entry
        finally:
            self._entries.remove(entry)

    def attribute(self, method: str, key: str) -> InputContext:
        """The context of a server-initiated request: the single in-flight entry's, or an
        unattributed one."""
        if len(self._entries) == 1:
            entry = self._entries[0]
            return InputContext(
                server_id=self.server_id,
                consumer=entry.consumer,
                key=key,
                method=method,
                deadline=entry.deadline,
                invocation_id=entry.invocation_id,
                attribution="exact",
                on_refused=entry.flag_sampling_denied,
            )
        return InputContext(
            server_id=self.server_id,
            consumer=None,
            key=key,
            method=method,
            attribution="unattributed",
        )
