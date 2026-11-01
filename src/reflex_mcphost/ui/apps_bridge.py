"""MCP App Views of the Host UI: one View per tab, adapted to Reflex state (F7-05; API §7,
TD Flow F, DD-012). Reflex-free: ``HostState`` handlers and the tab's watcher call it.

``VIEWS`` is the process-local registry ``{client_token: ViewSession}`` (never in Reflex
state). A ``ViewSession`` holds the core ``AppBridge`` of one View, its outbox and its
pending ``ui/open-link`` confirmation; its ports bind the bridge to the running Host:

- ``invoke_tool`` → ``host.invoker.call("<server_id>::<name>", arguments,
  consumer=Consumer.app(server_id))`` (Art. 3: consent, App scope and audit in the Invoker);
- ``read_resource`` → ``host.catalog.read_resource(server_id, uri,
  consumer=Consumer.app(server_id))`` (the bridge caps the result at 5 MiB);
- ``log`` → ``host.connections.append_log(server_id, entry)`` (``method → outcome`` only);
- ``confirm_link`` → a confirmation in the frame, answered by Approve / Decline
  (``answer_link``); the URL is re-checked with ``ui.models.is_openable_url``;
- ``send`` → the outbox: ``{seq, message}`` entries the relay posts to the proxy window and
  acknowledges (``ack``), delta D4 — data only, never script.

The tab's watcher (``serve_tab``, chained from the page ``on_load``, so Reflex cancels it
when the tab disconnects or navigates) runs the tab's current session: it enters the bridge,
commits ``app_view`` / ``app_outbox`` on every change and tears the View down
(``server_removed``) when ``registry.changed`` removes its Server. A cancelled watcher
discards the View without teardown. ``open`` replaces a tab's View (``replaced``),
``close`` closes it (``closed``), ``close_all`` tears every View down at Host shutdown
(``host_closing``, ≤ 2 s overall). Arguments, results, HTML and messages stay in memory
and are never logged (Art. 5, Data Model §8).
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Final

import anyio

from reflex_mcphost.core.apps.bridge import AppBridge, AppPorts, AppSeed
from reflex_mcphost.core.apps.constants import TEARDOWN_TIMEOUT_S
from reflex_mcphost.core.apps.resource import NotRendered
from reflex_mcphost.core.events import RegistryChanged
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import actions, models
from reflex_mcphost.ui.proxy import view_url

__all__ = [
    "EMPTY",
    "HOST_CONTEXT",
    "VIEWS",
    "ViewRegistry",
    "ViewSession",
    "render_view",
]

Commit = Callable[[dict[str, Any]], Awaitable[None]]
EMPTY: Final[dict[str, Any]] = {"app_view": {}, "app_outbox": []}
"""The tab's state fields when it shows no View."""
HOST_CONTEXT: Final[Mapping[str, Any]] = {"theme": "light", "platform": "web"}
"""``hostContext`` of ``ui/initialize`` (``displayMode`` is added by the bridge)."""
_HUMAN = Consumer.human()
_FINISH_WAIT_S = 1.0
"""How long ``close`` waits for the tab's watcher to leave the closed session."""
_log = get_logger(__name__)


def _host_info() -> dict[str, str]:
    from reflex_mcphost import __version__

    return {"name": "reflex-mcphost", "version": __version__}


