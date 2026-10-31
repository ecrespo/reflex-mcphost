"""Full secret-seeded redaction test (F4-09b; Constitution Art. 5, Plan Phase 4 Done).

Seeds known secrets — env values, static header values, a confidential OAuth client
secret, secret-like tool arguments and sampling text — and collects the ones the session
creates at runtime (authorization codes, PKCE verifiers, access/refresh tokens and client
secrets from the fixture AS journal; every ``state`` from the authorization URLs). It then
drives one realistic session on a modern and on a legacy protected Server: a failed
(tampered) authorization, a successful one, tool calls (one with a secret-like argument),
an in-session refresh, elicitation and sampling rounds, failures and sign-out. None of the
secrets may appear in:

- log records at DEBUG of every logger (``reflex_mcphost.*``, ``mcp.*``, ``httpx2``,
  ``httpcore2`` …) — the "browser" uses ``urllib`` in a worker thread, which never logs;
- any table/column of the SQLite database, nor the bytes of ``mcphost.db`` and its WAL;
- event payloads, the per-Server log rings, exception text/``repr`` and the ``repr`` of
  public objects (``ServerStatus``, ``InvocationResult``, ``AuthorizationFlow``, config).

The fallback ``secrets.json`` *is* the token store (DM §3, Art. 5), so tokens are expected
there; it is only required to be ``0600``.

All seeded sentinels match the gitleaks allowlist ``^(?:[a-z]+-)*(?:secret|sekret)-[a-z0-9-]+$``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import pytest

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.consent.models import ConsentDecision
from reflex_mcphost.core.db import DB_FILENAME
from reflex_mcphost.core.errors import AuthorizationError
from reflex_mcphost.core.events import HostEvent
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationResult
from reflex_mcphost.core.prompter import ElicitationResponse
from reflex_mcphost.core.secret_store import SECRETS_FILENAME
from reflex_mcphost.core.types import Consumer
from tests.fixtures.servers.auth_server import AuthServerState
from tests.integration.auth.conftest import (  # noqa: F401 - fixtures used by this module
    oauth_as,
    protected_legacy_http,
    protected_modern_http,
)
from tests.integration.auth.helpers import http_table, query, replace_param
from tests.integration.handlers.helpers import (
    FakeProvider,
    HandlerPrompter,
    modern_stdio_table,
    recording,
)
from tests.integration.test_host import TIMEOUT, Setup, setup  # noqa: F401 - fixture
from tests.integration.test_invoker import wait_tools, write_config

pytestmark = pytest.mark.anyio

S = ServerState
BOT = Consumer.agent("bot")
REMOTE, LOCAL, KEYED = "remote", "local", "keyed"

ENV_SECRET = "env-secret-seed-0001"  # noqa: S105 - test sentinel
HEADER_SECRET = "header-secret-seed-0002"  # noqa: S105 - test sentinel
CLIENT_SECRET = "client-secret-seed-0003"  # noqa: S105 - test sentinel
ARG_SECRET = "arg-secret-seed-0004"  # noqa: S105 - test sentinel
SAMPLING_SECRET = "sampling-secret-seed-0005"  # noqa: S105 - test sentinel
ELICIT_SECRET = "elicit-secret-seed-0006"  # noqa: S105 - test sentinel
CLIENT_ID = "confidential-client-1"
SEEDED = (ENV_SECRET, HEADER_SECRET, CLIENT_SECRET, ARG_SECRET, SAMPLING_SECRET, ELICIT_SECRET)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # surface the 302 so the test can read (and tamper with) Location


def _get(url: str, *, follow: bool) -> tuple[int, str, str]:
    """GET ``url`` without any logging; returns (status, Location, body)."""
    handlers = [] if follow else [_NoRedirect()]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, "", response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.headers.get("location", ""), err.read().decode("utf-8", "replace")


async def _browse(url: str, *, tamper_state: bool = False) -> str:
    """Follow the AS approval to the loopback callback, like the user's browser."""
    status, location, body = await anyio.to_thread.run_sync(lambda: _get(url, follow=False))
    assert status == 302, body
    if tamper_state:
        location = replace_param(location, "state", "tampered-secret-state-0007")
    return (await anyio.to_thread.run_sync(lambda: _get(location, follow=True)))[2]


@pytest.fixture(params=["modern", "legacy"])
def protected(request: pytest.FixtureRequest) -> str:
    """The protected fixture Server of each era (legacy marked ``interop``)."""
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
    url: str = request.getfixturevalue(f"protected_{request.param}_http")
    return url


