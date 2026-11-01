"""``reflex-mcphost`` command line (API §6; REQ-UI-001, REQ-UI-002, REQ-UI-008; DD-016).

``run`` (also the bare command) checks its arguments (exit 2), loads the explicit config
and opens + closes the database (exit 3), materializes the workspace and runs
``python -m reflex run --env prod`` there on one port, passing its options only through the
``REFLEX_MCPHOST_*`` environment variables. It polls ``GET /ping`` with the allowed
``Host``, prints the URL and opens it in the browser after the first answer (not with
``--no-browser``). Ctrl-C / SIGTERM are forwarded once to the child, which runs in its own
process group, and the CLI exits 0 after it ended; a child that fails before the UI was
ready is exit 3, afterwards its own code.

``servers`` prints the merged Server list, the shadowed definitions and the problems
without starting a Host, connecting, or importing ``reflex``. ``--version`` prints the
version. This module imports no ``reflex``: the app runs in the child process.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Protocol, TextIO

from reflex_mcphost.core.errors import MCPHostError

if TYPE_CHECKING:
    import httpx

    from reflex_mcphost.core.config.models import HostConfig

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ENV_CONFIG",
    "ENV_HOST",
    "ENV_LOG_JSON",
    "ENV_PORT",
    "ENV_UNSAFE_ALLOW_REMOTE",
    "EXIT_OK",
    "EXIT_PROBLEMS",
    "EXIT_STARTUP",
    "EXIT_USAGE",
    "ChildProcess",
    "Runtime",
    "child_command",
    "child_environment",
    "configure_logging",
    "main",
    "probe_ping",
    "spawn_child",
]

PROG = "reflex-mcphost"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_STARTUP = 3

ENV_PREFIX = "REFLEX_MCPHOST_"
ENV_HOST = "REFLEX_MCPHOST_HOST"
ENV_PORT = "REFLEX_MCPHOST_PORT"
ENV_CONFIG = "REFLEX_MCPHOST_CONFIG"
ENV_UNSAFE_ALLOW_REMOTE = "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE"  # = ui.install's opt-in
ENV_LOG_JSON = "REFLEX_MCPHOST_LOG_JSON"
FIXED_CHILD_ENV = {"GRANIAN_WORKERS": "1", "REFLEX_CHECK_LATEST_VERSION": "false"}
"""One backend worker = one Host (S-5 #5); no PyPI version check on start."""
_DROPPED_CHILD_ENV = ("REFLEX_FRONTEND_ONLY", "REFLEX_BACKEND_ONLY")
"""Inherited Reflex run-mode switches that would drop half of the single-port app."""

PROBE_INTERVAL_S = 0.5
STOP_GRACE_S = 30.0
"""How long a forwarded signal may take (Host ``aclose`` budget 15 s plus Reflex)."""
INTERRUPT_SIGNAL: int = (
    signal.CTRL_BREAK_EVENT  # type: ignore[attr-defined,unused-ignore]
    if os.name == "nt"
    else signal.SIGINT
)
"""What a Ctrl-C is forwarded as: the child is in its own process group (Windows needs
``CTRL_BREAK_EVENT`` for that)."""
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_COMMANDS = ("run", "servers")
_TOP_LEVEL = ("--version", "-h", "--help")


class ChildProcess(Protocol):
    """The part of ``subprocess.Popen`` the CLI uses (fakes in tests)."""

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def send_signal(self, sig: int) -> None: ...

    def kill(self) -> None: ...


class UsageError(Exception):
    """A refused option (exit 2)."""


class StartupError(Exception):
    """A fatal start-up error (exit 3)."""


class TerminationRequestedError(KeyboardInterrupt):
    """SIGTERM / SIGHUP received: handled like Ctrl-C, forwarded as ``signal``."""

    def __init__(self, sig: int) -> None:
        super().__init__(sig)
        self.signal = sig


# ------------------------------------------------------------------ process boundary


def spawn_child(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> ChildProcess:
    """Start Reflex in ``cwd`` in its own process group / session.

    A terminal Ctrl-C then reaches only the CLI, which forwards it exactly once (a second
    SIGINT would cut the Host's shutdown short).
    """
    if os.name == "nt":  # pragma: no cover - Windows CI
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined,unused-ignore]
        return subprocess.Popen(list(argv), cwd=cwd, env=dict(env), creationflags=flags)  # noqa: S603
    return subprocess.Popen(list(argv), cwd=cwd, env=dict(env), start_new_session=True)  # noqa: S603


def _probe_transport() -> httpx.BaseTransport:
    import httpx

    return httpx.HTTPTransport()


def probe_ping(url: str, host_header: str) -> bool:
    """``GET url`` with ``Host: host_header`` (never through a proxy); ``True`` on 200."""
    import httpx

    try:
        with httpx.Client(transport=_probe_transport(), trust_env=False, timeout=2.0) as client:
            response = client.get(url, headers={"Host": host_header})
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _load_config(path: Path | None) -> HostConfig:
    import anyio

    from reflex_mcphost.core.config.models import HostConfig

    return anyio.run(partial(HostConfig.aload, path))


def _check_database(data_dir: Path) -> None:
    from reflex_mcphost.core.db import open_database

    open_database(data_dir).close()


def _materialize() -> Path:
    from reflex_mcphost.app.workspace import materialize

    return materialize()


@dataclass
class Runtime:
    """The CLI's side effects, replaceable in tests."""

    spawn: Callable[[Sequence[str], Path, Mapping[str, str]], ChildProcess] = spawn_child
    probe: Callable[[str, str], bool] = probe_ping
    open_browser: Callable[[str], object] = webbrowser.open
    materialize: Callable[[], Path] = _materialize
    load_config: Callable[[Path | None], HostConfig] = _load_config
    check_database: Callable[[Path], None] = _check_database
    sleep: Callable[[float], None] = time.sleep
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    stdout: TextIO | None = None
    stderr: TextIO | None = None
    install_signals: bool = True

    @property
    def out(self) -> TextIO:
        return self.stdout if self.stdout is not None else sys.stdout

    @property
    def err(self) -> TextIO:
        return self.stderr if self.stderr is not None else sys.stderr


# ------------------------------------------------------------------ console logging (TD §7.1)

_HANDLER_ATTR = "_reflex_mcphost_console"


class JsonLineFormatter(logging.Formatter):
    """One JSON object per record: ``ts``, ``level``, ``logger``, ``msg``, context, ``exc``."""

    def format(self, record: logging.LogRecord) -> str:
        from reflex_mcphost.core.logging import CONTEXT_FIELDS

        stamp = datetime.fromtimestamp(record.created, UTC)
        data: dict[str, Any] = {
            "ts": stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in (*CONTEXT_FIELDS, "consumer", "outcome", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                data[name] = value if isinstance(value, int | float | bool) else str(value)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            data["exc"] = record.exc_text
        return json.dumps(data, ensure_ascii=False)


def configure_logging(*, json_lines: bool, stream: TextIO | None = None) -> None:
    """Attach one console handler (text, or JSON lines) to the ``reflex_mcphost`` logger.

    Idempotent (replaces a previous console handler). The handler carries the
    ``SecretRedactionFilter`` too, and the logger stops propagating so a root handler that
    Reflex sets up does not print every line twice.
    """
    from reflex_mcphost.core.logging import ROOT_LOGGER, SecretRedactionFilter

    logger = logging.getLogger(ROOT_LOGGER)
    for old in [h for h in logger.handlers if getattr(h, _HANDLER_ATTR, False)]:
        logger.removeHandler(old)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    setattr(handler, _HANDLER_ATTR, True)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(
        JsonLineFormatter()
        if json_lines
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


# ------------------------------------------------------------------ argument parsing


class _Parser(argparse.ArgumentParser):
    """``argparse`` that reports usage errors to the runtime's stderr."""

    def __init__(self, *args: Any, err: TextIO, out: TextIO, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._err, self._out = err, out

    def _print_message(self, message: str, file: Any = None) -> None:
        if message:
            (self._err if file is sys.stderr else self._out).write(message)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port {port} is outside 1-65535")
    return port


def build_parser(out: TextIO, err: TextIO) -> argparse.ArgumentParser:
    """The ``reflex-mcphost`` parser (API §6)."""
    from reflex_mcphost import __version__

    parser = _Parser(
        prog=PROG,
        description="An open MCP host built with Reflex. Without a command: run.",
        out=out,
        err=err,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="{run,servers}", parser_class=_Parser)
    run = commands.add_parser("run", help="start the reference app", out=out, err=err)
    run.add_argument("--host", default=DEFAULT_HOST, help="bind address (default 127.0.0.1)")
    run.add_argument("--port", type=_port, default=DEFAULT_PORT, help="UI and backend port")
    run.add_argument("--no-browser", action="store_true", help="do not open a browser")
    run.add_argument("--config", type=Path, default=None, help="mcphost.toml to use")
    run.add_argument(
        "--unsafe-allow-remote",
        action="store_true",
        help="allow a concrete non-loopback --host (reachable from other machines)",
    )
    run.add_argument("--log-json", action="store_true", help="JSON log lines on the console")
    servers = commands.add_parser(
        "servers", help="list the merged Server definitions", out=out, err=err
    )
    servers.add_argument("--json", action="store_true", help="machine-readable output")
    servers.add_argument("--config", type=Path, default=None, help="mcphost.toml to use")
    servers.add_argument("--strict", action="store_true", help="exit 1 on any config problem")
    return parser


def _with_command(argv: Sequence[str]) -> list[str]:
    args = list(argv)
    if not args or (args[0] not in _COMMANDS and args[0] not in _TOP_LEVEL):
        return ["run", *args]
    return args


# ------------------------------------------------------------------ run: argument rules


@dataclass(frozen=True)
class Origin:
    """The single origin the app serves (DD-017)."""

    host: str
    port: int

    @property
    def host_header(self) -> str:
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return f"http://{self.host_header}/"


def _check_host(raw: str, *, allow_remote: bool) -> str:
    host = raw.strip().removeprefix("[").removesuffix("]").lower()
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if not host or (address is not None and address.is_unspecified) or host == "*":
        raise UsageError(
            f"--host {raw!r} is a wildcard address; the UI guard needs one concrete origin "
            "(REQ-NFR-011), so it is refused even with --unsafe-allow-remote"
        )
    if host == "localhost" or host.endswith(".localhost"):
        raise UsageError(
            f"--host {raw!r}: localhost is reserved for the MCP Apps sandbox proxy; use 127.0.0.1"
        )
    if address is None and not _HOSTNAME.match(host):
        raise UsageError(f"--host {raw!r} is not an IP address or a host name")
    if host != DEFAULT_HOST and not allow_remote:
        raise UsageError(
            f"--host {raw!r} is not 127.0.0.1; binding elsewhere needs --unsafe-allow-remote "
            "(REQ-UI-002)"
        )
    return str(address) if address is not None else host


def child_command(host: str, port: int) -> list[str]:
    """``python -m reflex run --env prod`` on one port for UI and backend (DD-016)."""
    return [
        sys.executable,
        "-m",
        "reflex",
        "run",
        "--env",
        "prod",
        "--backend-host",
        host,
        "--frontend-port",
        str(port),
        "--backend-port",
        str(port),
    ]


def child_environment(
    base: Mapping[str, str],
    *,
    host: str,
    port: int,
    config: Path | None,
    unsafe_allow_remote: bool,
    log_json: bool,
) -> dict[str, str]:
    """``base`` without inherited ``REFLEX_MCPHOST_*`` values, plus the DD-016 set."""
    env = {
        k: v
        for k, v in base.items()
        if not k.startswith(ENV_PREFIX) and k not in _DROPPED_CHILD_ENV
    }
    env |= FIXED_CHILD_ENV
    env[ENV_HOST], env[ENV_PORT] = host, str(port)
    if config is not None:
        env[ENV_CONFIG] = str(config)
    if unsafe_allow_remote:
        env[ENV_UNSAFE_ALLOW_REMOTE] = "1"
    if log_json:
        env[ENV_LOG_JSON] = "1"
    return env


def _banner(origin: Origin) -> str:
    rule = "!" * 78
    return (
        f"{rule}\nWARNING: --unsafe-allow-remote: the Host UI will be served at {origin.url}\n"
        "Anyone who can reach that address can drive this Host and its MCP Servers.\n"
        "OAuth authorization and MCP Apps work only from a browser on this machine.\n"
        f"{rule}\n"
    )


# ------------------------------------------------------------------ run


def _preflight(runtime: Runtime, config_path: Path | None) -> None:
    try:
        config = runtime.load_config(config_path)
    except MCPHostError as exc:
        raise StartupError(f"invalid config: {exc}") from None
    try:
        runtime.check_database(config.data_dir)
    except (MCPHostError, OSError, sqlite3.Error) as exc:
        raise StartupError(f"the database in {config.data_dir} cannot be opened: {exc}") from None


@contextmanager
def _termination_signals(enabled: bool) -> Iterator[None]:
    """While running, SIGTERM / SIGHUP raise ``TerminationRequestedError`` (forwarded)."""
    names = ("SIGTERM", "SIGHUP") if os.name == "posix" else ("SIGTERM",)
    sigs = [getattr(signal, n) for n in names]
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: FrameType | None) -> None:
        raise TerminationRequestedError(signal.SIGTERM if os.name == "posix" else INTERRUPT_SIGNAL)

    if enabled:
        for sig in sigs:
            previous[sig] = signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


def _stop(child: ChildProcess, sig: int) -> None:
    """Forward ``sig`` once and wait for the child; kill it after the grace period."""
    child.send_signal(sig)
    while True:
        try:
            child.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        except KeyboardInterrupt:
            continue  # a second Ctrl-C while the Host shuts down: keep waiting, do not resend
        return


def _supervise(runtime: Runtime, child: ChildProcess, origin: Origin, *, browser: bool) -> int:
    ping = f"{origin.url}ping"
    while True:
        code = child.poll()
        if code is not None:
            if code != 0:
                runtime.err.write(
                    f"{PROG}: Reflex exited with code {code} before the UI was ready\n"
                )
                return EXIT_STARTUP
            return EXIT_OK
        if runtime.probe(ping, origin.host_header):
            break
        runtime.sleep(PROBE_INTERVAL_S)
    runtime.out.write(f"{PROG}: the Host UI is ready at {origin.url}\n")
    runtime.out.flush()
    if browser:
        runtime.open_browser(origin.url)
    return child.wait()


def _run(runtime: Runtime, args: argparse.Namespace) -> int:
    host = _check_host(args.host, allow_remote=args.unsafe_allow_remote)
    origin = Origin(host, args.port)
    config_path = Path(args.config).expanduser().resolve() if args.config is not None else None
    configure_logging(json_lines=args.log_json, stream=runtime.err)
    _preflight(runtime, config_path)
    if host != DEFAULT_HOST:
        runtime.err.write(_banner(origin))
    try:
        workspace = runtime.materialize()
    except OSError as exc:
        raise StartupError(f"cannot prepare the app workspace: {exc}") from None
    argv = child_command(host, args.port)
    env = child_environment(
        runtime.environ,
        host=host,
        port=args.port,
        config=config_path,
        unsafe_allow_remote=args.unsafe_allow_remote,
        log_json=args.log_json,
    )
    runtime.out.write(f"{PROG}: starting the Host UI at {origin.url} (workspace {workspace})\n")
    runtime.out.flush()
    try:
        child = runtime.spawn(argv, workspace, env)
    except OSError as exc:
        raise StartupError(f"cannot start Reflex: {exc}") from None
    with _termination_signals(runtime.install_signals):
        try:
            return _supervise(runtime, child, origin, browser=not args.no_browser)
        except TerminationRequestedError as exc:
            _stop(child, exc.signal)
        except KeyboardInterrupt:
            _stop(child, INTERRUPT_SIGNAL)
    return EXIT_OK


# ------------------------------------------------------------------ servers (REQ-UI-008)


def _servers(runtime: Runtime, args: argparse.Namespace) -> int:
    import anyio

    from reflex_mcphost.core.config.models import HostConfig
    from reflex_mcphost.core.registry import ServerRegistry

    path = Path(args.config).expanduser() if args.config is not None else None
    try:
        config = HostConfig.load(path)
    except MCPHostError as exc:
        raise StartupError(f"invalid config: {exc}") from None
    registry = ServerRegistry(config)
    anyio.run(registry.load)
    definitions, shadowed, problems = (
        registry.definitions(),
        registry.shadowed(),
        registry.problems(),
    )
    if args.json:
        payload = {
            "servers": [d.model_dump(mode="json") for d in definitions],
            "shadowed": [s.model_dump(mode="json") for s in shadowed],
            "problems": [p.model_dump(mode="json") for p in problems],
        }
        runtime.out.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        errors = registry.config_errors()
        rows = [
            (d.id, d.transport.kind, d.origin.kind.value, d.enabled, d.id in errors)
            for d in definitions
        ]
        table = [
            [i, kind, origin, "yes" if enabled else "no", "config_error" if bad else "ok"]
            for i, kind, origin, enabled, bad in rows
        ]
        table += [
            [
                s.definition.id,
                s.definition.transport.kind,
                s.definition.origin.kind.value,
                "yes" if s.definition.enabled else "no",
                "shadowed",
            ]
            for s in shadowed
        ]
        _write_table(runtime.out, ["ID", "TRANSPORT", "ORIGIN", "ENABLED", "STATUS"], table)
        if problems:
            runtime.out.write("\nproblems:\n")
            for p in problems:
                runtime.out.write(f"  [{p.code.value}] {p.message}\n")
    return EXIT_PROBLEMS if args.strict and problems else EXIT_OK


def _write_table(out: TextIO, header: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        out.write("  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
        out.write("\n")


# ------------------------------------------------------------------ entry point


def main(argv: Sequence[str] | None = None, *, runtime: Runtime | None = None) -> int:
    """Console-script entry point; returns the exit code (API §6)."""
    runtime = runtime if runtime is not None else Runtime()
    parser = build_parser(runtime.out, runtime.err)
    try:
        args = parser.parse_args(_with_command(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:  # --help / --version (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        if args.command == "servers":
            return _servers(runtime, args)
        return _run(runtime, args)
    except UsageError as exc:
        runtime.err.write(f"{PROG}: error: {exc}\n")
        return EXIT_USAGE
    except StartupError as exc:
        runtime.err.write(f"{PROG}: error: {exc}\n")
        return EXIT_STARTUP
