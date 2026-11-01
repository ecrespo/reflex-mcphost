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
    InvocationFinished,
    RegistryChanged,
    ServerLog,
    ServerStateChanged,
)
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.prompter import ElicitationRequest, SamplingResponseReview
from reflex_mcphost.ui import actions, models
from reflex_mcphost.ui import prompter as prompter_mod
from reflex_mcphost.ui.install import HostRuntime
from reflex_mcphost.ui.prompter import PromptAnswerError, PromptChange, PromptRegistry

if TYPE_CHECKING:
    from reflex_mcphost.core.connection.stderr import LogEntry
    from reflex_mcphost.core.host import Host

__all__ = [
    "CARD_FIELD",
    "CATALOG_TABS",
    "WATCHERS",
    "Commit",
    "HostState",
    "HostView",
    "Sink",
    "WatcherRegistry",
    "apply_events",
    "banner_text",
    "committer",
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

    async def invocation_finished(self, invocation_id: str) -> None: ...


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
        elif isinstance(event, InvocationFinished):
            await sink.invocation_finished(event.invocation_id)


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

    def __init__(
        self,
        host: Host,
        commit: Commit,
        prompts: PromptRegistry | None = None,
        *,
        on_finished: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._host = host
        self._commit = commit
        self._prompts = prompts
        self._on_finished = on_finished
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

    async def invocation_finished(self, invocation_id: str) -> None:
        """``invocation.finished``: the tab's Audit view is refreshed (F6-07)."""
        if self._on_finished is not None:
            await self._on_finished()


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
    view: Callable[..., Sink] = HostView,
    on_finished: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one tab's watcher until cancelled; a no-op if ``token`` already has one.
    ``prompts`` defaults to the process-wide ``PROMPTS``; ``on_finished`` (passed to the
    view) runs on every ``invocation.finished``."""
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
                sink = (
                    view(host, commit, registry)
                    if on_finished is None
                    else view(host, commit, registry, on_finished=on_finished)
                )
                await sink.refresh_all()
                await apply_events(source, sink)
        finally:
            _log.info("watcher stopped (%s)", _suffix(token))


# ------------------------------------------------------------------ Reflex state


def committer(state: Any) -> Commit:
    """A ``Commit`` writing into ``state`` under its lock (a background event's ``self``)."""

    async def commit(fields: dict[str, Any]) -> None:
        async with state:
            for name, value in fields.items():
                setattr(state, name, value)

    return commit


def _running_host() -> Host | None:
    try:
        return HostRuntime.host
    except RuntimeError:
        return None


NOT_RUNNING = "The Host is not running."
URL_REFUSED = "The Server's URL is not an http(s) address; it was not opened (declined)."
AUTH_URL_REFUSED = "The authorization URL is not an http(s) address; it was not opened."
CARD_FIELD = "__card_id__"
"""Hidden form field carrying a prompt card's id: a card form's ``on_submit`` is compiled
outside the cards' ``rx.foreach``, so it cannot take the loop's card as an argument."""


def _card_form(form_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(card id, the form values without CARD_FIELD)``; ``""`` when the id is missing."""
    values = dict(form_data)
    card_id = values.pop(CARD_FIELD, "")
    return (card_id if isinstance(card_id, str) else ""), values


CATALOG_TABS = {
    "tools": "tools",
    "resources": "resources",
    "templates": "resource_templates",
    "prompts": "catalog_prompts",
}
"""Catalog tab → ``HostState`` field."""


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
    elicitation_errors: dict[str, dict[str, str]] = {}  # noqa: RUF012 - as above
    editing: list[str] = []  # noqa: RUF012 - as above
    # Servers view (F6-04)
    server_filter: str = ""
    server_selected: str = ""
    server_notice: str = ""
    # Catalog view (F6-05)
    catalog_tab: str = "tools"
    catalog_filter: str = ""
    catalog_selected: dict[str, Any] = {}  # noqa: RUF012 - as above
    json_mode: bool = False
    form_errors: dict[str, str] = {}  # noqa: RUF012 - as above
    invocation: dict[str, Any] = {}  # noqa: RUF012 - as above
    read_result: dict[str, Any] = {}  # noqa: RUF012 - as above
    # Permissions and Audit views (F6-07)
    rules: list[dict[str, Any]] = []  # noqa: RUF012 - as above
    rule_filter: str = ""
    permissions_notice: str = ""
    audit: list[dict[str, Any]] = []  # noqa: RUF012 - as above
    audit_filters: dict[str, str] = {}  # noqa: RUF012 - as above
    audit_cursor: str = ""
    audit_notice: str = ""
    audit_loaded: bool = False

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

        commit = committer(self)

        async def on_finished() -> None:
            async with self:
                filters = dict(self.audit_filters) if self.audit_loaded else None
            host = _running_host()
            if host is not None:
                await actions.refresh_audit(host, filters, commit)

        await watch_host(self.router.session.client_token, commit, on_finished=on_finished)

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

    # ------------------------------------------------------------------ prompt cards (F6-06)

    def _pending(self, request_id: str, kind: type[Any]) -> Any:
        prompt = prompter_mod.PROMPTS.get(request_id)
        if prompt is None or not isinstance(prompt.payload, kind):
            self.prompt_error = "This prompt is no longer pending."
            return None
        return prompt.payload

    def _resolved(self, request_id: str, answer: object) -> bool:
        self.editing = [i for i in self.editing if i != request_id]
        self.elicitation_errors = {
            k: v for k, v in self.elicitation_errors.items() if k != request_id
        }
        if not prompter_mod.PROMPTS.resolve(request_id, answer):
            self.prompt_error = "This prompt was already answered."
            return False
        self.prompt_error = ""
        return True

    @rx.event
    def submit_elicitation(self, request_id: str, form_data: dict[str, Any]) -> None:
        """Submit an elicitation form (REQ-HSH-010). Content that fails the requested
        schema stays on the card as field errors and nothing is sent (REQ-HSH-013)."""
        self._answer_elicitation(request_id, "accept", form_data)

    @rx.event
    def submit_elicitation_form(self, form_data: dict[str, Any]) -> None:
        """``submit_elicitation`` for the card form: the card id is in ``CARD_FIELD``."""
        request_id, values = _card_form(form_data)
        if request_id:
            self._answer_elicitation(request_id, "accept", values)

    @rx.event
    def answer_elicitation(self, request_id: str, action: str) -> Any:
        """Decline / Cancel (REQ-HSH-011), or accept a url-mode elicitation: only then
        is its URL opened, with an external redirect (REQ-HSH-015)."""
        return self._answer_elicitation(request_id, action, None)

    def _answer_elicitation(self, request_id: str, action: str, raw: object) -> Any:
        request: ElicitationRequest | None = self._pending(request_id, ElicitationRequest)
        if request is None:
            return None
        try:
            response, errors = models.elicitation_answer(request, action, raw)
        except ValueError as exc:
            self.prompt_error = str(exc)
            return None
        if response is None:
            self.elicitation_errors = {**self.elicitation_errors, request_id: errors}
            return None
        refused = (
            request.mode == "url" and action == "accept" and not models.is_openable_url(request.url)
        )
        if not self._resolved(request_id, response):
            return None
        if refused:  # declined by elicitation_answer; the URL is never echoed
            self.prompt_error = URL_REFUSED
            return None
        if request.mode == "url" and action == "accept" and request.url:
            return rx.redirect(request.url, is_external=True)
        return None

    @rx.event
    def toggle_edit(self, request_id: str) -> None:
        """Show / hide the edit form of a sampling card."""
        if request_id in self.editing:
            self.editing = [i for i in self.editing if i != request_id]
        else:
            self.editing = [*self.editing, request_id]

    @rx.event
    def approve_edited_sampling_form(self, form_data: dict[str, Any]) -> None:
        """``approve_edited_sampling`` for the card form (card id in ``CARD_FIELD``)."""
        request_id, values = _card_form(form_data)
        if request_id:
            self._approve_edited(request_id, values)

    @rx.event
    def approve_edited_sampling(self, request_id: str, form_data: dict[str, Any]) -> None:
        """Approve a sampling request once with edited message texts (REQ-HSH-002)."""
        self._approve_edited(request_id, form_data)

    def _approve_edited(self, request_id: str, form_data: dict[str, Any]) -> None:
        request: ConsentRequest | None = self._pending(request_id, ConsentRequest)
        if request is None:
            return
        targets = prompter_mod.consent_options(request)[1]
        target = self.chosen_targets.get(request_id, targets[0])
        try:
            answer = prompter_mod.sampling_answer(
                request, "allow_once", target, "once", edits=form_data
            )
        except PromptAnswerError as exc:
            self.prompt_error = str(exc)
            return
        self._resolved(request_id, answer)

    @rx.event
    def answer_review(self, request_id: str, approved: bool) -> None:
        """Approve or Reject a sampling completion (REQ-HSH-004)."""
        self._review(request_id, approved is True, None)

    @rx.event
    def submit_review_edit(self, request_id: str, form_data: dict[str, Any]) -> None:
        """Approve a sampling completion with an edited text (REQ-HSH-004)."""
        text = form_data.get("completion")
        self._review(request_id, True, text if isinstance(text, str) else None)

    @rx.event
    def submit_review_edit_form(self, form_data: dict[str, Any]) -> None:
        """``submit_review_edit`` for the card form (card id in ``CARD_FIELD``)."""
        request_id, values = _card_form(form_data)
        if request_id:
            text = values.get("completion")
            self._review(request_id, True, text if isinstance(text, str) else None)

    def _review(self, request_id: str, approved: bool, text: str | None) -> None:
        review: SamplingResponseReview | None = self._pending(request_id, SamplingResponseReview)
        if review is None:
            return
        try:
            verdict = prompter_mod.review_verdict(review, approved, text)
        except PromptAnswerError as exc:
            self.prompt_error = str(exc)
            return
        self._resolved(request_id, verdict)

    # ------------------------------------------------------------------ Servers (F6-04)

    @rx.var
    def servers_shown(self) -> list[dict[str, Any]]:
        """At most ``PAGE_LIMIT`` Servers rows matching the filter."""
        return models.page(self.servers, self.server_filter)[0]

    @rx.var
    def servers_more(self) -> int:
        return models.page(self.servers, self.server_filter)[1]

    @rx.var
    def server_logs(self) -> list[dict[str, Any]]:
        """The selected Server's last ``LOG_LIMIT`` log lines."""
        return self.logs.get(self.server_selected, [])[-models.LOG_LIMIT :]

    @rx.event
    def set_server_filter(self, value: str) -> None:
        self.server_filter = value

    @rx.event
    def select_server(self, server_id: str) -> None:
        """Show a Server's log stream: the buffered lines, then ``server.log`` events."""
        self.server_selected = server_id
        host = _running_host()
        if host is None:
            self.server_notice = NOT_RUNNING
            return
        try:
            lines = models.log_rows(host.connections.logs(server_id))
        except MCPHostError as exc:
            self.server_notice = str(exc)
            return
        self.logs = {**self.logs, server_id: lines}

    @rx.event(background=True)
    async def server_action(self, server_id: str, action: str) -> None:
        """Connect / disconnect / reconnect / sign out / enable / disable (REQ-UI-003,
        REQ-REG-010, REQ-AUTH-007)."""
        host = _running_host()
        notice = (
            NOT_RUNNING if host is None else await actions.server_action(host, action, server_id)
        )
        async with self:
            self.server_notice = notice

    @rx.event(background=True)
    async def reload_servers(self) -> None:
        """Re-read every config source (REQ-REG-013)."""
        host = _running_host()
        notice = NOT_RUNNING if host is None else await actions.reload_servers(host)
        async with self:
            self.server_notice = notice

    @rx.event(background=True)
    async def set_log_level(self, server_id: str, level: str) -> None:
        """*Deprecated, compatibility* (REQ-HSH-031)."""
        host = _running_host()
        notice = (
            NOT_RUNNING if host is None else await actions.set_log_level(host, server_id, level)
        )
        async with self:
            self.server_notice = notice

    @rx.event(background=True)
    async def authorize(self, server_id: str) -> Any:
        """Start the OAuth flow (REQ-AUTH-002) and open the authorization URL in a new tab
        with an external redirect; the URL is never put in a state var or a log
        (REQ-AUTH-008). The flow's outcome or reason code is shown."""
        host = _running_host()
        if host is None:
            async with self:
                self.server_notice = NOT_RUNNING
            return
        flow, notice = await actions.start_authorization(host, server_id)
        async with self:
            self.server_notice = notice
        if flow is None:
            return
        url = flow.authorization_url
        if url is not None and not flow.done:
            if not models.is_openable_url(url):  # never echoed (it carries `state`)
                async with self:
                    self.server_notice = AUTH_URL_REFUSED
                return
            yield rx.redirect(url, is_external=True)
        outcome = await actions.authorization_outcome(flow)
        async with self:
            self.server_notice = outcome

    # ------------------------------------------------------------------ Catalog (F6-05)

    def _catalog_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = getattr(self, CATALOG_TABS.get(self.catalog_tab, "tools"))
        return rows

    @rx.var(deps=list(CATALOG_TABS.values()))
    def catalog_shown(self) -> list[dict[str, Any]]:
        """At most ``PAGE_LIMIT`` rows of the current tab matching the filter (REQ-NFR-004).

        The row fields are explicit ``deps``: the tab picks its field with ``getattr``,
        which Reflex's automatic dependency tracking does not see."""
        return models.page(self._catalog_rows(), self.catalog_filter)[0]

    @rx.var(deps=list(CATALOG_TABS.values()))
    def catalog_more(self) -> int:
        return models.page(self._catalog_rows(), self.catalog_filter)[1]

    @rx.event
    def set_catalog_tab(self, tab: str) -> None:
        self.catalog_tab = tab if tab in CATALOG_TABS else "tools"

    @rx.event
    def set_catalog_filter(self, value: str) -> None:
        self.catalog_filter = value

    @rx.event
    def toggle_json_mode(self) -> None:
        """Edit the arguments as one JSON object instead of the generated form."""
        self.json_mode = not self.json_mode

    @rx.event
    def select_entry(self, server_id: str, entry_id: str) -> None:
        """Open a catalog entry of the current tab: a tool's details and invoke form, a
        resource, a template's variables form or a prompt's arguments form."""
        self.form_errors = {}
        self.invocation = {}
        self.read_result = {}
        self.json_mode = False
        tab = self.catalog_tab
        if tab == "tools":
            host = _running_host()
            if host is None:
                self.catalog_selected = {}
                self.form_errors = {"": NOT_RUNNING}
                return
            try:
                entry = host.catalog.get_tool(entry_id)
            except (MCPHostError, ValueError) as exc:
                self.catalog_selected = {}
                self.form_errors = {"": str(exc)}
                return
            self.catalog_selected = {"tab": tab, **models.tool_detail(entry)}
            return
        row = next(
            (
                r
                for r in self._catalog_rows()
                if r.get("server_id") == server_id and r.get("id") == entry_id
            ),
            None,
        )
        if row is None:
            self.catalog_selected = {}
            return
        selected: dict[str, Any] = {"tab": tab, **row}
        if tab == "templates":
            selected["variables"] = models.template_variables(row["uri_template"])
        self.catalog_selected = selected

    @rx.event(background=True)
    async def invoke_tool(self, form_data: dict[str, Any]) -> None:
        """Invoke the selected tool as ``human:ui`` through the Invoker (REQ-UI-004,
        Art. 3); progress, Cancel and the result panel follow the handle."""
        fqn = self.catalog_selected.get("fqn")
        host = _running_host()
        commit = committer(self)
        if host is None or not isinstance(fqn, str):
            await commit({"form_errors": {"": NOT_RUNNING if host is None else "no tool"}})
            return
        await actions.invoke(host, fqn, form_data, commit, token=self.router.session.client_token)

    @rx.event(background=True)
    async def cancel_invocation(self) -> None:
        """Cancel this tab's running invocation (REQ-INV-004)."""
        await actions.cancel_invocation(self.router.session.client_token)

    @rx.event(background=True)
    async def read_selected(self, form_data: dict[str, Any]) -> None:
        """Read the selected resource / template, or get the selected prompt."""
        selected = dict(self.catalog_selected)
        host = _running_host()
        if host is None:
            view: models.Row = {"kind": "", "title": "", "blocks": [], "error": NOT_RUNNING}
        elif selected.get("tab") == "resources":
            view = await actions.read_resource(host, selected["server_id"], selected["uri"])
        elif selected.get("tab") == "templates":
            view = await actions.read_template(host, selected["fqn"], form_data)
        elif selected.get("tab") == "prompts":
            view = await actions.get_prompt(host, selected["fqn"], form_data)
        else:
            return
        async with self:
            self.read_result = view

    # ------------------------------------------------------------------ Permissions (F6-07)

    @rx.var
    def rules_shown(self) -> list[dict[str, Any]]:
        return models.page(self.rules, self.rule_filter, keys=models.RULE_SEARCH_KEYS)[0]

    @rx.var
    def rules_more(self) -> int:
        return models.page(self.rules, self.rule_filter, keys=models.RULE_SEARCH_KEYS)[1]

    @rx.event
    def load_rules(self) -> None:
        """Persistent and session rules (REQ-CNS-013)."""
        host = _running_host()
        if host is None:
            self.permissions_notice = NOT_RUNNING
            return
        self.rules = actions.rule_view(host)

    @rx.event
    def set_rule_filter(self, value: str) -> None:
        self.rule_filter = value

    @rx.event
    async def update_rule(self, rule_id: str, effect: str, lifetime: str) -> None:
        """Edit a rule's effect / lifetime; applied to the next evaluation (REQ-CNS-013)."""
        host = _running_host()
        if host is None:
            self.permissions_notice = NOT_RUNNING
            return
        self.permissions_notice = await actions.update_rule(host, rule_id, effect, lifetime)
        self.rules = actions.rule_view(host)

    @rx.event
    async def revoke_rule(self, rule_id: str) -> None:
        """Delete a rule; the next evaluation no longer sees it (REQ-CNS-013)."""
        host = _running_host()
        if host is None:
            self.permissions_notice = NOT_RUNNING
            return
        self.permissions_notice = await actions.revoke_rule(host, rule_id)
        self.rules = actions.rule_view(host)

    # ------------------------------------------------------------------ Audit (F6-07)

    @rx.event
    async def load_audit(self) -> None:
        """The first Audit page for the current filters, newest first (REQ-INV-011)."""
        await self._load_audit()

    async def _load_audit(self) -> None:
        self.audit_loaded = True
        host = _running_host()
        if host is None:
            self.audit_notice = NOT_RUNNING
            return
        self.audit, self.audit_cursor, self.audit_notice = await actions.audit_page(
            host, self.audit_filters
        )

    @rx.event
    async def set_audit_filter(self, key: str, value: str) -> None:
        """Change one REQ-INV-011 filter and reload the first page."""
        if key not in models.AUDIT_FILTERS:
            return
        self.audit_filters = {**self.audit_filters, key: value}
        await self._load_audit()

    @rx.event
    async def load_more_audit(self) -> None:
        """Append the next page ("Load more" follows the cursor)."""
        host = _running_host()
        if host is None or not self.audit_cursor:
            return
        rows, cursor, notice = await actions.audit_page(host, self.audit_filters, self.audit_cursor)
        self.audit = [*self.audit, *rows]
        self.audit_cursor, self.audit_notice = cursor, notice
