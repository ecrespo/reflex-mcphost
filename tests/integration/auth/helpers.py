"""Host-level helpers for the auth integration tests (F4-07, F4-08)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio

from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.host import Host
from tests.integration.test_host import Setup

S = ServerState
TIMEOUT = 20.0
SERVER = "remote"


def http_table(url: str, *, mode: str | None = None, **oauth: Any) -> str:
    lines = ['transport = "http"', f"url = {json.dumps(url)}"]
    if mode is not None:
        lines.append(f"mode = {json.dumps(mode)}")
    if oauth:
        pairs = ", ".join(f"{k} = {json.dumps(v)}" for k, v in oauth.items())
        lines.append(f"oauth = {{ {pairs} }}")
    return "\n".join(lines)


@asynccontextmanager
async def host_for(setup: Setup, url: str, **kwargs: Any) -> AsyncIterator[Host]:
    host = setup.host(setup.config({SERVER: http_table(url, **kwargs)}))
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            yield host


async def wait_state(
    host: Host, *states: ServerState, oauth_as: Any = None, limit_s: float = TIMEOUT
) -> ServerStatus:
    """Wait for one of ``states``; on timeout fail with :func:`stall_report` (pass the
    fixture AS as ``oauth_as`` to include its request journal)."""
    supervisor = host.connections.supervisor(SERVER)
    try:
        with anyio.fail_after(limit_s):
            return await supervisor.wait_for(*states)
    except TimeoutError as exc:
        raise AssertionError(stall_report(supervisor, states, oauth_as, limit_s)) from exc


_STACK_ROOTS = ("reflex_mcphost", "mcp", "httpx", "httpcore", "anyio", "tests")


def _await_chain(coro: Any) -> list[Any]:
    """Frames from a task's coroutine down to where it is suspended (``cr_await`` chain;
    ``Task.get_stack`` gives only the outermost frame of a suspended coroutine)."""
    frames = []
    for _ in range(200):  # bounded: a malformed chain never loops forever
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None)
        frame = frame or getattr(coro, "ag_frame", None)
        if frame is None:
            break
        frames.append(frame)
        nxt = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None)
        coro = nxt or getattr(coro, "ag_await", None)
        if coro is None:
            break
    return frames


def _task_stacks() -> list[str]:
    """Where every pending asyncio task is parked (innermost frames of interest only)."""
    import asyncio
    import os

    lines: list[str] = []
    for task in asyncio.all_tasks():
        frames = [
            f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno} {f.f_code.co_name}"
            for f in _await_chain(task.get_coro())
            if any(f"{os.sep}{root}" in f.f_code.co_filename for root in _STACK_ROOTS)
        ]
        lines.append(f"  {task.get_name()}: {' > '.join(frames[-4:]) or '(no frame of interest)'}")
    return sorted(lines)


def stall_report(
    supervisor: Any, states: tuple[ServerState, ...], oauth_as: Any, timeout: float
) -> str:
    """Diagnostics for a ``wait_state`` timeout: the Server's status, the Host log tail,
    the fixture AS journal (endpoint, method and issuer only — never a value) and the
    pending tasks' stacks. Built defensively: a failure here never hides the timeout."""
    wanted = ", ".join(str(s.value) for s in states)
    parts = [f"waited {timeout:g} s for {wanted}"]
    try:
        st = supervisor.status
        parts.append(
            f"status: state={st.state.value} reason={st.reason} "
            f"failures={getattr(st, 'consecutive_failures', None)} since={st.since.isoformat()}"
        )
        tail = supervisor.logs.snapshot()[-15:]
        parts.append("host log tail:")
        parts.extend(f"  {e.level} {e.logger}: {e.text}" for e in tail)
        if oauth_as is not None:
            parts.append("fixture AS journal (last 20):")
            parts.extend(
                f"  {r.get('endpoint')} {r.get('method', '')} {r.get('issuer', '')}".rstrip()
                for r in list(oauth_as.journal)[-20:]
            )
        parts.append("pending tasks:")
        parts.extend(_task_stacks())
    except Exception as exc:  # diagnostics must never mask the timeout itself
        parts.append(f"(stall report incomplete: {type(exc).__name__})")
    return "\n".join(parts)


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def replace_param(url: str, name: str, value: str | None) -> str:
    parts = urlsplit(url)
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    if value is None:
        params.pop(name, None)
    else:
        params[name] = value
    from urllib.parse import urlencode

    return parts._replace(query=urlencode(params)).geturl()


async def sign_in(host: Host, browser: Any) -> None:
    """Park, authorize through the fixture browser, wait until ``ready``."""
    await wait_state(host, S.AUTH_REQUIRED)
    flow = await host.connections.authorize(SERVER)
    assert flow.authorization_url is not None
    await browser.open(flow.authorization_url)
    with anyio.fail_after(TIMEOUT):
        await flow.wait()
    await wait_state(host, S.READY)


async def clear_journal(oauth_as: Any, *, quiet_s: float = 0.5, limit_s: float = 5.0) -> None:
    """Clear the fixture AS journal once a closed Host's in-flight requests have landed.

    A Host that just closed may leave requests the protected fixture has received but not
    yet introspected (seen on Windows runners); recorded after a plain ``clear()`` they
    would look like the next Host presenting an old token. The journal is written from the
    fixture's own thread, so there is no event to wait on: wait until it has not grown for
    ``quiet_s`` (at most ``limit_s``), then clear it.
    """
    with anyio.move_on_after(limit_s):
        seen, quiet_since = len(oauth_as.journal), anyio.current_time()
        while anyio.current_time() - quiet_since < quiet_s:
            await anyio.sleep(0.05)
            if len(oauth_as.journal) != seen:
                seen, quiet_since = len(oauth_as.journal), anyio.current_time()
    oauth_as.journal.clear()
