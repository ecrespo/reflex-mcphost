"""SDK auth logger hygiene (delta D2, TD §6.1/§7.1; F4-07)."""

from __future__ import annotations

import logging

import pytest
from mcp.client.auth import OAuthFlowError, OAuthTokenError

from reflex_mcphost.core.auth.oauth import (
    SDK_AUTH_LOGGERS,
    AuthorizationDeferredError,
    protect_sdk_auth_loggers,
)
from reflex_mcphost.core.secrets import register_secret

STATE = "state-secret-hygiene-0001"
TOKEN_BODY = "access-secret-hygiene-0002"  # noqa: S105 - test sentinel


def test_sdk_exception_text_is_stripped(caplog: pytest.LogCaptureFixture) -> None:
    """REQ-AUTH-008, Art. 5: an ``mcp.client.auth`` record with an OAuth exception keeps
    only the exception type name — no message text, args or traceback."""
    protect_sdk_auth_loggers()
    protect_sdk_auth_loggers()  # idempotent
    logger = logging.getLogger("mcp.client.auth.oauth2")
    assert logger.handlers == []  # the library installs no handler
    caplog.set_level(logging.DEBUG)
    for exc in (
        OAuthFlowError(f"State parameter mismatch: {STATE} != other"),
        OAuthTokenError(f"Token exchange failed (400): {TOKEN_BODY}"),
    ):
        try:
            raise exc
        except OAuthFlowError:
            logger.exception("OAuth flow error %s", STATE)
    names = [r.getMessage() for r in caplog.records if r.name == "mcp.client.auth.oauth2"]
    assert names == ["OAuthFlowError", "OAuthTokenError"]
    assert STATE not in caplog.text
    assert TOKEN_BODY not in caplog.text


def test_registered_secrets_are_redacted_and_parking_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-AUTH-008: plain SDK auth records pass the redaction filter; the Host's own
    parking signal (``AuthorizationDeferredError``) is not logged as an SDK error."""
    protect_sdk_auth_loggers()
    register_secret(STATE)
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(SDK_AUTH_LOGGERS[1])
    logger.warning("refresh failed for %s", STATE)
    try:
        raise AuthorizationDeferredError()
    except AuthorizationDeferredError:
        logger.exception("OAuth flow error")
    messages = [r.getMessage() for r in caplog.records if r.name == SDK_AUTH_LOGGERS[1]]
    assert messages == ["refresh failed for **********"]