class ViewSession:
    """One View of one tab: the core bridge, its outbox and the link confirmation."""

    def __init__(
        self,
        *,
        host: Any,
        token: str,
        server_id: str,
        plan: actions.ViewPlan,
        arguments: Mapping[str, Any],
        result: Any,
        proxy_origin: str,
        teardown_timeout_s: float = TEARDOWN_TIMEOUT_S,
    ) -> None:
        self.token = token
        self.server_id = server_id
        self.view_id = secrets.token_urlsafe(12)
        self.proxy_origin = proxy_origin
        self.src = view_url(proxy_origin, self.view_id)
        self._csp = plan.csp
        self._border = plan.resource.prefers_border
        self._height: int | None = None
        self._outbox: list[dict[str, Any]] = []
        self._seq = 0
        self._link: str = ""
        self._link_answered: anyio.Event | None = None
        self._link_approved = False
        self._changed = anyio.Event()
        self._closed = anyio.Event()
        self.finished = anyio.Event()
        self.started = False
        self._registry: ViewRegistry | None = None
        self.bridge = AppBridge(
            server_id=server_id,
            view_id=self.view_id,
            seed=AppSeed(arguments=dict(arguments), result=result),
            html=plan.resource.html,
            host_info=_host_info(),
            host_context=HOST_CONTEXT,
            csp=plan.csp,
            ports=self._ports(host),
            teardown_timeout_s=teardown_timeout_s,
        )

    # ------------------------------------------------------------------ ports

    def _ports(self, host: Any) -> AppPorts:
        server_id = self.server_id
        consumer = Consumer.app(server_id)

        async def invoke_tool(name: str, arguments: dict[str, Any]) -> Any:
            return await host.invoker.call(f"{server_id}::{name}", arguments, consumer=consumer)

        async def read_resource(uri: str) -> Any:
            return await host.catalog.read_resource(server_id, uri, consumer=consumer)

        def log(entry: Any) -> None:
            host.connections.append_log(server_id, entry)

        return AppPorts(
            invoke_tool=invoke_tool,
            read_resource=read_resource,
            confirm_link=self._confirm_link,
            log=log,
            send=self._send,
            size_changed=self._size_changed,
        )

    async def _send(self, message: str) -> None:
        self._seq += 1
        self._outbox.append({"seq": self._seq, "message": message})
        self._notify()

    def _size_changed(self, height: int) -> None:
        self._height = height
        self._notify()

    async def _confirm_link(self, url: str) -> bool:
        """``ui/open-link``: show the full URL and wait for Approve / Decline (REQ-APP-015)."""
        if not models.is_openable_url(url) or self.closed or self._link_answered is not None:
            return False
        answered = anyio.Event()
        self._link, self._link_answered, self._link_approved = url, answered, False
        self._notify()
        try:
            await answered.wait()
            return self._link_approved
        finally:
            self._link, self._link_answered = "", None
            self._notify()

    # ------------------------------------------------------------------ the tab's side

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def link(self) -> str:
        """The URL awaiting confirmation (``""`` when none)."""
        return self._link

    def _notify(self) -> None:
        self._changed.set()
        self._changed = anyio.Event()

    def fields(self) -> dict[str, Any]:
        """The tab's ``app_view`` / ``app_outbox`` state fields for this View."""
        row = models.app_view_row(
            status="rendered",
            view_id=self.view_id,
            src=self.src,
            origin=self.proxy_origin,
            csp=self._csp,
            border=self._border,
            height=self._height,
            link=self._link,
        )
        return {"app_view": row, "app_outbox": list(self._outbox)}

    async def receive(self, data: str) -> None:
        """One message the relay accepted from this View's proxy window."""
        await self.bridge.receive(data)

    def ack(self, seq: int) -> None:
        """The relay posted every outbox entry up to ``seq``: drop them."""
        kept = [e for e in self._outbox if e["seq"] > seq]
        if len(kept) != len(self._outbox):
            self._outbox = kept
            self._notify()

    def answer_link(self, approved: bool) -> str | None:
        """Answer the pending ``ui/open-link``; the URL to open when approved and still
        openable, else ``None``."""
        answered, url = self._link_answered, self._link
        if answered is None:
            return None
        self._link_approved = approved is True and models.is_openable_url(url)
        answered.set()
        return url if self._link_approved else None

    def _release_link(self) -> None:
        if self._link_answered is not None:
            self._link_approved = False
            self._link_answered.set()

    async def close(self, reason: str) -> None:
        """``ui/resource-teardown {reason}``, wait ≤ 2 s, cancel in-flight requests
        (REQ-APP-010); then the watcher leaves the session. Idempotent."""
        self._release_link()
        try:
            await self.bridge.teardown(reason)
        finally:
            self._closed.set()
            self._notify()
        if self.started:
            with anyio.move_on_after(_FINISH_WAIT_S):
                await self.finished.wait()

    async def run(self, commit: Commit, host: Any) -> None:
        """Run the View in the tab's watcher until it is closed (or the watcher is
        cancelled: the View is then discarded without teardown)."""
        if self.started:
            raise RuntimeError("a ViewSession runs once")
        self.started = True
        try:
            async with (
                self.bridge,
                host.events.subscribe() as events,
                anyio.create_task_group() as tg,
            ):
                tg.start_soon(self._watch_registry, events)
                while True:
                    changed = self._changed
                    await commit(self.fields())
                    if self.closed:
                        break
                    await changed.wait()
                tg.cancel_scope.cancel()
        finally:
            self._closed.set()
            self._release_link()

    async def _watch_registry(self, events: AsyncIterator[object]) -> None:
        async for event in events:
            if isinstance(event, RegistryChanged) and self.server_id in event.diff.removed:
                if self._registry is not None:
                    await self._registry.close_session(self, "server_removed")
                else:
                    await self.close("server_removed")
                return


