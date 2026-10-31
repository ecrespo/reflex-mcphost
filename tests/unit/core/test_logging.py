"""Logging and redaction (REQ-NFR-040, REQ-REG-007, REQ-NFR-010)."""

from __future__ import annotations

import logging

import pytest

import reflex_mcphost
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.secrets import MASK, default_registry

SECRET = "seeded-secret-value-0123456789"  # noqa: S105 - test seed, not a credential


@pytest.fixture
def secret() -> object:
    default_registry.register(SECRET)
    yield SECRET
    default_registry.forget(SECRET)


def _all_text(caplog: pytest.LogCaptureFixture) -> str:
    handler = logging.StreamHandler()
    parts = [caplog.text]
    for rec in caplog.records:
        parts.append(handler.format(rec))
        parts.extend(str(v) for v in vars(rec).values())
    return "\n".join(parts)


def test_library_installs_only_a_null_handler() -> None:
    """REQ-NFR-040: importing the package configures no real handler."""
    assert reflex_mcphost.__name__ == "reflex_mcphost"
    handlers = logging.getLogger("reflex_mcphost").handlers
    assert handlers
    assert all(isinstance(h, logging.NullHandler) for h in handlers)


def test_get_logger_namespaces_under_package() -> None:
    """REQ-NFR-040: loggers live under the reflex_mcphost namespace."""
    assert get_logger("invoker").name == "reflex_mcphost.invoker"
    assert get_logger("reflex_mcphost.core.db").name == "reflex_mcphost.core.db"


def test_context_adapter_adds_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-NFR-040: server_id, fqn and invocation_id are attached to records."""
    log = ContextAdapter(get_logger("t"), server_id="github", fqn="github::x", era="modern")
    with caplog.at_level(logging.INFO, logger="reflex_mcphost"):
        log.bind(invocation_id="inv_1").info("finished", extra={"outcome": "success"})
    rec = caplog.records[-1]
    assert (rec.server_id, rec.fqn, rec.era) == ("github", "github::x", "modern")  # type: ignore[attr-defined]
    assert rec.invocation_id == "inv_1"  # type: ignore[attr-defined]
    assert rec.outcome == "success"  # type: ignore[attr-defined]


def test_secret_never_reaches_records(caplog: pytest.LogCaptureFixture, secret: str) -> None:
    """REQ-REG-007, REQ-NFR-010: message, %s args, extras and exception text are redacted."""
    log = get_logger("t")
    with caplog.at_level(logging.DEBUG, logger="reflex_mcphost"):
        log.info(f"inline {secret}")
        log.info("arg %s", secret)
        log.info("dict %(k)s", {"k": secret})
        log.info("extra", extra={"header_value": f"Bearer {secret}", "nested": {"t": secret}})
        try:
            raise RuntimeError(f"failed with {secret}")
        except RuntimeError:
            log.exception("boom")
        log.info("stack", stack_info=True)
        ContextAdapter(log, server_id="s").warning("adapter %s", secret)
    text = _all_text(caplog)
    assert secret not in text
    assert text.count(MASK) >= 6
