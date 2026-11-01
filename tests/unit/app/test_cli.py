"""``reflex-mcphost run`` (F6-08; API §6, DD-016, DD-017).

The Reflex child, the readiness probe, the browser opener and the workspace are fakes on a
``Runtime``; the pre-flight (``HostConfig.aload`` + opening the database) is real, on a
temporary config and data dir. Nothing compiles a Reflex app or opens a browser.
"""

from __future__ import annotations

import io
import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from reflex_mcphost import __version__
from reflex_mcphost.app import cli
from reflex_mcphost.app.cli import Runtime, main

PORT = 3123
DD016 = {
    "GRANIAN_WORKERS",
    "REFLEX_CHECK_LATEST_VERSION",
    "REFLEX_MCPHOST_HOST",
    "REFLEX_MCPHOST_PORT",
    "REFLEX_MCPHOST_CONFIG",
    "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE",
    "REFLEX_MCPHOST_LOG_JSON",
}


@dataclass
class FakeChild:
    """A Reflex child: ``exit_before`` = code returned by poll() before the probe answers;
    ``wait_script`` = what each wait() call does (int → return it, exception → raise it)."""

    exit_before: int | None = None
    wait_script: list[Any] = field(default_factory=lambda: [0])
    signals: list[int] = field(default_factory=list)
    killed: bool = False
    returncode: int | None = None

    def poll(self) -> int | None:
        if self.exit_before is not None:
            self.returncode = self.exit_before
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        step = self.wait_script.pop(0) if self.wait_script else 0
        if callable(step) and not isinstance(step, type):
            step = step()
        if isinstance(step, BaseException) or (
            isinstance(step, type) and issubclass(step, BaseException)
        ):
            raise step
        self.returncode = int(step)
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def kill(self) -> None:
        self.killed = True


@dataclass
class Rig:
    tmp_path: Path
    child: FakeChild = field(default_factory=FakeChild)
    probe_answers: list[bool] = field(default_factory=lambda: [False, True])
    spawned: list[tuple[list[str], Path, dict[str, str]]] = field(default_factory=list)
    probes: list[tuple[str, str]] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    environ: dict[str, str] = field(default_factory=lambda: {"PATH": "/usr/bin", "LANG": "C"})
    stdout: io.StringIO = field(default_factory=io.StringIO)
    stderr: io.StringIO = field(default_factory=io.StringIO)

    @property
    def workspace(self) -> Path:
        return self.tmp_path / "workspace"

    def runtime(self) -> Runtime:
        def spawn(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> FakeChild:
            self.events.append("spawn")
            self.spawned.append((list(argv), cwd, dict(env)))
            return self.child

        def probe(url: str, host_header: str) -> bool:
            self.probes.append((url, host_header))
            return self.probe_answers.pop(0) if self.probe_answers else True

        def open_browser(url: str) -> bool:
            self.events.append("browser")
            self.opened.append(url)
            return True

        def materialize() -> Path:
            self.events.append("materialize")
            self.workspace.mkdir(exist_ok=True)
            return self.workspace

        return Runtime(
            spawn=spawn,
            probe=probe,
            open_browser=open_browser,
            materialize=materialize,
            sleep=lambda _s: None,
            environ=self.environ,
            stdout=self.stdout,
            stderr=self.stderr,
            install_signals=False,
        )

    def config(self, body: str = "") -> Path:
        path = self.tmp_path / "mcphost.toml"
        data = (self.tmp_path / "data").as_posix()
        path.write_text(f'schema_version = 1\n[host]\ndata_dir = "{data}"\n{body}', "utf-8")
        return path

    def run(self, *args: str) -> int:
        return main(list(args), runtime=self.runtime())


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


@pytest.fixture(autouse=True)
def _quiet_logging() -> Iterator[None]:
    logger = logging.getLogger("reflex_mcphost")
    timing = logging.getLogger("reflex_mcphost.ui.timing")  # JSON mode sets it (F8-02)
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    timing_level = timing.level
    yield
    logger.handlers[:] = handlers
    logger.propagate, logger.level = propagate, level
    timing.setLevel(timing_level)


# ------------------------------------------------------------------ argument rules (exit 2)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "[::]"])  # noqa: S104 - refused hosts
def test_wildcard_host_is_refused_even_with_the_flag(rig: Rig, host: str) -> None:
    """REQ-UI-002, REQ-NFR-011: a wildcard host is exit 2 even with --unsafe-allow-remote."""
    code = rig.run("run", "--host", host, "--unsafe-allow-remote", "--config", str(rig.config()))
    assert code == 2
    assert "wildcard" in rig.stderr.getvalue()
    assert rig.spawned == []


def test_non_loopback_host_without_the_flag_is_refused(rig: Rig) -> None:
    """REQ-UI-002: a non-loopback --host without --unsafe-allow-remote → exit 2, no child."""
    code = rig.run("run", "--host", "192.0.2.1", "--config", str(rig.config()))
    assert code == 2
    assert "--unsafe-allow-remote" in rig.stderr.getvalue()
    assert rig.spawned == []
    assert rig.events == []


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "app.localhost"])
def test_localhost_is_always_refused(rig: Rig, host: str) -> None:
    """REQ-NFR-011: localhost is reserved for the MCP Apps sandbox proxy → exit 2."""
    code = rig.run("run", "--host", host, "--unsafe-allow-remote", "--config", str(rig.config()))
    assert code == 2
    assert "localhost" in rig.stderr.getvalue()


