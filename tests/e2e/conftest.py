"""End-to-end rig (marker ``e2e``; F6-09a, F6-09b, F7-06b): the reference app from the built wheel.

``e2e`` builds the wheel once per session (``uv build``) and runs ``uvx --from <wheel>
--compile-bytecode reflex-mcphost …`` with a temporary ``XDG_CACHE_HOME`` (the app workspace
of DD-016 lives there) while keeping the developer's uv cache, so the tool environment is
resolved from cache. The tool environment is created (and its site-packages listed) before
any app start, so the smoke test can check that nothing but ``__pycache__/*.pyc`` was added
under site-packages (DD-016). The keyring is the ``fail`` backend (secrets go to the 0600
file fallback); user-level third-party config sources are disabled in every config.

``AppRun`` starts ``reflex-mcphost run --no-browser`` in its own session (a terminal Ctrl-C
reaches the whole foreground process group: ``interrupt()`` sends SIGINT to the group, uv
leaves it to the CLI, which forwards it once to Reflex) and collects the merged output.

Playwright: one driver per session (``engines``); Chromium serves the Phase 6 flows, the
MCP Apps tests run on Chromium, Firefox and WebKit (``ENGINES``; WebKit without system
libraries: ``WEBKIT_PRELOAD_ENV``, the spike S-3 workaround).

e2e tests run only when selected (``pytest -m e2e``): the first start downloads Bun and
builds the frontend (network) and every start runs a production build. They need Linux
(``/proc`` for the process tree; the CI ``e2e`` job runs on ``ubuntu-latest``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.conftest import APPS_SERVER, MODERN, _free_port

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright

REPO = Path(__file__).resolve().parents[2]
READY_RE = re.compile(r"reflex-mcphost: the Host UI is ready at (http://\S+)")
FIRST_START_TIMEOUT = 900.0
"""The session's first start installs Bun and the frontend packages (network)."""
START_TIMEOUT = 120.0
STOP_TIMEOUT = 20.0
"""Ctrl-C → exit 0 within 20 s (Host ``aclose`` budget 15 s, REQ-CON-009, plus Reflex)."""
KEYRING_FAIL = "keyring.backends.fail.Keyring"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``e2e`` tests unless the marker expression selects them (``-m e2e``)."""
    if "e2e" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="e2e: run with `pytest -m e2e` (builds the frontend)")
    for item in items:
        if item.get_closest_marker("e2e") is not None:
            item.add_marker(skip)


# ------------------------------------------------------------------ session environment


def _uv_cache_dir(uv: str) -> str:
    """The developer's uv cache (resolved before ``XDG_CACHE_HOME`` is overridden)."""
    if os.environ.get("UV_CACHE_DIR"):
        return os.environ["UV_CACHE_DIR"]
    out = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True, check=True)  # noqa: S603
    return out.stdout.strip()


@dataclass
class E2E:
    """The session's wheel, tool command and environment."""

    root: Path
    wheel: Path
    uv: str
    env: dict[str, str]
    site_packages: Path
    baseline: frozenset[str] = field(default_factory=frozenset)

    @property
    def cache_home(self) -> Path:
        return self.root / "xdg-cache"

    def command(self, *args: str) -> list[str]:
        """``uvx --from <wheel> --compile-bytecode reflex-mcphost <args>``."""
        return [
            self.uv,
            "tool",
            "run",
            "--from",
            str(self.wheel),
            "--compile-bytecode",
            "reflex-mcphost",
            *args,
        ]

    def tool_python(self, code: str) -> str:
        """Run ``python -c code`` in the tool environment; its stdout."""
        argv = [self.uv, "tool", "run", "--from", str(self.wheel), "--compile-bytecode"]
        out = subprocess.run(  # noqa: S603
            [*argv, "python", "-c", code],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.root,
            check=True,
            timeout=600,
        )
        return out.stdout.strip()

    def workspaces(self) -> list[Path]:
        """The materialized app workspaces under the session's cache home."""
        parent = self.cache_home / "reflex-mcphost" / "app"
        return sorted(p for p in parent.iterdir() if p.is_dir() and not p.name.startswith("."))

    def write_config(self, name: str, servers: dict[str, Sequence[str]]) -> Path:
        """A ``mcphost.toml`` with these stdio Servers (argv each) and its own data dir."""
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        body = [
            "schema_version = 1",
            "[host]",
            f"data_dir = {json.dumps((directory / 'data').as_posix())}",
            "[sources]",
            "claude_desktop = false",
            "cursor = false",
            "vscode = false",
        ]
        for server_id, argv in servers.items():
            body += [
                f"[servers.{server_id}]",
                'transport = "stdio"',
                f"command = {json.dumps(argv[0])}",
                f"args = {json.dumps(list(argv[1:]))}",
            ]
        path = directory / "mcphost.toml"
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path


