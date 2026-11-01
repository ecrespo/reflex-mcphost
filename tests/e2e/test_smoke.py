"""Headless smoke of the reference app from the built wheel (F6-09a; DD-016, DD-017).

``uvx --from <wheel> --compile-bytecode reflex-mcphost run --no-browser`` with the modern
stdio fixture as an own Server, in a temporary ``XDG_CACHE_HOME``; HTTP only (``httpx``, no
browser). The module's run is shared by the guard checks and ends with the Ctrl-C test; the
site-packages check runs last and covers every start of the session so far.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import MODERN, _free_port
from tests.e2e.conftest import (
    E2E,
    FIRST_START_TIMEOUT,
    STOP_TIMEOUT,
    AppRun,
    cmdline,
    descendants,
    modern_argv,
    site_files,
)
from tests.process import pid_alive

pytestmark = pytest.mark.e2e
NOTICE = "Open http://127.0.0.1:{port}/"


@pytest.fixture(scope="module")
def smoke(e2e: E2E) -> Iterator[AppRun]:
    config = e2e.write_config("smoke", {"modern": modern_argv()})
    run = AppRun(e2e, config, _free_port()).start()
    try:
        run.wait_ready(FIRST_START_TIMEOUT)
        yield run
    finally:
        run.stop()


def _client() -> httpx.Client:
    return httpx.Client(trust_env=False, timeout=10.0)


def _fixture_servers(run: AppRun) -> list[int]:
    assert run.proc is not None
    return [p for p in descendants(run.proc.pid) if str(MODERN) in cmdline(p)]


def _listening_addresses(port: int) -> set[str]:
    """Local addresses (hex, /proc/net/tcp{,6}) in LISTEN state on ``port``."""
    found: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:  # pragma: no cover - no IPv6 table
            continue
        for row in rows:
            local, state = row.split()[1], row.split()[3]
            address, hex_port = local.rsplit(":", 1)
            if state == "0A" and int(hex_port, 16) == port:
                found.add(address)
    return found


def test_ui_is_served_on_127_0_0_1(smoke: AppRun) -> None:
    """REQ-UI-001, REQ-UI-002: the wheel's app answers ``GET /`` (200, the UI page) and
    ``/ping`` at ``http://127.0.0.1:<port>``, and listens only on the IPv4 loopback."""
    with _client() as client:
        page = client.get(f"{smoke.origin}/")
        ping = client.get(f"{smoke.origin}/ping")
    assert page.status_code == 200
    assert "<html" in page.text.lower()
    assert ping.status_code == 200
    assert _listening_addresses(smoke.port) == {"0100007F"}  # 127.0.0.1, nothing else


def test_localhost_host_is_refused_with_the_notice(smoke: AppRun) -> None:
    """REQ-NFR-011: ``Host: localhost:<port>`` → 403 whose body tells the user to open
    ``http://127.0.0.1:<port>`` (DD-017)."""
    with _client() as client:
        response = client.get(f"{smoke.origin}/", headers={"Host": f"localhost:{smoke.port}"})
    assert response.status_code == 403
    assert NOTICE.format(port=smoke.port) in response.text


def test_foreign_origin_is_refused(smoke: AppRun) -> None:
    """REQ-NFR-011: a request with a foreign ``Origin`` is refused (403), the same request
    with the allowed origin is served."""
    with _client() as client:
        foreign = client.get(f"{smoke.origin}/ping", headers={"Origin": "http://evil.test"})
        own = client.get(f"{smoke.origin}/ping", headers={"Origin": smoke.origin})
    assert foreign.status_code == 403
    assert own.status_code == 200


def test_event_polling_handshake_with_a_foreign_host_is_refused(smoke: AppRun) -> None:
    """REQ-NFR-011: the socket.io polling handshake on ``/_event/`` with a rebinding
    ``Host`` is refused before Reflex sees it (403)."""
    url = f"{smoke.origin}/_event/?EIO=4&transport=polling"
    with _client() as client:
        response = client.get(url, headers={"Host": f"evil.test:{smoke.port}"})
    assert response.status_code == 403
    assert NOTICE.format(port=smoke.port) in response.text


def test_frontend_event_url_is_the_loopback_origin(smoke: AppRun, e2e: E2E) -> None:
    """REQ-NFR-011 (S-5 #8): the built frontend connects its event websocket to
    ``ws://127.0.0.1:<port>/_event`` (the workspace's ``.web/env.json``)."""
    env_files = [w / ".web" / "env.json" for w in e2e.workspaces()]
    events = {json.loads(p.read_text(encoding="utf-8"))["EVENT"] for p in env_files if p.exists()}
    assert f"ws://127.0.0.1:{smoke.port}/_event" in events


def test_ctrl_c_exits_0_and_stops_the_fixture_server(smoke: AppRun) -> None:
    """REQ-CON-009, REQ-UI-001: Ctrl-C (SIGINT to the process group) → the CLI forwards it,
    the Host closes its Clients and the run exits 0 within 20 s; the own stdio fixture
    Server's process is gone (Host lifespan, delta D1)."""
    deadline = time.monotonic() + 30
    while not (servers := _fixture_servers(smoke)):
        assert time.monotonic() < deadline, "the fixture Server was never started"
        time.sleep(0.2)
    started = time.monotonic()
    smoke.interrupt()
    code = smoke.wait(timeout=STOP_TIMEOUT)
    elapsed = time.monotonic() - started
    assert code == 0, smoke.output[-3000:]
    assert elapsed < STOP_TIMEOUT
    deadline = time.monotonic() + 2
    while any(pid_alive(p) for p in servers) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(pid_alive(p) for p in servers)


def test_servers_json_with_the_same_config(e2e: E2E) -> None:
    """REQ-UI-008: ``reflex-mcphost servers --json`` from the wheel lists the own fixture
    Server of the smoke config (exit 0, no UI started)."""
    config = e2e.write_config("smoke", {"modern": modern_argv()})
    out = subprocess.run(  # noqa: S603
        e2e.command("servers", "--json", "--config", str(config)),
        capture_output=True,
        text=True,
        env=e2e.env,
        cwd=e2e.root,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert [s["id"] for s in payload["servers"]] == ["modern"]
    assert payload["problems"] == []


def test_wildcard_host_is_refused_from_the_wheel(e2e: E2E) -> None:
    """REQ-UI-002: ``run --host 0.0.0.0`` from the wheel exits 2 without starting Reflex."""
    out = subprocess.run(  # noqa: S603
        e2e.command("run", "--no-browser", "--host", "0.0.0.0"),  # noqa: S104 - the refused value
        capture_output=True,
        text=True,
        env=e2e.env,
        cwd=e2e.root,
        timeout=120,
        check=False,
    )
    assert out.returncode == 2
    assert "wildcard" in out.stderr


def test_nothing_but_bytecode_was_added_to_site_packages(e2e: E2E) -> None:
    """REQ-UI-001 (DD-016): after the session's runs, no file other than
    ``__pycache__/*.pyc`` was added under the tool environment's site-packages (the
    workspace and ``.web/`` live under the user cache)."""
    added = site_files(e2e.site_packages) - e2e.baseline
    unexpected = sorted(p for p in added if not (p.endswith(".pyc") and "/__pycache__/" in f"/{p}"))
    assert unexpected == []