def test_invalid_host_is_refused(rig: Rig) -> None:
    """API §6: a --host that is neither an IP nor a host name → exit 2."""
    code = rig.run("run", "--host", "bad host/x", "--unsafe-allow-remote")
    assert code == 2


@pytest.mark.parametrize("port", ["0", "65536", "http"])
def test_invalid_port_is_a_usage_error(rig: Rig, port: str) -> None:
    """API §6: usage errors exit 2."""
    assert rig.run("run", "--port", port) == 2


def test_unknown_option_is_a_usage_error(rig: Rig) -> None:
    """API §6: an unknown option exits 2 (argparse) and starts nothing."""
    assert rig.run("run", "--frontend-port", "3001") == 2
    assert rig.events == []


def test_remote_mode_prints_a_banner_and_sets_the_opt_in(rig: Rig) -> None:
    """REQ-UI-002: with the flag a concrete host starts, warns and sets the env opt-in."""
    code = rig.run(
        "run", "--host", "192.0.2.1", "--unsafe-allow-remote", "--config", str(rig.config())
    )
    assert code == 0
    err = rig.stderr.getvalue()
    assert "WARNING" in err
    assert "http://192.0.2.1:3000/" in err
    (argv, _cwd, env) = rig.spawned[0]
    assert env["REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE"] == "1"
    assert env["REFLEX_MCPHOST_HOST"] == "192.0.2.1"
    assert argv[argv.index("--backend-host") + 1] == "192.0.2.1"
    assert rig.probes[0] == ("http://192.0.2.1:3000/ping", "192.0.2.1:3000")
    assert rig.opened == ["http://192.0.2.1:3000/"]


def test_remote_ipv6_host_is_bracketed(rig: Rig) -> None:
    """REQ-NFR-011: a concrete IPv6 host gets the [host]:port origin."""
    rig.run("run", "--host", "2001:db8::5", "--unsafe-allow-remote", "--config", str(rig.config()))
    assert rig.probes[0] == ("http://[2001:db8::5]:3000/ping", "[2001:db8::5]:3000")


# ------------------------------------------------------------------ pre-flight (exit 3)


def test_invalid_explicit_config_is_exit_3(rig: Rig) -> None:
    """API §6: an invalid --config file → exit 3 before the workspace or the child."""
    bad = rig.tmp_path / "bad.toml"
    bad.write_text("schema_version = [", encoding="utf-8")
    assert rig.run("run", "--config", str(bad)) == 3
    assert "bad.toml" in rig.stderr.getvalue()
    assert rig.events == []


def test_missing_explicit_config_is_exit_3(rig: Rig) -> None:
    """API §6: an explicit --config that does not exist is an invalid own config → 3."""
    assert rig.run("run", "--config", str(rig.tmp_path / "absent.toml")) == 3
    assert rig.events == []


def test_database_open_failure_is_exit_3(rig: Rig) -> None:
    """API §6: the database in the configured data dir must open, else exit 3."""
    blocker = rig.tmp_path / "data"
    blocker.write_text("not a directory", encoding="utf-8")
    assert rig.run("run", "--config", str(rig.config())) == 3
    assert "database" in rig.stderr.getvalue()
    assert rig.events == []