def site_files(root: Path) -> frozenset[str]:
    """Every file under ``root`` as a POSIX relative path."""
    return frozenset(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def modern_argv(*extra: str) -> list[str]:
    """The modern stdio fixture Server on this interpreter (which has ``mcp``)."""
    return [sys.executable, str(MODERN), *extra]


def apps_argv() -> list[str]:
    """The MCP Apps stdio fixture Server (modern era) on this interpreter (F7-06b)."""
    return [sys.executable, str(APPS_SERVER)]


@pytest.fixture(scope="session")
def e2e(tmp_path_factory: pytest.TempPathFactory) -> E2E:
    """Build the wheel once, create its tool environment and record site-packages."""
    if sys.platform != "linux":
        pytest.skip("e2e needs Linux (/proc process tree; CI e2e job: ubuntu-latest)")
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    root = tmp_path_factory.mktemp("e2e")
    dist = root / "dist"
    subprocess.run(  # noqa: S603
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO,
        check=True,
        capture_output=True,
        timeout=600,
    )
    (wheel,) = dist.glob("reflex_mcphost-*.whl")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("REFLEX_MCPHOST_") and k not in ("VIRTUAL_ENV", "PYTHONPATH")
    }
    env |= {
        "UV_CACHE_DIR": _uv_cache_dir(uv),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "PYTHON_KEYRING_BACKEND": KEYRING_FAIL,
        "PYTHONUNBUFFERED": "1",
    }
    rig = E2E(root=root, wheel=wheel, uv=uv, env=env, site_packages=Path())
    rig.site_packages = Path(
        rig.tool_python("import sysconfig; print(sysconfig.get_paths()['purelib'])")
    )
    assert (rig.site_packages / "reflex_mcphost" / "__init__.py").is_file()
    rig.baseline = site_files(rig.site_packages)
    return rig


# ------------------------------------------------------------------ one app run


