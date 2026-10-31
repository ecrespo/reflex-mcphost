"""Pinned Reflex surface the UI layer and reference app rely on (spike S-5, DD-011/016/017).

Static items of the S-5 *Contract assertions* (#1, #4, #5, #7) are pinned here; the
behavioural ones (#2 api_transformer on HTTP + websocket, #3 CLI port rules, #6 on_load
cancellation, #8 env.json EVENT URL) need the app and land with Phase 6/7 (F6-*, F7-*).
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

rx = pytest.importorskip("reflex")


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