def _servers(protected: str) -> dict[str, str]:
    return {
        REMOTE: http_table(protected, client_id=CLIENT_ID, client_secret=CLIENT_SECRET),
        LOCAL: modern_stdio_table(env={"SEEDED_TOKEN": "${SEEDED_ENV}"}),
        KEYED: "\n".join(
            [
                'transport = "http"',
                f'url = "{protected}"',
                'headers = { X-Api-Key = "${SEEDED_HEADER}" }',
            ]
        ),
    }


def _db_dump(path: Path) -> str:
    """Every value of every column of every table, read through a separate connection."""
    rows: list[str] = []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            for row in con.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 - names from schema
                rows.append(f"{table}: {row!r}")
    con.close()
    return "\n".join(rows)


def _file_bytes(data_dir: Path) -> dict[str, bytes]:
    names = (DB_FILENAME, f"{DB_FILENAME}-wal", f"{DB_FILENAME}-shm")
    return {n: (data_dir / n).read_bytes() for n in names if (data_dir / n).exists()}


def _record_text(record: logging.LogRecord) -> str:
    extras = {k: v for k, v in vars(record).items() if k not in ("msg", "args")}
    return "\n".join(
        [record.getMessage(), record.exc_text or "", record.stack_info or "", repr(extras)]
    )


async def _session(host: Host, auth: AuthServerState, texts: list[str], states: list[str]) -> None:
    connections = host.connections
    supervisor = connections.supervisor(REMOTE)
    with anyio.fail_after(TIMEOUT):
        await supervisor.wait_for(S.AUTH_REQUIRED)
        failed = await connections.supervisor(KEYED).wait_for(S.FAILED)
    assert failed.reason == "http_unauthorized"  # static headers never start OAuth

    # 1. A tampered callback (foreign `state`) never completes the flow.
    controller = connections.oauth_controller(REMOTE)
    assert controller is not None
    controller.deadline_s = 1.0
    flow = await connections.authorize(REMOTE)
    assert flow.authorization_url is not None
    states.append(query(flow.authorization_url)["state"])
    assert "Authorization failed" in await _browse(flow.authorization_url, tamper_state=True)
    with pytest.raises(AuthorizationError) as err, anyio.fail_after(TIMEOUT):
        await flow.wait()
    texts += [str(err.value), repr(err.value), repr(flow)]
    with anyio.fail_after(TIMEOUT):
        await supervisor.wait_for(S.AUTH_REQUIRED)

    # 2. A genuine authorization (confidential client: client_secret_post).
    controller.deadline_s = 300
    flow = await connections.authorize(REMOTE)
    assert flow.authorization_url is not None
    states.append(query(flow.authorization_url)["state"])
    assert "You can close this tab" in await _browse(flow.authorization_url)
    with anyio.fail_after(TIMEOUT):
        await flow.wait()
    texts.append(repr(flow))
    await wait_tools(host, REMOTE)
    await wait_tools(host, LOCAL)
    assert CLIENT_SECRET in auth.seen_secrets  # the secret was really sent

    # 3. Tool calls: a secret-like argument; elicitation; sampling; an in-session refresh.
    results: list[InvocationResult] = []
    call = host.invoker.call
    results.append(await call(f"{REMOTE}::noop", {"password": ARG_SECRET}, consumer=BOT))
    results.append(await call(f"{LOCAL}::noop", {"api_key": ARG_SECRET}, consumer=BOT))
    auth.revoke_access_tokens()  # the next request gets a 401 → one refresh, replayed
    named = await call(f"{REMOTE}::ask_name", {}, consumer=BOT)
    assert named.ok, named.error
    assert ELICIT_SECRET in named.text  # it really reached the Server (and came back)
    assert any(r["grant_type"] == "refresh_token" for r in auth.requests("token"))
    sampled = await call(f"{REMOTE}::sample", {}, consumer=BOT)
    assert sampled.ok, sampled.error
    assert SAMPLING_SECRET in sampled.text

    # 4. Failures, each carrying the secret-like argument where one can be passed.
    results.append(await call(f"{REMOTE}::echo", {"text": {"token": ARG_SECRET}}, consumer=BOT))
    results.append(await call(f"{REMOTE}::tool_error", {"token": ARG_SECRET}, consumer=BOT))
    results.append(await call(f"{REMOTE}::nope", {"token": ARG_SECRET}, consumer=BOT))
    results.append(await call(f"{KEYED}::noop", {"token": ARG_SECRET}, consumer=BOT))
    assert [r.ok for r in results] == [True, True, False, False, False, False]
    texts += [repr(r) for r in results]
    texts += [str(r.error) for r in results if r.error is not None]

    # 5. Sign-out.
    await connections.sign_out(REMOTE)
    for server_id in (REMOTE, LOCAL, KEYED):
        texts.append(repr(connections.status(server_id)))
        texts += [repr(entry) for entry in connections.logs(server_id)]
    texts.append(repr(host.config))