class AppRun:
    """One ``reflex-mcphost run --no-browser`` process (own session, merged output)."""

    def __init__(self, rig: E2E, config: Path, port: int, *extra: str) -> None:
        self.rig = rig
        self.port = port
        self.argv = rig.command(
            "run", "--no-browser", "--port", str(port), "--config", str(config), *extra
        )
        self.lines: list[str] = []
        self._cond = threading.Condition()
        self.proc: subprocess.Popen[str] | None = None
        self.started_at = 0.0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def output(self) -> str:
        with self._cond:
            return "".join(self.lines)

    def start(self) -> AppRun:
        self.started_at = time.monotonic()
        self.proc = subprocess.Popen(  # noqa: S603
            self.argv,
            cwd=self.rig.root,
            env=self.rig.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        return self

    def _pump(self) -> None:
        assert self.proc is not None
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with self._cond:
                self.lines.append(line)
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def wait_for(self, pattern: str | re.Pattern[str], timeout: float, since: int = 0) -> str:
        """The first output line (from line ``since``) matching ``pattern``."""
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout
        seen = since
        with self._cond:
            while True:
                for line in self.lines[seen:]:
                    if regex.search(line):
                        return line
                seen = len(self.lines)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (self.proc is not None and self.proc.poll() is not None):
                    if self.proc is not None and self.proc.poll() is not None:
                        for line in self.lines[seen:]:  # pragma: no cover - raced with exit
                            if regex.search(line):
                                return line
                    tail = "".join(self.lines[-40:])
                    raise AssertionError(f"no line matching {regex.pattern!r}:\n{tail}")
                self._cond.wait(min(remaining, 0.5))

    def wait_ready(self, timeout: float = START_TIMEOUT) -> float:
        """Wait for the CLI's ready line; seconds since ``start``."""
        match = READY_RE.search(self.wait_for(READY_RE, timeout))
        assert match is not None
        assert match.group(1) == f"{self.origin}/"
        return time.monotonic() - self.started_at

    def interrupt(self) -> None:
        """A terminal Ctrl-C: SIGINT to the run's process group."""
        assert self.proc is not None
        os.killpg(self.proc.pid, signal.SIGINT)

    def wait(self, timeout: float) -> int:
        assert self.proc is not None
        return self.proc.wait(timeout=timeout)

    def stop(self) -> None:
        """Tear down whatever is still running: Ctrl-C to the group, then SIGKILL to the
        whole process tree (Reflex and the stdio Servers run in sessions of their own)."""
        if self.proc is None or self.proc.poll() is not None:
            return
        tree = {pid: cmdline(pid) for pid in [self.proc.pid, *descendants(self.proc.pid)]}
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
            self.proc.wait(timeout=STOP_TIMEOUT)
        except (ProcessLookupError, subprocess.TimeoutExpired):  # pragma: no cover - failures
            pass
        for pid, argv in tree.items():  # survivors only (same command line: not a reused pid)
            if argv and cmdline(pid) == argv:  # pragma: no cover - only after a failed stop
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    continue
        self.proc.wait(timeout=10.0)


@contextmanager
def app_run(
    rig: E2E, config: Path, *extra: str, timeout: float = START_TIMEOUT
) -> Iterator[AppRun]:
    """A started, ready ``AppRun`` on a free port, stopped at the end of the block."""
    run = AppRun(rig, config, _free_port(), *extra).start()
    try:
        run.wait_ready(timeout)
        yield run
    finally:
        run.stop()


@pytest.fixture(scope="session")
def warm(e2e: E2E) -> E2E:
    """The session's first (cold) start, so later starts run on a warm workspace."""
    config = e2e.write_config("warm-up", {})
    with app_run(e2e, config, timeout=FIRST_START_TIMEOUT):
        pass
    return e2e


# ------------------------------------------------------------------ process tree (Linux)


def _parents() -> dict[int, int]:
    out: dict[int, int] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        out[int(stat.parent.name)] = int(fields[1])
    return out


def descendants(pid: int) -> list[int]:
    """Live descendants of ``pid``."""
    parents = _parents()
    found: list[int] = []
    frontier = [pid]
    while frontier:
        current = frontier.pop()
        children = [p for p, parent in parents.items() if parent == current]
        found += children
        frontier += children
    return found


def cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


# ------------------------------------------------------------------ Playwright (F6-09b)

MEASUREMENTS: list[str] = []
"""NFR measurement lines, printed in the terminal summary (and so in the CI log)."""


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if MEASUREMENTS:
        terminalreporter.section("e2e measurements (REQ-NFR-003/004)")
        for line in MEASUREMENTS:
            terminalreporter.write_line(line)


ENGINES: tuple[str, ...] = ("chromium", "firefox", "webkit")
"""The Playwright engines of the MCP Apps E2E (F7-06b; WebKit stands in for Safari, Plan R-8)."""
WEBKIT_PRELOAD_ENV = "MCPHOST_E2E_WEBKIT_PRELOAD"
"""S-3 workaround for a host without WebKit's system libraries (no sudo): a ``:``-separated
list of extracted ``.so`` files (``apt-get download libmanette-0.2-0 libhidapi-hidraw0`` +
``dpkg-deb -x``) given as ``LD_PRELOAD`` to the WebKit launch only (its ``MiniBrowser``
wrapper overwrites ``LD_LIBRARY_PATH``); run with
``PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1``. CI installs the libraries with
``playwright install --with-deps`` and sets neither."""


class Engines:
    """The session's Playwright driver and one lazily launched browser per engine."""

    def __init__(self, playwright: Playwright) -> None:
        self._playwright = playwright
        self._browsers: dict[str, Browser] = {}

    def get(self, name: str) -> Browser:
        from playwright.sync_api import Error

        if name not in self._browsers:
            kwargs: dict[str, Any] = {"headless": True}
            preload = os.environ.get(WEBKIT_PRELOAD_ENV)
            if name == "webkit" and preload:
                kwargs["env"] = {**os.environ, "LD_PRELOAD": preload}
            try:
                self._browsers[name] = getattr(self._playwright, name).launch(**kwargs)
            except Error as exc:  # an environment problem, never a skip (F7-06b)
                pytest.fail(
                    f"{name} does not launch (uv run playwright install --with-deps {name}; "
                    f"WebKit without system libraries: see {WEBKIT_PRELOAD_ENV}): {exc}"
                )
        return self._browsers[name]

    def close(self) -> None:
        for launched in self._browsers.values():
            launched.close()
        self._browsers.clear()


@pytest.fixture(scope="session")
def engines() -> Iterator[Engines]:
    """One Playwright driver for the session (a second ``sync_playwright`` cannot run in
    the same thread); browsers are launched on first use."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launched = Engines(playwright)
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture(scope="session")
def browser(engines: Engines) -> Browser:
    """Headless Chromium (``uv run playwright install chromium`` provides it)."""
    return engines.get("chromium")
