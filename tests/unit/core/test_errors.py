"""Exception hierarchy (API §3.4, Tech Design §5.3)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core import errors as e


@pytest.mark.parametrize(
    "exc",
    [
        e.ConfigError("bad"),
        e.UnknownServerError("github"),
        e.UnknownEntryError("github::x"),
        e.ServerUnavailableError("github", "failed"),
        e.CapabilityNotSupportedError("github", "logging"),
        e.AuthorizationError("github", "state mismatch"),
        e.ServerRequestError("github", -32602, "invalid params", None),
        e.HostClosedError(),
        e.InputRequestError("github", "sampling_denied"),
    ],
)
def test_all_public_errors_derive_from_base(exc: Exception) -> None:
    """API §3.4: every public exception is an MCPHostError."""
    assert isinstance(exc, e.MCPHostError)


def test_errors_carry_structured_fields() -> None:
    """REQ-CAT-004, REQ-CON-002: errors expose server_id / state / capability."""
    unavailable = e.ServerUnavailableError("github", "failed")
    assert (unavailable.server_id, unavailable.state) == ("github", "failed")
    assert "github" in str(unavailable)
    cap = e.CapabilityNotSupportedError("github", "logging")
    assert cap.capability == "logging"
    req = e.ServerRequestError("github", -32602, "invalid params", {"k": 1})
    assert (req.code, req.message, req.data) == (-32602, "invalid params", {"k": 1})


@pytest.mark.parametrize(
    "code", ["sampling_denied", "input_rounds_exceeded", "undeclared_input_request"]
)
def test_input_request_error_codes(code: str) -> None:
    """REQ-HSH-046: InputRequestError carries one of the three codes."""
    err = e.InputRequestError("github", code)  # type: ignore[arg-type]
    assert err.code == code
    assert err.server_id == "github"


def test_input_request_error_rejects_unknown_code() -> None:
    """REQ-HSH-046: only the three specified codes are valid."""
    with pytest.raises(ValueError, match="code"):
        e.InputRequestError("github", "other")  # type: ignore[arg-type]


def test_server_request_error_repr_omits_data() -> None:
    """Art. 5: repr/str never include the Server-supplied data payload."""
    err = e.ServerRequestError("github", -1, "boom", {"token": "s3cr3t"})
    assert "s3cr3t" not in repr(err)
    assert "s3cr3t" not in str(err)
