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


async def wait_state(host: Host, *states: ServerState) -> ServerStatus:
    supervisor = host.connections.supervisor(SERVER)
    with anyio.fail_after(TIMEOUT):
        return await supervisor.wait_for(*states)


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
