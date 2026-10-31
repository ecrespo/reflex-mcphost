"""Shared fixtures: fixture Servers (Tech Design §8) and sample config files (DD-013).

``modern_*`` run ``tests/fixtures/servers/modern_server.py`` on the project's mcp 2.3;
``legacy_*`` run ``legacy_server.py`` on ``mcp>=1.30,<2`` in an isolated uv environment
(marker ``interop``). Each fixture yields connection parameters: ``StdioServerParameters``
for stdio, the endpoint URL for HTTP (server torn down after the test).
"""

from __future__ import annotations

import hashlib
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.config.models import SourceKind, SourcesConfig
from reflex_mcphost.core.config.paths import Platform, discover_sources
from tests.fixtures.servers.header_recorder import read_records

SERVERS = Path(__file__).parent / "fixtures" / "servers"
CONFIGS = Path(__file__).parent / "fixtures" / "configs"
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


@dataclass(frozen=True)
class RecordedHttp:
    """A modern HTTP fixture Server whose request headers are appended to ``headers_file``."""

    url: str
    headers_file: Path

    def requests(self) -> list[dict[str, Any]]:
        return read_records(self.headers_file)


@pytest.fixture
def modern_http_recorded(tmp_path: Path) -> Iterator[RecordedHttp]:
    port = _free_port()
    headers_file = tmp_path / "headers.jsonl"
    cmd = [
        sys.executable,
        str(MODERN),
        "--transport",
        "http",
        "--port",
        str(port),
        "--record-headers",
        str(headers_file),
    ]
    for url in _serve_http(cmd, port):
        yield RecordedHttp(url=url, headers_file=headers_file)


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


@pytest.fixture
def legacy_http_recorded(_legacy_env: None, tmp_path: Path) -> Iterator[RecordedHttp]:
    """A legacy HTTP fixture Server recording its request headers (``drop_session`` tool)."""
    port = _free_port()
    headers_file = tmp_path / "legacy-headers.jsonl"
    command = _legacy_command(
        "--transport", "http", "--port", str(port), "--record-headers", str(headers_file)
    )
    for url in _serve_http(command, port):
        yield RecordedHttp(url=url, headers_file=headers_file)


# ------------------------------------------------------------------ config files (DD-013)

SAMPLE_FILES = {
    SourceKind.MCP_JSON_PROJECT: "dot_mcp.json",
    SourceKind.CURSOR_PROJECT: "cursor_mcp.json",
    SourceKind.VSCODE_PROJECT: "vscode_mcp.json",
    SourceKind.CURSOR_USER: "cursor_mcp.json",
    SourceKind.VSCODE_USER: "vscode_mcp.json",
    SourceKind.CLAUDE_DESKTOP: "claude_desktop_config.json",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class ThirdPartyFiles:
    """A fake home + project under ``tmp_path``; every installed file is hashed so the
    fixture can assert it is byte-identical at teardown (REQ-REG-008, Art. 6)."""

    home: Path
    cwd: Path
    user_file: Path
    platform: Platform
    hashes: dict[Path, str] = field(default_factory=dict)

    def path(self, kind: SourceKind) -> Path:
        specs = discover_sources(
            self.cwd, user_file=self.user_file, sources=SourcesConfig(), platform=self.platform
        )
        return next(s.path for s in specs if s.kind is kind)

    def install(
        self, kind: SourceKind, *, sample: str | None = None, text: str | None = None
    ) -> Path:
        """Write a sample (default: the format's sample file) or ``text`` at ``kind``'s path."""
        return self.install_at(self.path(kind), sample=sample or SAMPLE_FILES.get(kind), text=text)

    def install_at(self, path: Path, *, sample: str | None = None, text: str | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            path.write_text(text, encoding="utf-8")
        else:
            assert sample is not None
            shutil.copyfile(CONFIGS / sample, path)
        self.hashes[path] = _sha256(path)
        return path

    def install_all(self) -> None:
        for kind in SAMPLE_FILES:
            self.install(kind)

    def verify(self) -> None:
        for path, digest in self.hashes.items():
            assert _sha256(path) == digest, f"third-party file modified: {path}"


@pytest.fixture(scope="session", autouse=True)
def _sample_configs_untouched() -> Iterator[None]:
    """REQ-REG-008, Art. 6: the shipped third-party sample config files under
    ``tests/fixtures/configs`` are byte-identical at the end of the whole suite."""
    before = {path: _sha256(path) for path in sorted(CONFIGS.rglob("*")) if path.is_file()}
    yield
    after = {path: _sha256(path) for path in sorted(CONFIGS.rglob("*")) if path.is_file()}
    assert after == before, "third-party sample config files changed during the suite"


@pytest.fixture
def third_party_files(tmp_path: Path) -> Iterator[ThirdPartyFiles]:
    home, cwd = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    cwd.mkdir()
    files = ThirdPartyFiles(
        home=home,
        cwd=cwd,
        user_file=tmp_path / "cfg" / "mcphost.toml",
        platform=Platform(system="linux", home=home, env={}),
    )
    yield files
    files.verify()


@pytest.fixture
def legacy_http_dropping(_legacy_env: None, tmp_path: Path) -> Iterator[RecordedHttp]:
    """A legacy HTTP fixture Server answering 404 to every request with a session id."""
    port = _free_port()
    headers_file = tmp_path / "dropping-headers.jsonl"
    command = _legacy_command(
        "--transport",
        "http",
        "--port",
        str(port),
        "--record-headers",
        str(headers_file),
        "--drop-every-session",
    )
    for url in _serve_http(command, port):
        yield RecordedHttp(url=url, headers_file=headers_file)