def _leaks(secrets: list[str], where: str, haystack: str | bytes) -> list[str]:
    """``where: secret`` for every secret found in ``haystack``."""
    found = []
    for value in secrets:
        needle: str | bytes = value.encode("utf-8") if isinstance(haystack, bytes) else value
        if needle in haystack:
            found.append(f"{where}: {value}")
    return found


@pytest.fixture
def capture_all() -> Iterator[list[logging.LogRecord]]:
    """Every record of every logger at DEBUG (after each logger's own filters ran)."""
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root = logging.getLogger()
    handler, level = Collect(logging.DEBUG), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


async def test_no_seeded_secret_appears_anywhere(
    setup: Setup,  # noqa: F811 - pytest fixture
    oauth_as: AuthServerState,  # noqa: F811 - pytest fixture
    protected: str,
    capture_all: list[logging.LogRecord],
) -> None:
    """REQ-NFR-010, REQ-AUTH-008, REQ-NFR-012, REQ-AUTH-009: env values, static header
    values, a client secret, secret-like tool arguments, sampling/elicitation text, and the
    codes, PKCE verifiers, ``state`` values and access/refresh tokens of a real OAuth session
    (failed and completed authorization, refresh, tool calls, elicitation, sampling,
    failures, sign-out) appear in no log record at DEBUG (any logger), no SQLite row, no
    SQLite/WAL bytes, no event, no log ring, no exception text and no ``repr`` of a public
    object; the DB and the token fallback file are ``0600``; the static-header Server fails
    with ``http_unauthorized`` without starting OAuth."""
    setup.env.update(SEEDED_ENV=ENV_SECRET, SEEDED_HEADER=HEADER_SECRET)
    oauth_as.preregister(CLIENT_ID, secret=CLIENT_SECRET)
    prompter = HandlerPrompter(
        [ElicitationResponse(action="accept", content={"name": ELICIT_SECRET})],
        decision=ConsentDecision.ALLOW_ONCE,
    )
    host = setup.host(
        write_config(setup, _servers(protected)),
        prompter=prompter,
        sampling_provider=FakeProvider(SAMPLING_SECRET),
    )
    texts: list[str] = []
    states: list[str] = []
    events: list[HostEvent] = []
    data_dir = setup.data_dir
    with anyio.fail_after(TIMEOUT * 4):
        async with host:
            async with recording(host) as events:
                await _session(host, oauth_as, texts, states)
            live_files = _file_bytes(data_dir)  # WAL present while the Host runs
            live_dump = _db_dump(data_dir / DB_FILENAME)
    assert prompter.elicited
    assert prompter.sampling_asks()

    secrets = [*SEEDED, *oauth_as.seen_secrets, *states, "tampered-secret-state-0007"]
    assert len(oauth_as.seen_secrets) >= 6  # code, verifier, access, refresh, client secret…
    ours = [r for r in capture_all if r.threadName != "fixture-auth-server"]
    assert any(r.name.startswith("mcp.") for r in ours)  # the SDK's loggers were captured
    leaks = [hit for r in ours for hit in _leaks(secrets, f"log {r.name}", _record_text(r))]
    leaks += _leaks(secrets, "events", "\n".join(e.model_dump_json() for e in events))
    leaks += _leaks(secrets, "exceptions / repr / log rings", "\n".join(texts))
    leaks += _leaks(secrets, "SQLite rows (live)", live_dump)
    leaks += _leaks(secrets, "SQLite rows (closed)", _db_dump(data_dir / DB_FILENAME))
    for name, blob in {**live_files, **_file_bytes(data_dir)}.items():
        leaks += _leaks(secrets, name, blob)
    assert sorted(set(leaks)) == []
    if sys.platform != "win32":
        for name in (DB_FILENAME, SECRETS_FILENAME):
            path = data_dir / name
            if path.exists():
                assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, name
    assert (data_dir / SECRETS_FILENAME).exists()  # the file backend held the tokens
