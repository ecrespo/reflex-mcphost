"""Logging and redaction (REQ-NFR-040, REQ-REG-007, REQ-NFR-010)."""

from __future__ import annotations

import logging

import pytest

import reflex_mcphost
from reflex_mcphost.core.logging import (
    PAYLOAD_OMITTED,
    SDK_LOGGERS,
    ContextAdapter,
    get_logger,
    protect_sdk_loggers,
)
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


# ---------------------------------------------------------------- SDK log hygiene (F4-09b)

ARG = "arg-secret-sdk-hygiene-0001"


def test_sdk_payload_logs_are_stripped(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-NFR-010, Art. 5: an SDK transport record carrying a JSON-RPC message (tool
    arguments, elicitation answers, sampling text the Host cannot know are secret) keeps
    only its prefix; records without a payload pass unchanged; no handler is installed."""
    protect_sdk_loggers()
    protect_sdk_loggers()  # idempotent
    logger = logging.getLogger("mcp.client.streamable_http")
    assert logger.handlers == []
    caplog.set_level(logging.DEBUG)
    logger.debug(
        "Sending client message: jsonrpc='2.0' id=6 method='tools/call' "
        "params={'arguments': {'password': '%s'}}",
        ARG,
    )
    logger.debug("aborting in-flight POST for cancelled request %r", "listen-2")
    messages = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert messages == [
        f"Sending client message: {PAYLOAD_OMITTED}",
        "aborting in-flight POST for cancelled request 'listen-2'",
    ]
    assert ARG not in _all_text(caplog)


def test_sdk_exception_payloads_are_stripped(caplog: pytest.LogCaptureFixture, secret: str) -> None:
    """REQ-NFR-010: an SDK exception whose text carries a payload keeps only its type name;
    registered secrets are masked in every SDK record (including the session's ``client``
    logger)."""
    protect_sdk_loggers()
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("client")  # mcp.client.session's logger name
    try:
        raise ValueError(f"1 validation error\n  input_value={{'name': '{ARG}'}}")
    except ValueError:
        logger.exception("client callback for %r returned an invalid result", "elicitation")
    logger.warning("refresh failed for %s", secret)
    text = _all_text(caplog)
    assert ARG not in text
    assert secret not in text
    assert "ValueError" in text
    assert MASK in text


def test_protected_sdk_loggers_cover_client_and_shared() -> None:
    """REQ-NFR-010: every ``mcp.client``/``mcp.shared`` transport module logger the Host
    drives is protected (the full list is pinned by the SDK contract test)."""
    assert {"mcp.client.streamable_http", "mcp.client.stdio", "client"} <= set(SDK_LOGGERS)
