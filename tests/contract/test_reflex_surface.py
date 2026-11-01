"""Pinned Reflex surface the UI layer and reference app rely on (spike S-5, DD-011/016/017).

Static items of the S-5 *Contract assertions* (#1, #4, #5, #7) are pinned here, plus (F6-01)
the behavioural #1 extension (an async-context-manager lifespan task's exit is awaited past
the coroutine cancel cap, delta D1), #2 (``api_transformer`` sees HTTP and websocket scopes)
and the state-testing seam of Phase 6 (``State(_reflex_internal_init=True)`` +
``EventHandler.fn``; private surface, Art. 2 risk accepted like S-6) and (F6-08) #3, the
``reflex run`` options and prod single-port rule. #6 on_load cancellation and #8 env.json
EVENT URL land with F6-09a / Phase 7.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

rx = pytest.importorskip("reflex")

from reflex.app_mixins import lifespan as reflex_lifespan  # noqa: E402 - after importorskip

from tests.unit.ui.rig import asgi_of, lifespan, new_app, ws_call  # noqa: E402


def test_register_lifespan_task_signature() -> None:
    """S-5 #1: App.register_lifespan_task(self, task=None, **task_kwargs)."""
    params = inspect.signature(rx.App.register_lifespan_task).parameters
    assert list(params) == ["self", "task", "task_kwargs"]
    assert params["task"].default is None
    assert params["task_kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_app_accepts_api_transformer() -> None:
    """S-5 #2 (static part): rx.App has an api_transformer field."""
    assert "api_transformer" in {f.name for f in dataclasses.fields(rx.App)}


def test_config_fields_and_unsafe_defaults() -> None:
    """S-5 #4: Config fields exist; defaults use localhost / 0.0.0.0, so rxconfig must override."""
    from reflex_base.config import Config

    config = Config(app_name="contract")
    assert config.backend_host == "0.0.0.0"  # noqa: S104 - pinning the unsafe default
    assert "localhost" in config.api_url
    for name in ("deploy_url", "cors_allowed_origins", "telemetry_enabled"):
        assert hasattr(config, name), name


def test_single_worker_without_redis() -> None:
    """S-5 #5: one backend worker when no Redis is configured."""
    from reflex.utils import processes

    assert processes.get_num_workers() == 1


def test_environment_variables_and_bun_version() -> None:
    """S-5 #7: env knobs used by DD-016 exist; Bun version is informational (cold start)."""
    from reflex_base.constants.installer import Bun
    from reflex_base.environment import EnvironmentVariables

    for name in (
        "REFLEX_DIR",
        "REFLEX_WEB_WORKDIR",
        "REFLEX_CHECK_LATEST_VERSION",
        "REFLEX_MOUNT_FRONTEND_COMPILED_APP",
    ):
        assert hasattr(EnvironmentVariables, name), name
    assert Bun.VERSION == "1.4.4"


# ------------------------------------------------------------------ F6-01 behavioural pins


@pytest.mark.anyio
async def test_async_context_manager_lifespan_exit_outlives_the_cancel_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-CON-009 (shutdown budget kept), S-5 #1 extended (delta D1): Reflex enters and
    exits an async-context-manager lifespan task on its exit stack in one task and awaits
    the exit to completion even when it outlasts ``_LIFESPAN_TASK_CANCEL_TIMEOUT`` (5 s in
    Reflex 0.10.1, scaled down here so the test stays short), whereas a coroutine task is
    abandoned at that cap. The cap's name and value are pinned too."""
    assert reflex_lifespan._LIFESPAN_TASK_CANCEL_TIMEOUT == 5
    cap = 0.2
    monkeypatch.setattr(reflex_lifespan, "_LIFESPAN_TASK_CANCEL_TIMEOUT", cap)
    seen: dict[str, object] = {}

    @contextlib.asynccontextmanager
    async def owner() -> AsyncIterator[None]:
        seen["enter"] = asyncio.current_task()
        yield
        await asyncio.sleep(cap * 3)  # longer than the cap: a slow Host.aclose()
        seen["exit"] = asyncio.current_task()

    async def coroutine_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(cap * 10)  # cleanup longer than the cap
            seen["coroutine_cleanup_done"] = True
            raise

    app = new_app()
    app.register_lifespan_task(owner)
    app.register_lifespan_task(coroutine_task)
    async with lifespan(app):
        await asyncio.sleep(0)
    assert seen["exit"] is seen["enter"]  # entered and exited in one task, not cut short
    assert "coroutine_cleanup_done" not in seen  # the coroutine task was abandoned at the cap


@pytest.mark.anyio
async def test_api_transformer_sees_http_and_websocket_scopes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REQ-NFR-011 (guard placement), S-5 #2 behavioural: a transformer wraps the backend
    app on the path of HTTP requests (``/ping``) and of the ``/_event`` websocket."""
    scopes: list[tuple[str, str]] = []

    def recorder(inner: object) -> object:
        async def wrapped(scope: dict[str, object], receive: object, send: object) -> None:
            scopes.append((str(scope["type"]), str(scope.get("path"))))
            await inner(scope, receive, send)  # type: ignore[operator]

        return wrapped

    asgi = asgi_of(new_app(api_transformer=recorder), monkeypatch, tmp_path)
    transport = httpx.ASGITransport(app=asgi)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
        assert (await c.get("/ping")).status_code == 200
    await ws_call(asgi, {"host": "127.0.0.1:8000"})
    assert ("http", "/ping") in scopes
    assert ("websocket", "/_event/") in scopes


class _SeamState(rx.State):
    count: int = 0

    @rx.event
    def bump(self, by: int) -> None:
        self.count += by


def test_state_testing_seam() -> None:
    """REQ-UI-007 (state testable in ``ui``): a state is built with
    ``State(_reflex_internal_init=True)`` and a handler runs as ``State.handler.fn(state,
    ...)`` — the seam every Phase 6 state test uses (private surface, pinned here)."""
    state = _SeamState(_reflex_internal_init=True)
    handler = _SeamState.bump
    assert callable(handler.fn)
    handler.fn(state, 3)
    assert state.count == 3
    assert isinstance(state.router.session.client_token, str)


def test_reflex_run_options_and_prod_single_port_rule() -> None:
    """S-5 #3 (F6-08, DD-016): ``reflex run`` takes ``--env {dev,preview,prod}``,
    ``--frontend-port``, ``--backend-port``, ``--backend-host`` and ``--single-port``; in prod,
    different frontend and backend ports exit with code 1 before any config is read."""
    from reflex import constants
    from reflex import reflex as reflex_cli

    options = {opt for param in reflex_cli.run.params for opt in getattr(param, "opts", ())}
    assert {
        "--env",
        "--frontend-port",
        "--backend-port",
        "--backend-host",
        "--single-port",
    } <= options
    env_param = next(p for p in reflex_cli.run.params if "--env" in getattr(p, "opts", ()))
    assert {"dev", "preview", "prod"} <= set(env_param.type.choices)
    with pytest.raises(SystemExit) as exc:
        reflex_cli._run(env=constants.Env.PROD, frontend_port=3001, backend_port=3002)
    assert exc.value.code == 1


# ------------------------------------------------------------------ F7-04 / F7-05 pins


def test_local_js_component_mechanism(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """REQ-APP-003 (relay), F7-04: the local-JS-component mechanism of the MCP App relay —
    ``rx.asset(path, shared=True)`` resolves the file next to the calling module, links it
    into ``./assets/external/<module path>/`` and returns an ``AssetPathStr`` whose
    ``importable_path`` (``$/public/external/...``, unversioned) is a component ``library``;
    ``library`` is a per-instance component field; ``passthrough_event_spec`` exists; and
    ``compile_page`` imports the tag from that path."""
    from reflex.assets import AssetPathStr
    from reflex.compiler.compiler import compile_page

    import reflex_mcphost.ui.assets as assets
    from reflex_mcphost.ui.assets import RELAY_FILE, RELAY_LIBRARY, relay_library

    monkeypatch.chdir(tmp_path)
    assert relay_library() == RELAY_LIBRARY  # outside an app directory: nothing is linked
    assert not (tmp_path / "assets").exists()
    (tmp_path / "rxconfig.py").touch()
    assert assets._in_app_dir()
    monkeypatch.setattr(assets, "_in_app_dir", lambda: True)  # as in an app directory
    monkeypatch.delenv("REFLEX_BACKEND_ONLY", raising=False)
    params = inspect.signature(rx.asset).parameters
    assert list(params)[:3] == ["path", "shared", "subfolder"]
    assert hasattr(AssetPathStr, "importable_path")
    library = relay_library()
    assert library == RELAY_LIBRARY == f"$/public/external/reflex_mcphost/ui/assets/{RELAY_FILE}"
    assert (
        tmp_path / "assets" / "external" / "reflex_mcphost" / "ui" / "assets" / RELAY_FILE
    ).exists()
    assert "library" in rx.Component.get_fields()
    assert callable(rx.event.passthrough_event_spec)

    class Probe(rx.Component):
        tag = "Probe"

    _, code = compile_page("probe", Probe.create(library=library))
    assert f'import {{Probe}} from "{library}"' in code


def test_external_redirect_opens_with_noopener() -> None:
    """REQ-APP-015 (F7-05; F7-06b checks the browser side): ``rx.redirect(url,
    is_external=True)`` is the ``_redirect`` event with ``external``; the frontend opens it
    with ``window.open(path, "_blank", "noopener")`` — ``noopener`` is guaranteed,
    ``noreferrer`` is not set by Reflex 0.10.1 (recorded deviation)."""
    import reflex_base

    spec = rx.redirect("https://example.com/", is_external=True)
    args = {str(name): value._var_value for name, value in spec.args}
    assert args["external"] is True
    state_js = (
        Path(reflex_base.__file__).parent / ".templates" / "web" / "utils" / "state.js"
    ).read_text(encoding="utf-8")
    block = state_js.split('if (event.name == "_redirect")', 1)[1].split("const url", 1)[0]
    assert "window.open(" in block
    assert '"_blank"' in block
    assert '"noopener"' in block
    assert "noreferrer" not in block


def test_disconnect_cancels_only_the_on_load_chain() -> None:
    """REQ-APP-010 (tab disconnect discards the View), S-5 #6 source pin: Reflex cancels a
    disconnected token's ``on_load`` chain (where the watcher — and so the tab's MCP App
    View — runs), not independent background events."""
    from reflex_base.event.processor import base_state_processor

    source = inspect.getsource(base_state_processor.BaseStateEventProcessor._on_disconnect)
    assert "_pending_hydrates" in source
    assert "_superseded.pop((on_load_name, token)" in source
