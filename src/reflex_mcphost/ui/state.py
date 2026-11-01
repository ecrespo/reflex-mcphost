"""``HostState`` and the per-tab watcher (DD-011, spike S-5 §4).

A page's ``on_load`` runs ``HostState.start_watch``, which chains the background
``HostState.watch``. The watcher logic is plain async code, testable without Reflex:

- ``watch_host(token, commit)`` claims the tab's client token in the process-local
  ``WATCHERS`` registry (never in Reflex state: a flag in state survives a reload while the
  task is cancelled — S-5 pitfall), subscribes to the Host's ``EventBus`` and to the
  ``PromptRegistry`` change stream (merged), builds a ``HostView`` and feeds it with
  ``apply_events``. Cancellation (tab closed, reload) only
  releases the token and logs; it never touches state.
- ``apply_events(source, sink)`` maps each event to the one recompute it needs:
  ``server.state_changed`` / ``catalog.changed`` / ``server.log`` / ``registry.changed``
  affect one Server (or the diff's Servers); ``events.dropped`` recomputes everything; a
  prompt change re-renders the cards from ``PromptRegistry.pending()``.
- ``HostView`` holds the tab's view models (``ui.models`` rows) and hands each changed
  field to ``commit``; in ``HostState.watch`` that is ``async with self`` + assignment.

``HostState`` holds only JSON-serializable view models. Its answer handlers turn a card
answer into a ``Prompter`` return value through ``PromptRegistry.resolve`` — after checking
it is one of the card's options (REQ-HSH-047); the UI never writes consent rules itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Protocol

import anyio
import reflex as rx

from reflex_mcphost.core.consent.models import ConsentRequest
from reflex_mcphost.core.errors import MCPHostError
from reflex_mcphost.core.events import (
    CatalogChanged,
    EventsDropped,
    RegistryChanged,
    ServerLog,
    ServerStateChanged,
)
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.ui import models
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.install import HostRuntime
from reflex_mcphost.ui.prompter import PromptAnswerError, PromptChange, PromptRegistry

if TYPE_CHECKING:
    from reflex_mcphost.core.connection.stderr import LogEntry
    from reflex_mcphost.core.host import Host

__all__ = [
    "WATCHERS",
    "Commit",
    "HostState",
    "HostView",
    "Sink",
    "WatcherRegistry",
    "apply_events",
    "banner_text",
    "watch_host",
]

Commit = Callable[[dict[str, Any]], Awaitable[None]]
"""Writes changed view-model fields into a tab's state (``async with self`` in Reflex)."""
CATALOG_FIELDS = {
    "tools": "tools",
    "resources": "resources",
    "resource_templates": "resource_templates",
    "prompts": "catalog_prompts",
}
"""``catalog.changed`` list name → ``HostState`` field."""
_log = get_logger(__name__)


class Sink(Protocol):
    """What ``apply_events`` drives (``HostView``; fakes in tests)."""

    async def refresh_all(self) -> None: ...

    async def server_changed(self, server_id: str) -> None: ...

    async def catalog_changed(self, server_id: str, lists: tuple[str, ...]) -> None: ...

    async def server_log(self, server_id: str, entry: LogEntry) -> None: ...

    async def servers_changed(self, server_ids: tuple[str, ...]) -> None: ...

    async def prompts_changed(self) -> None: ...


async def apply_events(source: AsyncIterable[object], sink: Sink) -> None:
    """Apply every item of ``source`` to ``sink`` until the source ends."""
    async for event in source:
        if isinstance(event, ServerStateChanged):
            await sink.server_changed(event.server_id)
        elif isinstance(event, CatalogChanged):
            await sink.catalog_changed(event.server_id, tuple(event.lists))
        elif isinstance(event, ServerLog):
            await sink.server_log(event.server_id, event.entry)
        elif isinstance(event, RegistryChanged):
            diff = event.diff
            await sink.servers_changed((*diff.added, *diff.removed, *diff.changed))
        elif isinstance(event, EventsDropped):
            await sink.refresh_all()
        elif isinstance(event, PromptChange):
            await sink.prompts_changed()


@asynccontextmanager
async def merged(*sources: AsyncIterable[object]) -> AsyncIterator[AsyncIterator[object]]:
    """One stream of the items of every source (unbuffered: each source keeps its own
    bounded, drop-oldest buffer)."""
    send, receive = anyio.create_memory_object_stream[object](0)

    async def pump(source: AsyncIterable[object], out: Any) -> None:
        async with out:
            async for item in source:
                await out.send(item)

    async with anyio.create_task_group() as tg:
        for source in sources:
            tg.start_soon(pump, source, send.clone())
        send.close()
        try:
            async with receive:
                yield receive
        finally:
            tg.cancel_scope.cancel()


# ------------------------------------------------------------------ view models of one tab


