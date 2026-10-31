"""Pinned mcp 2.3 OAuth surface used by ``core.auth`` (F4-07, Flow E, delta D2, Art. 2).

A failure here means the SDK's auth API moved: re-check ``core/auth/oauth.py`` and
``core/auth/token_store.py`` before upgrading.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import pkgutil

import pytest
from mcp.client import auth as sdk_auth
from mcp.client.auth import (
    OAuthClientProvider,
    OAuthFlowError,
    OAuthRegistrationError,
    OAuthTokenError,
    TokenStorage,
)
from mcp.client.auth.oauth2 import OAuthContext
from mcp.client.auth.utils import is_valid_client_metadata_url, validate_authorization_response_iss
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata, OAuthMetadata

from reflex_mcphost.core.auth.oauth import SDK_AUTH_LOGGERS
from reflex_mcphost.core.auth.token_store import TokenStore
from reflex_mcphost.core.logging import SDK_LOGGERS


def test_provider_constructor_kwargs() -> None:
    """Flow E step 1: the kwargs the controller passes to ``OAuthClientProvider``."""
    params = inspect.signature(OAuthClientProvider.__init__).parameters
    for name in (
        "server_url",
        "client_metadata",
        "storage",
        "redirect_handler",
        "callback_handler",
        "client_metadata_url",
    ):
        assert name in params
    assert callable(OAuthClientProvider.async_auth_flow)


def test_token_storage_protocol_matches_token_store() -> None:
    """DM §3: ``TokenStore`` implements every ``TokenStorage`` method, all async."""
    for name in ("get_tokens", "set_tokens", "get_client_info", "set_client_info"):
        assert inspect.iscoroutinefunction(getattr(TokenStorage, name))
        assert inspect.iscoroutinefunction(getattr(TokenStore, name))


def test_authorization_code_result_and_metadata_defaults() -> None:
    """Flow E step 4: ``AuthorizationCodeResult(code, state, iss)``; client metadata
    defaults to ``application_type="native"`` (DCR fallback, REQ-AUTH-003)."""
    assert set(AuthorizationCodeResult.model_fields) == {"code", "state", "iss"}
    meta = OAuthClientMetadata(redirect_uris=["http://127.0.0.1/callback"])  # type: ignore[list-item]
    assert meta.application_type == "native"
    assert is_valid_client_metadata_url(
        "https://ecrespo.github.io/reflex-mcphost/oauth/client.json"
    )
    assert not is_valid_client_metadata_url("http://127.0.0.1/client.json")


def test_iss_validation_rules() -> None:
    """REQ-AUTH-010 (RFC 9207): mismatch and missing-while-advertised raise OAuthFlowError."""
    meta = OAuthMetadata.model_validate(
        {
            "issuer": "http://127.0.0.1:9000",
            "authorization_endpoint": "http://127.0.0.1:9000/authorize",
            "token_endpoint": "http://127.0.0.1:9000/token",
            "authorization_response_iss_parameter_supported": True,
        }
    )
    validate_authorization_response_iss("http://127.0.0.1:9000", meta)
    with pytest.raises(OAuthFlowError):
        validate_authorization_response_iss("https://evil.example", meta)
    with pytest.raises(OAuthFlowError):
        validate_authorization_response_iss(None, meta)
    validate_authorization_response_iss(None, None)


def test_oauth_context_attributes_read() -> None:
    """The ``OAuthContext`` members the controller reads or sets (refresh-once, issuer
    guard, redirect URI, pre-redemption iss check)."""
    fields = {f.name for f in dataclasses.fields(OAuthContext)}
    assert {"client_metadata", "oauth_metadata", "current_tokens", "token_expiry_time"} <= fields
    for method in ("is_token_valid", "can_refresh_token", "clear_tokens"):
        assert callable(getattr(OAuthContext, method))


def test_exception_hierarchy() -> None:
    """Reason mapping (API §3.4): registration/token errors are OAuthFlowError subclasses."""
    assert issubclass(OAuthRegistrationError, OAuthFlowError)
    assert issubclass(OAuthTokenError, OAuthFlowError)


def test_every_sdk_auth_logger_is_protected() -> None:
    """Delta D2: every ``mcp.client.auth*`` module that has a module logger is in
    ``SDK_AUTH_LOGGERS`` (so ``protect_sdk_auth_loggers`` covers it)."""
    found = {sdk_auth.__name__}
    for module_info in pkgutil.walk_packages(sdk_auth.__path__, sdk_auth.__name__ + "."):
        module = importlib.import_module(module_info.name)
        if isinstance(getattr(module, "logger", None), logging.Logger):
            found.add(module.logger.name)
    assert found <= set(SDK_AUTH_LOGGERS)


def test_every_sdk_client_logger_is_protected() -> None:
    """REQ-NFR-010: every ``mcp.client``/``mcp.shared``/``mcp.os`` module logger outside
    ``mcp.client.auth`` is in ``SDK_LOGGERS`` (so ``protect_sdk_loggers`` covers it)."""
    import mcp.client
    import mcp.os
    import mcp.shared

    found: set[str] = set()
    for package in (mcp.client, mcp.shared, mcp.os):
        for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if module_info.name.endswith("__main__") or module_info.name.startswith(
                sdk_auth.__name__
            ):
                continue
            try:
                module = importlib.import_module(module_info.name)
            except ImportError:  # platform-specific module (e.g. win32 on POSIX)
                continue
            found |= {v.name for v in vars(module).values() if isinstance(v, logging.Logger)}
    assert found
    assert found <= set(SDK_LOGGERS)
