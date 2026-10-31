"""Shared fixtures: fixture Servers (Tech Design §8).

``modern_*`` run ``tests/fixtures/servers/modern_server.py`` on the project's mcp 2.3;
``legacy_*`` run ``legacy_server.py`` on ``mcp>=1.30,<2`` in an isolated uv environment
(marker ``interop``). Each fixture yields connection parameters: ``StdioServerParameters``
for stdio, the endpoint URL for HTTP (server torn down after the test).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import StdioServerParameters

SERVERS = Path(__file__).parent / "fixtures" / "servers"
MODERN = SERVERS / "modern_server.py"
LEGACY = SERVERS / "legacy_server.py"
LEGACY_MCP = "mcp>=1.30,<2"
HTTP_START_TIMEOUT = 90.0  # first legacy run may resolve and download mcp 1.x


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _legacy_command(*extra: str) -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH: legacy fixtures need `uv run --isolated`")
    return [
        uv,
        "run",
        "--isolated",
        "--no-project",
        "--with",
        LEGACY_MCP,
        "python",
        str(LEGACY),
        *extra,
    ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _serve_http(command: list[str], port: int) -> Iterator[str]:
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # noqa: S603
    deadline = time.monotonic() + HTTP_START_TIMEOUT
    try:
        while True:
            if proc.poll() is not None:
                err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                pytest.fail(f"fixture server exited early ({proc.returncode}): {err[-2000:]}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail(f"fixture server did not listen on {port} in time")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture
def modern_stdio() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(MODERN)])


@pytest.fixture
def modern_http() -> Iterator[str]:
    port = _free_port()
    cmd = [sys.executable, str(MODERN), "--transport", "http", "--port", str(port)]
    yield from _serve_http(cmd, port)


@pytest.fixture(scope="session")
def _legacy_env() -> None:
    """Resolve the isolated mcp 1.x environment once, before any handshake is timed.

    A cold ``uv run --isolated`` can take minutes (resolve + download); doing it inside a
    stdio handshake made the first legacy test flaky on a cold cache.
    """
    command = [*_legacy_command()[:-1], "-c", "import mcp"]
    subprocess.run(command, check=True, capture_output=True, timeout=600)  # noqa: S603


@pytest.fixture
def legacy_stdio(_legacy_env: None) -> StdioServerParameters:
    command = _legacy_command()
    return StdioServerParameters(command=command[0], args=command[1:])


@pytest.fixture
def legacy_http(_legacy_env: None) -> Iterator[str]:
    port = _free_port()
    yield from _serve_http(_legacy_command("--transport", "http", "--port", str(port)), port)