class HostView:
    """One tab's view models, recomputed from ``host`` per affected Server."""

    def __init__(self, host: Host, commit: Commit, prompts: PromptRegistry | None = None) -> None:
        self._host = host
        self._commit = commit
        self._prompts = prompts
        self.servers: list[models.Row] = []
        self.catalog: dict[str, list[models.Row]] = {kind: [] for kind in CATALOG_FIELDS}
        self.logs: dict[str, list[models.Row]] = {}

    def _registry(self) -> Any:
        try:
            return self._host.registry
        except (RuntimeError, MCPHostError):
            return None

    def _catalog_entries(self, kind: str, server_id: str | None) -> list[Any]:
        catalog = self._host.catalog
        lists: dict[str, Callable[[str | None], list[Any]]] = {
            "tools": catalog.tools,
            "resources": catalog.resources,
            "resource_templates": catalog.resource_templates,
            "prompts": catalog.prompts,
        }
        return lists[kind](server_id)

    def _catalog_fields(self, kinds: Iterator[str] | tuple[str, ...]) -> dict[str, Any]:
        return {CATALOG_FIELDS[k]: list(self.catalog[k]) for k in kinds}

    async def refresh_all(self) -> None:
        registry = self._registry()
        self.servers = models.server_rows(self._host.connections.statuses(), registry)
        for kind in CATALOG_FIELDS:
            self.catalog[kind] = models.catalog_rows(kind, self._catalog_entries(kind, None))
        await self._commit(
            {
                "banner": "",
                "servers": list(self.servers),
                **self._catalog_fields(tuple(CATALOG_FIELDS)),
                "cards": self._cards(),
            }
        )

    def _cards(self) -> list[models.Row]:
        return models.prompt_cards(self._prompts.pending()) if self._prompts is not None else []

    def _recompute_server(self, server_id: str) -> None:
        try:
            status = self._host.connections.status(server_id)
        except MCPHostError:
            self.servers = [r for r in self.servers if r["id"] != server_id]
            self.logs.pop(server_id, None)
            for kind in CATALOG_FIELDS:
                self.catalog[kind] = models.replace_server_rows(self.catalog[kind], server_id, [])
            return
        row = models.server_row(status, self._registry())
        for i, old in enumerate(self.servers):
            if old["id"] == server_id:
                self.servers[i] = row
                break
        else:
            self.servers.append(row)
        for kind in CATALOG_FIELDS:
            rows = models.catalog_rows(kind, self._catalog_entries(kind, server_id))
            self.catalog[kind] = models.replace_server_rows(self.catalog[kind], server_id, rows)

    async def server_changed(self, server_id: str) -> None:
        await self.servers_changed((server_id,))

    async def servers_changed(self, server_ids: tuple[str, ...]) -> None:
        for server_id in dict.fromkeys(server_ids):
            self._recompute_server(server_id)
        await self._commit(
            {
                "servers": list(self.servers),
                "logs": dict(self.logs),
                **self._catalog_fields(tuple(CATALOG_FIELDS)),
            }
        )

    async def catalog_changed(self, server_id: str, lists: tuple[str, ...]) -> None:
        kinds = tuple(k for k in dict.fromkeys(lists) if k in CATALOG_FIELDS)
        for kind in kinds:
            rows = models.catalog_rows(kind, self._catalog_entries(kind, server_id))
            self.catalog[kind] = models.replace_server_rows(self.catalog[kind], server_id, rows)
        if kinds:
            await self._commit(self._catalog_fields(kinds))

    async def server_log(self, server_id: str, entry: LogEntry) -> None:
        lines = [*self.logs.get(server_id, []), *models.log_rows([entry])]
        self.logs[server_id] = lines[-models.LOG_LIMIT :]
        await self._commit({"logs": dict(self.logs)})

    async def prompts_changed(self) -> None:
        await self._commit({"cards": self._cards()})


# ------------------------------------------------------------------ the watcher


class WatcherRegistry:
    """Process-local set of client tokens that have a running watcher."""

    def __init__(self) -> None:
        self._tokens: set[str] = set()

    def active(self, token: str) -> bool:
        return token in self._tokens

    @contextmanager
    def claim(self, token: str) -> Iterator[bool]:
        """Yield ``True`` and hold ``token`` until exit, or ``False`` if already held."""
        if token in self._tokens:
            yield False
            return
        self._tokens.add(token)
        try:
            yield True
        finally:
            self._tokens.discard(token)


WATCHERS = WatcherRegistry()
"""The watchers of this backend process."""


def banner_text(phase: str) -> str:
    """The banner shown while no Host is running (``""`` when one is)."""
    if phase == "running":
        return ""
    return "Host starting…" if phase == "starting" else "Host stopped"


def _suffix(token: str) -> str:
    return f"…{token[-6:]}" if token else "…"


