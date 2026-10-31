"""``Catalog``: per-Server snapshots of tools, resources, templates and prompts (API §4.6).

Lifecycle (Tech Design Flow B steps 6-7). The Host registers :meth:`Catalog.on_ready` as a
supervisor ``on_ready`` hook: it runs for as long as the Server stays ``ready`` (the
supervisor cancels it when the connection ends) and

1. fetches every list the Server declared, following pagination cursors to the end
   (REQ-CAT-001), and publishes one ``catalog.changed``;
2. watches for list changes (REQ-CAT-003): modern — one ``subscriptions/listen`` stream
   opting in to the ``listChanged`` kinds the capabilities declare (plus the
   ``resourceSubscriptions`` of :meth:`subscribe`); legacy — the
   ``notifications/*/list_changed`` routed by :meth:`message_handler`. Each change re-fetches
   only that list and publishes ``catalog.changed(lists=…)``. A listen stream that ends
   while the Server is ready is reopened with the REQ-CON-005 backoff and every list is
   re-fetched once it is open again (REQ-CAT-012); transport failures are the supervisor's
   (``reconnecting``, API §4.5).
3. re-fetches a list the Server does not watch when its ``ttlMs`` elapses, never more often
   than every 30 s (REQ-CAT-013, SHOULD);
4. on exit marks the Server's entries ``stale`` — they stay visible (REQ-CAT-004) until the
   next ``ready`` replaces them.

Entries carry an FQN ``server_id::name`` (resources: ``(server_id, uri)``; REQ-CAT-002) and
are ordered by ``server_id`` then ``name``. A duplicate name keeps the first entry and logs
a warning naming Server and entry (REQ-CAT-008). Each tool stores ``definition_hash``
(REQ-CAT-009), its MCP Apps ``visibility`` and ``ui_resource_uri`` (REQ-CAT-014).
Annotations are stored verbatim as untrusted display data: nothing branches on them
(REQ-CAT-011, enforced by ``tests/architecture/test_annotation_hints.py``). Tools the SDK
drops for an invalid ``x-mcp-header`` never reach the Catalog (REQ-CON-018).

Reads (REQ-CAT-005…007) go through the supervisor's ``ClientLease`` and the SDK auto-loop
(``input_required_max_rounds=8``, DD-001). They are protocol reads: not consent-gated nor
audited in v0.1 (API §4.6 consent-scope decision). Not thread-safe (API §3.1).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, cast

import anyio
import mcp_types as types
import uritemplate
from anyio.abc import TaskGroup
from mcp import Client, InputRequiredRoundsExceededError, MCPError
from mcp.client import subscriptions as listen_events
from mcp.client.session import IncomingMessage, MessageHandlerFnT
from mcp.client.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ServerEvent,
    SubscriptionLost,
    ToolsListChanged,
)
from pydantic import BaseModel, ConfigDict

from reflex_mcphost.core.connection.state import (
    ServerState,
    ServerStatus,
    backoff_delay,
)
from reflex_mcphost.core.errors import (
    CapabilityNotSupportedError,
    InputRequestError,
    ServerRequestError,
    ServerUnavailableError,
    UnknownEntryError,
)
from reflex_mcphost.core.events import CatalogChanged, CatalogList, EventBus, ResourceUpdated
from reflex_mcphost.core.hmac_key import canonical_json
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.types import Consumer, Fqn

__all__ = [
    "DEFAULT_VISIBILITY",
    "TTL_FLOOR_S",
    "Catalog",
    "PromptEntry",
    "PromptResult",
    "ResourceContents",
    "ResourceEntry",
    "ResourceTemplateEntry",
    "ToolEntry",
    "Visibility",
    "definition_hash",
]

Visibility = Literal["model", "app"]
DEFAULT_VISIBILITY: tuple[Visibility, ...] = ("model", "app")
"""MCP Apps default when a tool declares no ``_meta.ui.visibility`` (REQ-CAT-014)."""
TTL_FLOOR_S = 30.0
LISTEN_STABLE_S = 30.0
"""A listen stream must stay open this long before the reopen backoff resets."""
"""REQ-CAT-013: a list is never re-fetched for its ``ttlMs`` more often than this."""
MAX_PAGES = 1000
"""Upper bound on pages per list, against a Server that never ends its cursor chain."""
ALL_LISTS: tuple[CatalogList, ...] = ("tools", "resources", "resource_templates", "prompts")
_DEPRECATED_UI_KEY = "ui/resourceUri"  # MCP Apps 2026-01-26: deprecated flat key
_HUMAN = Consumer.human()
_log = get_logger(__name__)
T = TypeVar("T")


class _Entry(BaseModel):
    model_config = ConfigDict(frozen=True)


class ToolEntry(_Entry):
    """One tool of one Server (API §4.6). ``annotations`` are display only (REQ-CAT-011)."""

    fqn: Fqn
    server_id: str
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: types.ToolAnnotations | None = None
    definition_hash: str
    ui_resource_uri: str | None = None
    visibility: tuple[Visibility, ...] = DEFAULT_VISIBILITY
    stale: bool = False


class ResourceEntry(_Entry):
    """One listed resource, identified by ``(server_id, uri)`` (REQ-CAT-002)."""

    server_id: str
    uri: str
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    size: int | None = None
    stale: bool = False


class ResourceTemplateEntry(_Entry):
    """One RFC 6570 resource template (REQ-CAT-006)."""

    fqn: Fqn
    server_id: str
    name: str
    uri_template: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    stale: bool = False


class PromptEntry(_Entry):
    """One prompt (REQ-CAT-007)."""

    fqn: Fqn
    server_id: str
    name: str
    title: str | None = None
    description: str | None = None
    arguments: tuple[types.PromptArgument, ...] = ()
    stale: bool = False


class ResourceContents(_Entry):
    """Result of ``read_resource`` / ``read_template`` (REQ-CAT-005): text and blob contents
    with their MIME types (SDK types)."""

    server_id: str
    uri: str
    contents: tuple[types.TextResourceContents | types.BlobResourceContents, ...]


class PromptResult(_Entry):
    """Result of ``get_prompt`` (REQ-CAT-007)."""

    fqn: Fqn
    description: str | None = None
    messages: tuple[types.PromptMessage, ...]


def definition_hash(tool: types.Tool) -> str:
    """REQ-CAT-009: ``sha256:`` + hex SHA-256 of the canonical JSON (sorted keys, no
    whitespace, UTF-8 kept) of ``{name, title, description, inputSchema, outputSchema,
    annotations}`` (wire field names)."""
    annotations = (
        tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        if tool.annotations is not None
        else None
    )
    material = {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "annotations": annotations,
    }
    return "sha256:" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _ui_meta(tool: types.Tool) -> tuple[str | None, tuple[Visibility, ...]]:
    """``(ui_resource_uri, visibility)`` from ``_meta`` (MCP Apps 2026-01-26, REQ-CAT-014)."""
    meta = tool.meta or {}
    ui = meta.get("ui")
    ui = ui if isinstance(ui, dict) else {}
    uri = ui.get("resourceUri")
    if not isinstance(uri, str):
        flat = meta.get(_DEPRECATED_UI_KEY)
        uri = flat if isinstance(flat, str) else None
    raw = ui.get("visibility")
    visibility = DEFAULT_VISIBILITY
    if isinstance(raw, list) and all(v in DEFAULT_VISIBILITY for v in raw):
        visibility = tuple(dict.fromkeys(cast(list[Visibility], raw)))
    return uri, visibility


def _tool_entry(server_id: str, tool: types.Tool) -> ToolEntry:
    uri, visibility = _ui_meta(tool)
    return ToolEntry(
        fqn=Fqn(server_id=server_id, name=tool.name),
        server_id=server_id,
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        annotations=tool.annotations,
        definition_hash=definition_hash(tool),
        ui_resource_uri=uri,
        visibility=visibility,
    )


def _resource_entry(server_id: str, resource: types.Resource) -> ResourceEntry:
    return ResourceEntry(
        server_id=server_id,
        uri=str(resource.uri),
        name=resource.name,
        title=resource.title,
        description=resource.description,
        mime_type=resource.mime_type,
        size=resource.size,
    )


def _template_entry(server_id: str, template: types.ResourceTemplate) -> ResourceTemplateEntry:
    return ResourceTemplateEntry(
        fqn=Fqn(server_id=server_id, name=template.name),
        server_id=server_id,
        name=template.name,
        uri_template=template.uri_template,
        title=template.title,
        description=template.description,
        mime_type=template.mime_type,
    )


def _prompt_entry(server_id: str, prompt: types.Prompt) -> PromptEntry:
    return PromptEntry(
        fqn=Fqn(server_id=server_id, name=prompt.name),
        server_id=server_id,
        name=prompt.name,
        title=prompt.title,
        description=prompt.description,
        arguments=tuple(prompt.arguments or ()),
    )


def _declared(caps: types.ServerCapabilities | None) -> tuple[CatalogList, ...]:
    """The lists a Server declared (Flow B step 6)."""
    if caps is None:
        return ()
    lists: list[CatalogList] = []
    if caps.tools is not None:
        lists.append("tools")
    if caps.resources is not None:
        lists += ["resources", "resource_templates"]
    if caps.prompts is not None:
        lists.append("prompts")
    return tuple(lists)


def _list_changed(caps: types.ServerCapabilities | None) -> frozenset[CatalogList]:
    """Lists whose changes the Server declares it notifies (``listChanged``)."""
    if caps is None:
        return frozenset()
    kinds: set[CatalogList] = set()
    if caps.tools is not None and caps.tools.list_changed:
        kinds.add("tools")
    if caps.resources is not None and caps.resources.list_changed:
        kinds |= {"resources", "resource_templates"}
    if caps.prompts is not None and caps.prompts.list_changed:
        kinds.add("prompts")
    return frozenset(kinds)


def _lists_of(event: ServerEvent | types.ServerNotification) -> tuple[CatalogList, ...]:
    if isinstance(event, ToolsListChanged | types.ToolListChangedNotification):
        return ("tools",)
    if isinstance(event, PromptsListChanged | types.PromptListChangedNotification):
        return ("prompts",)
    if isinstance(event, ResourcesListChanged | types.ResourceListChangedNotification):
        return ("resources", "resource_templates")
    return ()


class _Lease(Protocol):
    """What the Catalog needs from a ``ServerSupervisor`` (Host-internal)."""

    @property
    def status(self) -> ServerStatus: ...

    def lease(self) -> Any: ...  # AsyncContextManager[Client]


SupervisorLookup = Callable[[str], _Lease]
"""``server_id`` → its supervisor; raises ``UnknownServerError`` / ``ServerUnavailableError``."""
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class _ServerCatalog:
    """Entries and watcher state of one Server."""

    tools: tuple[ToolEntry, ...] = ()
    resources: tuple[ResourceEntry, ...] = ()
    templates: tuple[ResourceTemplateEntry, ...] = ()
    prompts: tuple[PromptEntry, ...] = ()
    era: str | None = None
    ttl_ms: dict[CatalogList, int] = field(default_factory=dict)
    subscriptions: set[str] = field(default_factory=set)
    pending: set[CatalogList] = field(default_factory=set)
    wake: anyio.Event = field(default_factory=anyio.Event)
    resubscribe: anyio.Event = field(default_factory=anyio.Event)

    def signal(self, lists: Iterable[CatalogList]) -> None:
        self.pending.update(lists)
        self.wake.set()

    def mark_stale(self) -> None:
        self.tools = tuple(e.model_copy(update={"stale": True}) for e in self.tools)
        self.resources = tuple(e.model_copy(update={"stale": True}) for e in self.resources)
        self.templates = tuple(e.model_copy(update={"stale": True}) for e in self.templates)
        self.prompts = tuple(e.model_copy(update={"stale": True}) for e in self.prompts)


def _unique(server_id: str, kind: str, entries: Iterable[T], key: Callable[[T], str]) -> list[T]:
    """Keep the first entry per key; warn about each duplicate (REQ-CAT-008)."""
    seen: set[str] = set()
    kept: list[T] = []
    for entry in entries:
        k = key(entry)
        if k in seen:
            _log.warning(
                "server %r returned a duplicate %s %r; keeping the first", server_id, kind, k
            )
            continue
        seen.add(k)
        kept.append(entry)
    return kept


class Catalog:
    """Tools, resources, templates and prompts of every Server (API §4.6)."""

    def __init__(
        self,
        *,
        events: EventBus,
        supervisor_of: SupervisorLookup,
        sleep: Sleep = anyio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._events = events
        self._supervisor_of = supervisor_of
        self._sleep = sleep
        self._rng = rng
        self._servers: dict[str, _ServerCatalog] = {}

    # ------------------------------------------------------------------ sync reads

    def _ids(self, server_id: str | None) -> list[str]:
        if server_id is not None:
            return [server_id] if server_id in self._servers else []
        return sorted(self._servers)

    def tools(self, server_id: str | None = None) -> list[ToolEntry]:
        """Tools ordered by ``server_id`` then ``name``; stale ones included (REQ-CAT-004)."""
        return [e for i in self._ids(server_id) for e in self._servers[i].tools]

    def resources(self, server_id: str | None = None) -> list[ResourceEntry]:
        return [e for i in self._ids(server_id) for e in self._servers[i].resources]

    def resource_templates(self, server_id: str | None = None) -> list[ResourceTemplateEntry]:
        return [e for i in self._ids(server_id) for e in self._servers[i].templates]

    def prompts(self, server_id: str | None = None) -> list[PromptEntry]:
        return [e for i in self._ids(server_id) for e in self._servers[i].prompts]

    def get_tool(self, fqn: Fqn | str) -> ToolEntry:
        """The tool named by ``fqn``; ``UnknownEntryError`` if there is none."""
        return self._find(fqn, lambda s: s.tools)

    def _find(self, fqn: Fqn | str, entries: Callable[[_ServerCatalog], Sequence[T]]) -> T:
        key = fqn if isinstance(fqn, Fqn) else Fqn.parse(fqn)
        server = self._servers.get(key.server_id)
        if server is not None:
            for entry in entries(server):
                if getattr(entry, "name", None) == key.name:
                    return entry
        raise UnknownEntryError(str(key))

    # ------------------------------------------------------------------ async reads

    async def read_resource(
        self, server_id: str, uri: str, *, consumer: Consumer = _HUMAN
    ) -> ResourceContents:
        """``resources/read`` (REQ-CAT-005): text and blob contents with MIME types.

        ``consumer`` (default ``Consumer.human()``) is the Consumer of input requests inside
        the read; it takes effect with the host-side handlers of F3-10."""
        result = await self._request(server_id, lambda c: c.read_resource(uri))
        return ResourceContents(server_id=server_id, uri=uri, contents=tuple(result.contents))

    async def read_template(
        self, fqn: Fqn | str, params: Mapping[str, str], *, consumer: Consumer = _HUMAN
    ) -> ResourceContents:
        """Expand the template per RFC 6570 and read the URI (REQ-CAT-006)."""
        entry = self._find(fqn, lambda s: s.templates)
        uri = uritemplate.expand(entry.uri_template, dict(params))
        return await self.read_resource(entry.server_id, uri, consumer=consumer)

    async def get_prompt(
        self,
        fqn: Fqn | str,
        arguments: Mapping[str, str] | None = None,
        *,
        consumer: Consumer = _HUMAN,
    ) -> PromptResult:
        """``prompts/get`` with ``arguments`` (REQ-CAT-007)."""
        entry = self._find(fqn, lambda s: s.prompts)
        args = dict(arguments) if arguments is not None else None
        result = await self._request(entry.server_id, lambda c: c.get_prompt(entry.name, args))
        return PromptResult(
            fqn=entry.fqn, description=result.description, messages=tuple(result.messages)
        )

    async def subscribe(self, server_id: str, uri: str) -> None:
        """Receive ``resource.updated`` for ``uri`` (REQ-CAT-010, COULD): modern — added to
        the listen stream's ``resourceSubscriptions``; legacy — ``resources/subscribe``.
        Remembered across reconnects. ``CapabilityNotSupportedError`` unless the Server
        declares ``resources.subscribe``."""
        status = self._subscribable(server_id)
        state = self._server(server_id)
        if uri in state.subscriptions:
            return
        if status.era == "legacy":
            await self._request(server_id, lambda c: _send_subscribe(c, uri, subscribe=True))
        state.subscriptions.add(uri)
        state.resubscribe.set()

    async def unsubscribe(self, server_id: str, uri: str) -> None:
        """Stop ``resource.updated`` for ``uri``; no-op if not subscribed."""
        state = self._servers.get(server_id)
        if state is None or uri not in state.subscriptions:
            return
        state.subscriptions.discard(uri)
        state.resubscribe.set()
        status = self._supervisor_of(server_id).status
        if status.state is ServerState.READY and status.era == "legacy":
            await self._request(server_id, lambda c: _send_subscribe(c, uri, subscribe=False))

    def _subscribable(self, server_id: str) -> ServerStatus:
        status = self._supervisor_of(server_id).status
        if status.state is not ServerState.READY:
            raise ServerUnavailableError(server_id, status.state)
        caps = status.capabilities
        if caps is None or caps.resources is None or not caps.resources.subscribe:
            raise CapabilityNotSupportedError(server_id, "resources.subscribe")
        return status

    async def _request(self, server_id: str, fn: Callable[[Client], Awaitable[T]]) -> T:
        """Run ``fn`` on the live Client; JSON-RPC errors → ``ServerRequestError`` (its
        Server-controlled ``data`` is kept but never rendered, Art. 5)."""
        supervisor = self._supervisor_of(server_id)
        try:
            async with supervisor.lease() as client:
                return await fn(client)
        except MCPError as exc:
            error = exc.error
            raise ServerRequestError(server_id, error.code, error.message, error.data) from None
        except InputRequiredRoundsExceededError:
            raise InputRequestError(server_id, "input_rounds_exceeded") from None

    # ------------------------------------------------------------------ Host-internal

    def _server(self, server_id: str) -> _ServerCatalog:
        state = self._servers.get(server_id)
        if state is None:
            state = self._servers[server_id] = _ServerCatalog()
        return state

    def forget(self, server_id: str) -> None:
        """Drop every entry of a Server removed from the registry."""
        if self._servers.pop(server_id, None) is not None:
            self._publish(server_id, ALL_LISTS)

    def message_handler(self, server_id: str) -> MessageHandlerFnT:
        """The supervisor ``message_handler`` of ``server_id``: routes legacy
        ``notifications/*/list_changed`` to the watcher (never requests inline: the handler
        runs on the session's receive path) and ``notifications/resources/updated`` to a
        ``resource.updated`` event (REQ-CAT-003, REQ-CAT-010)."""

        async def handler(message: IncomingMessage) -> None:
            state = self._servers.get(server_id)
            if state is None or state.era != "legacy":
                return
            if isinstance(message, types.ResourceUpdatedNotification):
                self._events.publish(
                    ResourceUpdated(server_id=server_id, uri=str(message.params.uri))
                )
                return
            if not isinstance(message, Exception) and (lists := _lists_of(message)):
                state.signal(lists)

        return handler

    async def on_ready(self, client: Client, status: ServerStatus) -> None:
        """Supervisor hook (Flow B steps 6-7): full refresh, then watch until cancelled;
        the entries go stale when it ends."""
        server_id = status.server_id
        state = self._server(server_id)
        state.era = status.era
        state.pending.clear()
        caps = status.capabilities
        log = ContextAdapter(_log, server_id=server_id)
        try:
            await self._refresh(server_id, client, _declared(caps))
            async with anyio.create_task_group() as tg:
                watched = _list_changed(caps)
                if status.era == "modern":
                    tg.start_soon(self._listen_loop, server_id, client, caps, watched, tg)
                else:
                    tg.start_soon(self._inbox_loop, server_id, client)
                    await self._resubscribe_legacy(server_id, client, log)
                self._start_ttl(server_id, client, set(_declared(caps)) - watched, tg)
        finally:
            state.era = None
            state.mark_stale()
            self._publish(server_id, ALL_LISTS)

    # ------------------------------------------------------------------ refresh

    async def _refresh(
        self, server_id: str, client: Client, lists: Iterable[CatalogList]
    ) -> tuple[CatalogList, ...]:
        """Fetch ``lists`` (each to its last page) and publish one ``catalog.changed``. A
        failing list is logged and keeps its previous entries."""
        state = self._server(server_id)
        done: list[CatalogList] = []
        log = ContextAdapter(_log, server_id=server_id)
        for kind in lists:
            try:
                items, ttl = await self._fetch_all(server_id, client, kind)
            except Exception as exc:
                log.warning("could not fetch %s (%s)", kind, type(exc).__name__)
                continue
            state.ttl_ms[kind] = ttl
            self._apply(server_id, state, kind, items)
            done.append(kind)
        if done:
            self._publish(server_id, tuple(done))
        return tuple(done)

    async def _fetch_all(
        self, server_id: str, client: Client, kind: CatalogList
    ) -> tuple[list[Any], int]:
        """Every item of one list, following ``nextCursor`` to the end (REQ-CAT-001); the
        first page's ``ttlMs`` (0 for legacy)."""
        fetch: Callable[..., Awaitable[Any]]
        if kind == "tools":
            fetch, attr = client.list_tools, "tools"
        elif kind == "resources":
            fetch, attr = client.list_resources, "resources"
        elif kind == "resource_templates":
            fetch, attr = client.list_resource_templates, "resource_templates"
        else:
            fetch, attr = client.list_prompts, "prompts"
        items: list[Any] = []
        ttl = 0
        cursor: str | None = None
        seen: set[str] = set()
        for page_no in range(MAX_PAGES):
            page = await fetch(cursor=cursor, cache_mode="refresh")
            items.extend(getattr(page, attr))
            if page_no == 0:
                ttl = int(getattr(page, "ttl_ms", 0) or 0)
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen:
                _log.warning("server %r repeated a %s cursor; list truncated", server_id, kind)
                break
            seen.add(cursor)
        else:
            _log.warning("server %r: %s exceeded %d pages; truncated", server_id, kind, MAX_PAGES)
        return items, ttl

    def _apply(
        self, server_id: str, state: _ServerCatalog, kind: CatalogList, items: list[Any]
    ) -> None:
        if kind == "tools":
            tools = _unique(server_id, "tool", items, lambda t: t.name)
            state.tools = tuple(
                sorted((_tool_entry(server_id, t) for t in tools), key=lambda e: e.name)
            )
        elif kind == "resources":
            resources = _unique(server_id, "resource", items, lambda r: str(r.uri))
            entries = (_resource_entry(server_id, r) for r in resources)
            state.resources = tuple(sorted(entries, key=lambda e: (e.name, e.uri)))
        elif kind == "resource_templates":
            templates = _unique(server_id, "resource template", items, lambda t: t.name)
            entries_t = (_template_entry(server_id, t) for t in templates)
            state.templates = tuple(sorted(entries_t, key=lambda e: e.name))
        else:
            prompts = _unique(server_id, "prompt", items, lambda p: p.name)
            entries_p = (_prompt_entry(server_id, p) for p in prompts)
            state.prompts = tuple(sorted(entries_p, key=lambda e: e.name))

    def _publish(self, server_id: str, lists: tuple[CatalogList, ...]) -> None:
        self._events.publish(CatalogChanged(server_id=server_id, lists=lists))

    # ------------------------------------------------------------------ watchers

    async def _inbox_loop(self, server_id: str, client: Client) -> None:
        """Legacy: re-fetch the lists named by routed ``list_changed`` notifications."""
        state = self._server(server_id)
        while True:
            await state.wake.wait()
            state.wake = anyio.Event()
            lists, state.pending = state.pending, set()
            await self._refresh(server_id, client, [k for k in ALL_LISTS if k in lists])

    async def _resubscribe_legacy(
        self, server_id: str, client: Client, log: ContextAdapter
    ) -> None:
        """Legacy subscriptions do not survive a new session: send them again."""
        for uri in sorted(self._server(server_id).subscriptions):
            try:
                await _send_subscribe(client, uri, subscribe=True)
            except Exception as exc:
                log.warning("could not re-subscribe to a resource (%s)", type(exc).__name__)

    async def _listen_loop(
        self,
        server_id: str,
        client: Client,
        caps: types.ServerCapabilities | None,
        watched: frozenset[CatalogList],
        tg: TaskGroup,
    ) -> None:
        """Modern: one ``subscriptions/listen`` stream; reopened with backoff (REQ-CAT-012)."""
        state = self._server(server_id)
        log = ContextAdapter(_log, server_id=server_id)
        failures = 0
        reopen = False
        ttl_started = False
        while True:
            resubscribe = state.resubscribe = anyio.Event()
            uris = sorted(state.subscriptions)
            if not watched and not uris:
                await resubscribe.wait()
                continue
            try:
                async with client.listen(
                    tools_list_changed="tools" in watched,
                    prompts_list_changed="prompts" in watched,
                    resources_list_changed="resources" in watched,
                    resource_subscriptions=uris,
                ) as sub:
                    opened_at = anyio.current_time()
                    if not ttl_started:
                        ttl_started = True
                        honored = _honored(sub.honored)
                        self._start_ttl(server_id, client, watched - honored, tg)
                    if reopen:
                        await self._refresh(server_id, client, _declared(caps))
                    reopen = False
                    changed = await self._consume(server_id, client, sub, resubscribe, log)
                # backoff resets only after a stable stream, so a Server that acks and closes
                # at once cannot force a reopen + full refetch every second
                if anyio.current_time() - opened_at >= LISTEN_STABLE_S:
                    failures = 0
                if changed:
                    continue  # subscriptions changed: reopen at once
            except (SubscriptionLost, MCPError, TimeoutError) as exc:
                log.info("listen stream could not be opened (%s)", type(exc).__name__)
            delay = backoff_delay(failures, rng=self._rng)
            failures += 1
            reopen = True
            log.info("listen stream closed; reopening in %.1f s", delay)
            await self._sleep(delay)

    async def _consume(
        self,
        server_id: str,
        client: Client,
        sub: Any,
        resubscribe: anyio.Event,
        log: ContextAdapter,
    ) -> bool:
        """Handle events until the stream ends (``False``) or ``resubscribe`` (``True``)."""
        interrupted = False
        async with anyio.create_task_group() as tg:

            async def until() -> None:
                nonlocal interrupted
                await resubscribe.wait()
                interrupted = True
                tg.cancel_scope.cancel()

            tg.start_soon(until)
            try:
                async for event in sub:
                    if isinstance(event, listen_events.ResourceUpdated):
                        self._events.publish(ResourceUpdated(server_id=server_id, uri=event.uri))
                    elif lists := _lists_of(event):
                        await self._refresh(server_id, client, lists)
            except (SubscriptionLost, MCPError) as exc:  # caught here: no ExceptionGroup
                log.info("listen stream lost (%s)", type(exc).__name__)
            tg.cancel_scope.cancel()
        return interrupted

    def _start_ttl(
        self, server_id: str, client: Client, lists: Iterable[CatalogList], tg: TaskGroup
    ) -> None:
        for kind in sorted(lists):
            tg.start_soon(self._ttl_loop, server_id, client, kind)

    async def _ttl_loop(self, server_id: str, client: Client, kind: CatalogList) -> None:
        """REQ-CAT-013 (SHOULD): re-fetch an unwatched list when its ``ttlMs`` elapses, at
        most every ``TTL_FLOOR_S``; stops once the Server returns no TTL."""
        state = self._server(server_id)
        while (ttl := state.ttl_ms.get(kind, 0)) > 0:
            await self._sleep(max(ttl / 1000.0, TTL_FLOOR_S))
            if not await self._refresh(server_id, client, (kind,)):
                return


def _honored(honored: types.SubscriptionFilter | None) -> frozenset[CatalogList]:
    if honored is None:
        return frozenset()
    kinds: set[CatalogList] = set()
    if honored.tools_list_changed:
        kinds.add("tools")
    if honored.resources_list_changed:
        kinds |= {"resources", "resource_templates"}
    if honored.prompts_list_changed:
        kinds.add("prompts")
    return frozenset(kinds)


async def _send_subscribe(client: Client, uri: str, *, subscribe: bool) -> None:
    """Legacy ``resources/subscribe`` / ``resources/unsubscribe`` (2025-11-25; removed in
    2026-07-28, where the listen stream carries subscriptions)."""
    params = types.SubscribeRequestParams(uri=uri)
    request: Any = (
        types.SubscribeRequest(params=params)
        if subscribe
        else types.UnsubscribeRequest(params=types.UnsubscribeRequestParams(uri=uri))
    )
    await client.session.send_request(request, types.EmptyResult)