def test_preflight_opens_and_closes_the_database(rig: Rig) -> None:
    """DD-016: the pre-flight creates the data dir and the DB file, then closes it."""
    assert rig.run("run", "--no-browser", "--config", str(rig.config())) == 0
    assert (rig.tmp_path / "data" / "mcphost.db").is_file()


def test_workspace_failure_is_exit_3(rig: Rig) -> None:
    """DD-016: a workspace that cannot be materialized is a fatal start-up error."""
    runtime = rig.runtime()

    def broken() -> Path:
        raise OSError("disk full")

    runtime.materialize = broken
    assert main(["run", "--config", str(rig.config())], runtime=runtime) == 3
    assert "workspace" in rig.stderr.getvalue()


def test_spawn_failure_is_exit_3(rig: Rig) -> None:
    """API §6: Reflex that cannot be started at all is a fatal start-up error."""
    runtime = rig.runtime()

    def broken(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> Any:
        raise FileNotFoundError("python")

    runtime.spawn = broken
    assert main(["run", "--config", str(rig.config())], runtime=runtime) == 3


# ------------------------------------------------------------------ the child (DD-016)


def test_child_argv_cwd_and_environment_are_the_dd016_set(rig: Rig) -> None:
    """DD-016: `python -m reflex run --env prod` on one port in the workspace, options only
    through REFLEX_MCPHOST_* variables, GRANIAN_WORKERS=1, no version check."""
    config = rig.config()
    rig.environ |= {
        "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE": "1",  # inherited: must not leak through
        "REFLEX_MCPHOST_STRAY": "x",
        "REFLEX_BACKEND_ONLY": "true",
    }
    assert rig.run("run", "--port", str(PORT), "--config", str(config), "--log-json") == 0
    (argv, cwd, env) = rig.spawned[0]
    assert argv == [
        sys.executable,
        "-m",
        "reflex",
        "run",
        "--env",
        "prod",
        "--backend-host",
        "127.0.0.1",
        "--frontend-port",
        str(PORT),
        "--backend-port",
        str(PORT),
    ]
    assert cwd == rig.workspace
    added = {k: v for k, v in env.items() if k not in {"PATH", "LANG"}}
    assert added == {
        "GRANIAN_WORKERS": "1",
        "REFLEX_CHECK_LATEST_VERSION": "false",
        "REFLEX_MCPHOST_HOST": "127.0.0.1",
        "REFLEX_MCPHOST_PORT": str(PORT),
        "REFLEX_MCPHOST_CONFIG": str(config.resolve()),
        "REFLEX_MCPHOST_LOG_JSON": "1",
    }
    assert set(added) <= DD016
    assert env["PATH"] == "/usr/bin"


def test_child_environment_without_config_or_flags(rig: Rig) -> None:
    """DD-016: REFLEX_MCPHOST_CONFIG, _UNSAFE_ALLOW_REMOTE and _LOG_JSON are unset by default."""
    runtime = rig.runtime()
    runtime.load_config = _fake_load_config(rig.tmp_path)
    assert main(["run", "--no-browser"], runtime=runtime) == 0
    env = rig.spawned[0][2]
    for name in (
        "REFLEX_MCPHOST_CONFIG",
        "REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE",
        "REFLEX_MCPHOST_LOG_JSON",
    ):
        assert name not in env
    assert env["REFLEX_MCPHOST_PORT"] == "3000"


def test_relative_config_path_is_passed_absolute(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """DD-016: REFLEX_MCPHOST_CONFIG is an absolute path (the child runs elsewhere)."""
    config = rig.config()
    monkeypatch.chdir(rig.tmp_path)
    assert rig.run("run", "--no-browser", "--config", config.name) == 0
    assert rig.spawned[0][2]["REFLEX_MCPHOST_CONFIG"] == str(config.resolve())


def test_no_secret_reaches_argv_or_environment(rig: Rig) -> None:
    """Art. 5: config secrets never reach the child's argv or the variables we add."""
    sentinel = "sentinel-value-not-a-secret-7f3a"
    config = rig.config(
        f'[servers.files]\ntransport = "stdio"\ncommand = "x"\nenv = {{ TOKEN = "{sentinel}" }}\n'
    )
    assert rig.run("run", "--no-browser", "--config", str(config)) == 0
    (argv, _cwd, env) = rig.spawned[0]
    assert sentinel not in " ".join(argv)
    assert all(sentinel not in v for v in env.values())
    assert sentinel not in rig.stdout.getvalue() + rig.stderr.getvalue()


# ------------------------------------------------------------------ readiness and browser


def test_browser_opened_once_after_the_probe_answers(rig: Rig) -> None:
    """REQ-UI-001: the URL is printed and opened once, only after GET /ping answered."""
    rig.probe_answers = [False, False, True]
    assert rig.run("run", "--port", str(PORT), "--config", str(rig.config())) == 0
    url = f"http://127.0.0.1:{PORT}/"
    assert rig.probes == [(f"{url}ping", f"127.0.0.1:{PORT}")] * 3
    assert rig.opened == [url]
    assert rig.events == ["materialize", "spawn", "browser"]
    assert url in rig.stdout.getvalue()


def test_no_browser_never_opens_but_prints_the_url(rig: Rig) -> None:
    """REQ-UI-001: --no-browser skips the browser; the URL is always printed."""
    assert rig.run("run", "--no-browser", "--config", str(rig.config())) == 0
    assert rig.opened == []
    assert "http://127.0.0.1:3000/" in rig.stdout.getvalue()


def test_child_failing_before_ready_is_exit_3(rig: Rig) -> None:
    """API §6: Reflex exiting non-zero before the UI is ready → exit 3, no browser."""
    rig.child = FakeChild(exit_before=1)
    rig.probe_answers = [False] * 5
    assert rig.run("run", "--config", str(rig.config())) == 3
    assert rig.opened == []
    assert "before the UI was ready" in rig.stderr.getvalue()


def test_child_exit_after_ready_is_its_code(rig: Rig) -> None:
    """API §6: after readiness the CLI exits with Reflex's code."""
    rig.child = FakeChild(wait_script=[4])
    assert rig.run("run", "--no-browser", "--config", str(rig.config())) == 4


def test_ctrl_c_after_ready_forwards_sigint_and_exits_0(rig: Rig) -> None:
    """API §6: Ctrl-C is forwarded once; the CLI waits for the child and exits 0."""
    rig.child = FakeChild(wait_script=[KeyboardInterrupt, -2])
    assert rig.run("run", "--config", str(rig.config())) == 0
    assert rig.child.signals == [cli.INTERRUPT_SIGNAL]
    assert rig.child.killed is False


def test_ctrl_c_before_ready_also_exits_0(rig: Rig) -> None:
    """API §6: a Ctrl-C during the first compile ends the child and exits 0, no browser."""
    runtime = rig.runtime()

    def probe(url: str, host_header: str) -> bool:
        raise KeyboardInterrupt

    runtime.probe = probe
    rig.child = FakeChild(wait_script=[-2])
    assert main(["run", "--config", str(rig.config())], runtime=runtime) == 0
    assert rig.child.signals == [cli.INTERRUPT_SIGNAL]
    assert rig.opened == []


def test_child_ignoring_the_signal_is_killed(rig: Rig) -> None:
    """A child that does not stop within the grace period is killed; still exit 0."""
    rig.child = FakeChild(
        wait_script=[KeyboardInterrupt, subprocess.TimeoutExpired("reflex", 1), -9]
    )
    assert rig.run("run", "--no-browser", "--config", str(rig.config())) == 0
    assert rig.child.killed is True


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM delivery to self is POSIX-only")
def test_sigterm_is_forwarded_and_exits_0(rig: Rig) -> None:
    """API §6: SIGTERM to the CLI is forwarded to the child; exit 0 after it ends."""
    previous = signal.getsignal(signal.SIGTERM)
    rig.child = FakeChild(wait_script=[lambda: os.kill(os.getpid(), signal.SIGTERM) or 0, 0])
    runtime = rig.runtime()
    runtime.install_signals = True
    assert main(["run", "--no-browser", "--config", str(rig.config())], runtime=runtime) == 0
    assert rig.child.signals == [signal.SIGTERM]
    assert signal.getsignal(signal.SIGTERM) == previous


# ------------------------------------------------------------------ bare command, version


def _fake_load_config(tmp_path: Path) -> Callable[[Path | None], Any]:
    from reflex_mcphost import HostConfig

    def load(path: Path | None) -> Any:
        return HostConfig(data_dir=tmp_path / "data", config_path=tmp_path / "user.toml")

    return load


def test_bare_command_behaves_as_run(rig: Rig) -> None:
    """REQ-UI-001: `reflex-mcphost` alone starts the app on 127.0.0.1:3000 and opens it."""
    runtime = rig.runtime()
    runtime.load_config = _fake_load_config(rig.tmp_path)
    assert main([], runtime=runtime) == 0
    assert rig.opened == ["http://127.0.0.1:3000/"]
    assert rig.spawned[0][0][-1] == "3000"


def test_options_without_subcommand_mean_run(rig: Rig) -> None:
    """API §6: `reflex-mcphost --no-browser --port P` is `run --no-browser --port P`."""
    assert rig.run("--no-browser", "--port", str(PORT), "--config", str(rig.config())) == 0
    assert rig.opened == []
    assert rig.spawned[0][2]["REFLEX_MCPHOST_PORT"] == str(PORT)


def test_version(rig: Rig) -> None:
    """API §6: --version prints the installed version and exits 0."""
    assert rig.run("--version") == 0
    assert rig.stdout.getvalue().strip() == f"reflex-mcphost {__version__}"


def test_help_exits_0(rig: Rig) -> None:
    """API §6: --help is not an error."""
    assert rig.run("--help") == 0
    assert "servers" in rig.stdout.getvalue()


# ------------------------------------------------------------------ console logging


def test_log_json_writes_json_lines(rig: Rig) -> None:
    """TD §7.1: --log-json → one JSON object per log line with ts, level, logger, msg."""
    stream = io.StringIO()
    cli.configure_logging(json_lines=True, stream=stream)
    logging.getLogger("reflex_mcphost.test").info(
        "invocation %s", "finished", extra={"server_id": "files"}
    )
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["level"] == "INFO"
    assert record["logger"] == "reflex_mcphost.test"
    assert record["msg"] == "invocation finished"
    assert record["server_id"] == "files"
    assert record["ts"].endswith("Z")


def test_text_logging_and_secret_redaction(rig: Rig) -> None:
    """REQ-NFR-010: the console handler masks registered secrets (text mode)."""
    from reflex_mcphost.core.secrets import default_registry

    sentinel = "sentinel-console-value-9c2e"
    default_registry.register(sentinel)
    stream = io.StringIO()
    cli.configure_logging(json_lines=False, stream=stream)
    cli.configure_logging(json_lines=False, stream=stream)  # idempotent: one handler
    logging.getLogger("reflex_mcphost.test").warning("value=%s", sentinel)
    out = stream.getvalue()
    assert sentinel not in out
    assert "**********" in out
    assert out.count("value=") == 1


def test_json_logging_serializes_exceptions(rig: Rig) -> None:
    """TD §7.1: an exception becomes an ``exc`` string in the JSON line."""
    stream = io.StringIO()
    cli.configure_logging(json_lines=True, stream=stream)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("reflex_mcphost.test").exception("failed")
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "ValueError: boom" in record["exc"]


# ------------------------------------------------------------------ defaults


def test_default_probe_sends_the_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """DD-016: the probe is GET /ping with the allowed Host, no proxy; errors mean "not yet"."""
    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200 if request.url.path == "/ping" else 404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cli, "_probe_transport", lambda: transport)
    assert cli.probe_ping("http://127.0.0.1:3123/ping", "127.0.0.1:3123") is True
    assert seen[0].headers["host"] == "127.0.0.1:3123"
    assert cli.probe_ping("http://127.0.0.1:3123/nope", "127.0.0.1:3123") is False

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(cli, "_probe_transport", lambda: httpx.MockTransport(refuse))
    assert cli.probe_ping("http://127.0.0.1:3123/ping", "127.0.0.1:3123") is False


def test_default_spawn_isolates_the_child_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real spawn starts Reflex in its own process group, so a terminal Ctrl-C
    reaches only the CLI, which forwards it exactly once."""
    calls: list[dict[str, Any]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            calls.append({"argv": argv, **kwargs})

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    cli.spawn_child(["python", "-m", "reflex"], tmp_path, {"A": "1"})
    (call,) = calls
    assert call["cwd"] == tmp_path
    assert call["env"] == {"A": "1"}
    if os.name == "posix":
        assert call["start_new_session"] is True
    else:  # pragma: no cover - Windows CI
        assert call["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