async def watch_host(
    token: str,
    commit: Commit,
    *,
    runtime: Any = HostRuntime,
    watchers: WatcherRegistry = WATCHERS,
    prompts: PromptRegistry | None = None,
    view: Callable[[Any, Commit, PromptRegistry], Sink] = HostView,
) -> None:
    """Run one tab's watcher until cancelled; a no-op if ``token`` already has one.
    ``prompts`` defaults to the process-wide ``PROMPTS``."""
    registry = prompts if prompts is not None else prompter_mod.PROMPTS
    with watchers.claim(token) as claimed:
        if not claimed:
            return
        try:
            host = runtime.host
        except RuntimeError:
            await commit({"banner": banner_text(runtime.phase)})
            return
        _log.info("watcher started (%s)", _suffix(token))
        try:
            async with (
                host.events.subscribe() as events,
                registry.subscribe() as changes,
                merged(events, changes) as source,
            ):
                sink = view(host, commit, registry)
                await sink.refresh_all()
                await apply_events(source, sink)
        finally:
            _log.info("watcher stopped (%s)", _suffix(token))


# ------------------------------------------------------------------ Reflex state


class HostState(rx.State):
    """View models of the Host for one tab (``ui.models`` rows only)."""

    banner: str = ""
    servers: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    tools: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    resources: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    resource_templates: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    catalog_prompts: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    logs: dict[str, list[dict[str, Any]]] = {}  # noqa: RUF012 - Reflex copies var defaults per state
    cards: list[dict[str, Any]] = []  # noqa: RUF012 - Reflex copies var defaults per state
    prompt_error: str = ""
    chosen_targets: dict[str, str] = {}  # noqa: RUF012 - Reflex copies var defaults per state
    chosen_deny_lifetimes: dict[str, str] = {}  # noqa: RUF012 - as above

    @rx.event
    def start_watch(self) -> Any:
        """Page ``on_load``: start this tab's watcher (once per client token)."""
        if HostRuntime.phase != "running":
            self.banner = banner_text(HostRuntime.phase)
            return None
        if WATCHERS.active(self.router.session.client_token):
            return None
        return HostState.watch

    @rx.event(background=True)
    async def watch(self) -> None:
        """Background watcher of this tab; ends when Reflex cancels it (tab gone)."""

        async def commit(fields: dict[str, Any]) -> None:
            async with self:
                for name, value in fields.items():
                    setattr(self, name, value)

        await watch_host(self.router.session.client_token, commit)

    @rx.event
    def answer_consent(self, request_id: str, choice: str, target: str, deny_lifetime: str) -> None:
        """A consent card answer (REQ-CNS-004/005/006); options the card does not offer are
        rejected here and never reach core (REQ-HSH-047)."""
        self._answer_consent(request_id, choice, target, deny_lifetime)

    def _answer_consent(
        self, request_id: str, choice: str, target: str, deny_lifetime: str
    ) -> None:
        registry = prompter_mod.PROMPTS
        prompt = registry.get(request_id)
        if prompt is None or not isinstance(prompt.payload, ConsentRequest):
            self.prompt_error = "This prompt is no longer pending."
            return
        try:
            answer = prompter_mod.consent_answer(prompt.payload, choice, target, deny_lifetime)
        except PromptAnswerError as exc:
            self.prompt_error = str(exc)
            return
        self.prompt_error = ""
        if not registry.resolve(request_id, answer):
            self.prompt_error = "This prompt was already answered."

    @rx.event
    def answer_trust(self, request_id: str, trusted: bool) -> None:
        """A trust card answer (REQ-REG-011): only an explicit Trust trusts the Server."""
        registry = prompter_mod.PROMPTS
        prompt = registry.get(request_id)
        if prompt is None or prompt.kind != "trust":
            self.prompt_error = "This prompt is no longer pending."
            return
        self.prompt_error = ""
        registry.resolve(request_id, trusted is True)

    @rx.event
    def choose_target(self, request_id: str, target: str) -> None:
        """Remember the target picked on a consent card."""
        self.chosen_targets = {**self.chosen_targets, request_id: target}

    @rx.event
    def choose_deny_lifetime(self, request_id: str, lifetime: str) -> None:
        """Remember the deny lifetime picked on a consent card."""
        self.chosen_deny_lifetimes = {**self.chosen_deny_lifetimes, request_id: lifetime}

    @rx.event
    def answer_card(self, request_id: str, choice: str) -> None:
        """An answer button: ``choice`` with the card's picked target and deny lifetime
        (defaults: the card's first target, ``once``)."""
        prompt = prompter_mod.PROMPTS.get(request_id)
        targets = (
            prompter_mod.consent_options(prompt.payload)[1]
            if prompt is not None and isinstance(prompt.payload, ConsentRequest)
            else ("tool",)
        )
        target = self.chosen_targets.get(request_id, targets[0])
        lifetime = self.chosen_deny_lifetimes.get(request_id, "once")
        self._answer_consent(request_id, choice, target, lifetime)
        if self.prompt_error == "":
            self.chosen_targets = {k: v for k, v in self.chosen_targets.items() if k != request_id}
            self.chosen_deny_lifetimes = {
                k: v for k, v in self.chosen_deny_lifetimes.items() if k != request_id
            }