class ViewRegistry:
    """The Views of this backend process: at most one current View per tab token."""

    def __init__(self) -> None:
        self._current: dict[str, ViewSession] = {}
        self._by_view: dict[str, ViewSession] = {}
        self._arrived: dict[str, anyio.Event] = {}

    def get(self, token: str) -> ViewSession | None:
        """The tab's current View."""
        return self._current.get(token)

    def _session(self, token: str, view_id: str) -> ViewSession | None:
        session = self._by_view.get(view_id)
        return session if session is not None and session.token == token else None

    async def open(self, token: str, session: ViewSession) -> None:
        """Make ``session`` the tab's View, tearing the previous one down (``replaced``)."""
        await self.close(token, "replaced")
        session._registry = self
        self._current[token] = session
        self._by_view[session.view_id] = session
        arrived = self._arrived.pop(token, None)
        if arrived is not None:
            arrived.set()

    async def close(self, token: str, reason: str) -> bool:
        """Tear the tab's View down with ``reason``; ``False`` if it has none."""
        session = self._current.get(token)
        if session is None:
            return False
        await self.close_session(session, reason)
        return True

    async def close_session(self, session: ViewSession, reason: str) -> None:
        if self._current.get(session.token) is session:
            del self._current[session.token]
        try:
            await session.close(reason)
        finally:
            if not session.started:
                self._by_view.pop(session.view_id, None)

    async def close_all(self, reason: str, timeout_s: float = TEARDOWN_TIMEOUT_S) -> None:
        """Tear every View down (Host shutdown: ``host_closing``), ≤ ``timeout_s`` overall."""
        sessions = list(self._current.values())
        if not sessions:
            return
        with anyio.move_on_after(timeout_s):
            async with anyio.create_task_group() as tg:
                for session in sessions:
                    tg.start_soon(self.close_session, session, reason)
        for session in sessions:  # whatever did not finish in time is discarded
            if self._current.get(session.token) is session:
                del self._current[session.token]

    async def receive(self, token: str, view_id: str, data: str) -> bool:
        """Route a relay message to its View (a closing one included); ``False`` if the
        View is unknown to this tab (dropped)."""
        session = self._session(token, view_id)
        if session is None or not isinstance(data, str):
            return False
        await session.receive(data)
        return True

    def ack(self, token: str, view_id: str, seq: int) -> None:
        session = self._session(token, view_id)
        if session is not None and isinstance(seq, int) and not isinstance(seq, bool):
            session.ack(seq)

    def answer_link(self, token: str, approved: bool) -> str | None:
        session = self._current.get(token)
        return session.answer_link(approved) if session is not None else None

    async def _next(self, token: str) -> ViewSession:
        while True:
            session = self._current.get(token)
            if session is not None and not session.started:
                return session
            arrived = self._arrived.setdefault(token, anyio.Event())
            try:
                await arrived.wait()
            finally:  # never leave an event behind (a cancelled watcher, another loop)
                if self._arrived.get(token) is arrived:
                    del self._arrived[token]

    async def serve_tab(self, token: str, commit: Commit, host: Any) -> None:
        """Run the tab's Views one after the other, until cancelled (the tab's watcher)."""
        while True:
            session = await self._next(token)
            try:
                await session.run(commit, host)
                if self._current.get(token) is None:
                    await commit(dict(EMPTY))
            finally:
                if self._current.get(token) is session:
                    del self._current[token]
                self._by_view.pop(session.view_id, None)
                session.finished.set()


VIEWS = ViewRegistry()
"""The MCP App Views of this backend process."""


async def render_view(
    host: Any,
    token: str,
    entry: Any,
    arguments: Mapping[str, Any],
    result: Any,
    commit: Commit,
    *,
    apps: Any,
    registry: ViewRegistry | None = None,
) -> ViewSession | None:
    """Flow F steps 1-3 after a Catalog invocation ended: a View for an allowed
    ``human:ui`` invocation of a tool with a UI resource (REQ-APP-001), a notice when MCP
    Apps are disabled or the resource cannot render (REQ-APP-013), nothing otherwise.
    ``registry`` defaults to ``VIEWS`` (looked up at call time)."""
    if not models.should_render(result, entry, _HUMAN):
        return None
    if not apps.enabled or not apps.proxy_origin:
        reason = apps.reason or "not_running"
        await commit({**EMPTY, "app_view": models.app_view_row(status="disabled", reason=reason)})
        return None
    plan = await actions.open_view(host, entry)
    if isinstance(plan, NotRendered):
        row = models.app_view_row(status="not_rendered", reason=plan.reason)
        await commit({**EMPTY, "app_view": row})
        return None
    session = ViewSession(
        host=host,
        token=token,
        server_id=entry.server_id,
        plan=plan,
        arguments=arguments,
        result=result,
        proxy_origin=apps.proxy_origin,
    )
    await (registry if registry is not None else VIEWS).open(token, session)
    _log.info("MCP App View opened for %s", entry.server_id)
    return session
