"""Test rig for the Reflex UI layer: an ``rx.App`` served in-process without a frontend.

``new_app`` creates an ``rx.App`` in a fork of the current Reflex registration context
(Reflex allows one App per context; the fork keeps every registered state). ``asgi_of``
builds the backend ASGI app of an ``rx.App`` with the page compiler stubbed out
(``App._compile`` writes ``.web/`` and takes seconds; nothing here needs the frontend) and
the working directory moved to ``tmp_path`` (Reflex writes ``.states/`` there).
``lifespan`` drives Reflex's own lifespan context (``App._run_lifespan_tasks``) without a
server. ``ws_call`` sends one raw ASGI websocket handshake and records what the app sent.
``published`` serves a Host through ``HostRuntime``; ``run_background`` runs a background
event handler's function on a plain state through ``Proxy`` (no Reflex runtime).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterator, MutableMapping
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest

rx = pytest.importorskip("reflex")

from starlette.applications import Starlette  # noqa: E402 - after the importorskip
from starlette.types import ASGIApp  # noqa: E402

Message = MutableMapping[str, Any]


def new_app(**kwargs: Any) -> Any:
    """A fresh ``rx.App(**kwargs)`` in its own fork of the registration context."""
    from reflex_base.registry import RegistrationContext

    with RegistrationContext.ensure_context().fork():
        return rx.App(**kwargs)


def asgi_of(app: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ASGIApp:
    """The backend ASGI app of ``app`` (api_transformer applied), without compiling pages."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rx.App, "_compile", lambda self, **kwargs: None)
    asgi: ASGIApp = app()
    return asgi


@asynccontextmanager
async def lifespan(app: Any) -> AsyncIterator[None]:
    """Reflex's lifespan context for ``app`` (start-up on enter, shutdown on exit)."""
    async with app._run_lifespan_tasks(Starlette()):
        yield


async def ws_call(asgi: ASGIApp, headers: dict[str, str], path: str = "/_event/") -> list[Message]:
    """One websocket handshake with ``headers``; returns the messages the app sent."""
    sent: list[Message] = []
    pending: list[Message] = [{"type": "websocket.connect"}]
    done = anyio.Event()

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        await done.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] in ("websocket.close", "websocket.accept"):
            done.set()

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"EIO=4&transport=websocket",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "scheme": "ws",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 50000),
        "subprotocols": [],
    }
    with anyio.fail_after(5):
        await asgi(scope, receive, send)
    return sent


@contextmanager
def published(host: object) -> Iterator[object]:
    """``HostRuntime`` serving ``host`` (a fake or a real Host) for the state handlers."""
    from reflex_mcphost.ui.install import HostRuntime

    HostRuntime._begin()
    HostRuntime._publish(host)  # type: ignore[arg-type]
    try:
        yield host
    finally:
        HostRuntime._end()


class Proxy:
    """Stands in for Reflex's ``StateProxy`` so a background handler's raw function runs
    on a plain state: ``async with self`` is a no-op, attributes go to the state."""

    def __init__(self, state: Any) -> None:
        object.__setattr__(self, "_state", state)

    async def __aenter__(self) -> Proxy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._state, name, value)


async def run_background(handler: Any, state: Any, *args: Any) -> list[Any]:
    """Run a background handler's function on ``state``; the events it yielded."""
    result = handler.fn(Proxy(state), *args)
    if inspect.isasyncgen(result):
        return [event async for event in result]
    value = await result
    return [] if value is None else [value]


def redirect_target(spec: Any) -> tuple[str, bool]:
    """``(url, is_external)`` of an ``rx.redirect`` event spec."""
    args = {str(name): value._var_value for name, value in spec.args}
    assert spec.handler.fn.__qualname__ == "_redirect"
    return args["path"], args["external"]
