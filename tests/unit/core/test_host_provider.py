"""``[sampling] provider`` loading (F4-02/F4-05; API §5.2, REQ-NFR-020)."""

from __future__ import annotations

import logging

import pytest

from reflex_mcphost.core.handlers.sampling import SamplingProvider
from reflex_mcphost.core.host import _load_provider

pytestmark = pytest.mark.anyio


async def test_factory_spec_loads_a_provider() -> None:
    """API §5.2: ``package.module:factory`` is imported and called."""
    provider = await _load_provider("tests.integration.handlers.helpers:FakeProvider")
    assert isinstance(provider, SamplingProvider)


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        ("no-colon", "ValueError"),
        ("reflex_mcphost_missing_module:factory", "ModuleNotFoundError"),
        ("builtins:object", "TypeError"),
    ],
)
async def test_bad_spec_disables_sampling(
    spec: str, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-NFR-020: a provider that cannot be loaded disables sampling (None) and is logged
    with its exception type; the Host keeps running."""
    with caplog.at_level(logging.ERROR):
        assert await _load_provider(spec) is None
    assert any(
        "sampling disabled" in r.getMessage() and kind in r.getMessage() for r in caplog.records
    )
