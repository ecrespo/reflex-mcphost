"""Host event bus (API §4.11, DD-014).

Publishing never blocks: each subscriber owns a bounded memory stream; on overflow its
oldest buffered events are dropped and the subscriber receives a single ``events.dropped``
(with the number of dropped events) before the surviving, newest events. ``publish`` must be
called from the event-loop thread.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import BaseModel, Field, TypeAdapter

from reflex_mcphost.core.config.models import ConfigProblem, RegistryDiff
from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.types import Consumer, Fqn

CatalogList = Literal["tools", "resources", "resource_templates", "prompts"]


class _Event(BaseModel, frozen=True):
    pass


# Payload fields typed `Any` are placeholders for types introduced by later phases
# (Outcome); names are final.
class ServerStateChanged(_Event, frozen=True):
    type: Literal["server.state_changed"] = "server.state_changed"
    server_id: str
    old: ServerState
    new: ServerState
    reason: str | None = None


class RegistryChanged(_Event, frozen=True):
    type: Literal["registry.changed"] = "registry.changed"
    diff: RegistryDiff
    problems: tuple[ConfigProblem, ...] = ()


class CatalogChanged(_Event, frozen=True):
    type: Literal["catalog.changed"] = "catalog.changed"
    server_id: str
    lists: tuple[CatalogList, ...]


class ResourceUpdated(_Event, frozen=True):
    type: Literal["resource.updated"] = "resource.updated"
    server_id: str
    uri: str


class InvocationStarted(_Event, frozen=True):
    type: Literal["invocation.started"] = "invocation.started"
    invocation_id: str
    fqn: Fqn
    consumer: Consumer


class InvocationProgress(_Event, frozen=True):
    type: Literal["invocation.progress"] = "invocation.progress"
    invocation_id: str
    fqn: Fqn
    consumer: Consumer
    progress: Any


class InvocationFinished(_Event, frozen=True):
    type: Literal["invocation.finished"] = "invocation.finished"
    invocation_id: str
    fqn: Fqn
    consumer: Consumer
    outcome: Any
    duration_ms: int


class ConsentRequested(_Event, frozen=True):
    type: Literal["consent.requested"] = "consent.requested"
    request_id: str
    operation: str
    consumer: Consumer
    server_id: str
    fqn: Fqn | None = None


class ConsentResolved(_Event, frozen=True):
    type: Literal["consent.resolved"] = "consent.resolved"
    request_id: str
    operation: str
    consumer: Consumer
    server_id: str
    fqn: Fqn | None = None
    outcome: Any


class ServerLog(_Event, frozen=True):
    type: Literal["server.log"] = "server.log"
    server_id: str
    entry: LogEntry


class EventsDropped(_Event, frozen=True):
    type: Literal["events.dropped"] = "events.dropped"
    count: int


HostEvent = Annotated[
    ServerStateChanged
    | RegistryChanged
    | CatalogChanged
    | ResourceUpdated
    | InvocationStarted
    | InvocationProgress
    | InvocationFinished
    | ConsentRequested
    | ConsentResolved
    | ServerLog
    | EventsDropped,
    Field(discriminator="type"),
]
host_event_adapter: TypeAdapter[HostEvent] = TypeAdapter(HostEvent)


class _Subscription:
    """One subscriber's bounded buffer plus its pending drop count."""

    def __init__(self, buffer: int) -> None:
        self.send: MemoryObjectSendStream[HostEvent]
        self.receive: MemoryObjectReceiveStream[HostEvent]
        self.send, self.receive = anyio.create_memory_object_stream[HostEvent](buffer)
        self.dropped = 0

    def offer(self, event: HostEvent) -> None:
        try:
            self.send.send_nowait(event)
        except anyio.WouldBlock:
            self.receive.receive_nowait()  # drop the oldest buffered event
            self.dropped += 1
            self.send.send_nowait(event)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            pass  # subscriber is leaving; its context manager removes it

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> HostEvent:
        if self.dropped:
            count, self.dropped = self.dropped, 0
            return EventsDropped(count=count)
        try:
            return await self.receive.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    def close(self) -> None:
        self.send.close()
        self.receive.close()


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def publish(self, event: HostEvent) -> None:
        """Deliver ``event`` to every subscriber without blocking."""
        for sub in tuple(self._subscriptions):
            sub.offer(event)

    @asynccontextmanager
    async def subscribe(self, *, buffer: int = 256) -> AsyncIterator[AsyncIterator[HostEvent]]:
        if buffer < 1:
            raise ValueError("buffer must be >= 1")
        sub = _Subscription(buffer)
        self._subscriptions.append(sub)
        try:
            yield sub
        finally:
            self._subscriptions.remove(sub)
            sub.close()
