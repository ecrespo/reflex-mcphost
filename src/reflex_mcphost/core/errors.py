"""Public exception hierarchy (API §3.4, Tech Design §5.3).

Expected invocation outcomes are values (``InvocationResult.outcome``), never exceptions.
These exceptions signal misuse or unavailable dependencies. ``str``/``repr`` never carry
secrets or Server-supplied payloads (Constitution Art. 5).
"""

from __future__ import annotations

from typing import Any, Literal, get_args

InputRequestCode = Literal["sampling_denied", "input_rounds_exceeded", "undeclared_input_request"]


class MCPHostError(Exception):
    """Base class of every public reflex-mcphost exception."""


class ConfigError(MCPHostError):
    """Invalid explicit config file or invalid Server definition (REQ-REG-006, REQ-REG-012)."""


class UnknownServerError(MCPHostError):
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        super().__init__(f"unknown server {server_id!r}")


class UnknownEntryError(MCPHostError):
    def __init__(self, fqn: str) -> None:
        self.fqn = str(fqn)
        super().__init__(f"unknown catalog entry {self.fqn!r}")


class ServerUnavailableError(MCPHostError):
    """Catalog read on a Server that is not ``ready`` (REQ-CAT-004)."""

    def __init__(self, server_id: str, state: str) -> None:
        self.server_id = server_id
        self.state = str(state)
        super().__init__(f"server {server_id!r} is not ready (state: {self.state})")


class CapabilityNotSupportedError(MCPHostError):
    """Request the Server did not declare (REQ-CON-002)."""

    def __init__(self, server_id: str, capability: str) -> None:
        self.server_id = server_id
        self.capability = capability
        super().__init__(f"server {server_id!r} does not support {capability!r}")


class AuthorizationError(MCPHostError):
    """OAuth flow aborted or failed (REQ-AUTH-006)."""

    def __init__(self, server_id: str, reason: str) -> None:
        self.server_id = server_id
        self.reason = reason
        super().__init__(f"authorization for {server_id!r} failed: {reason}")


class ServerRequestError(MCPHostError):
    """JSON-RPC error returned to a catalog read or prompt get.

    ``data`` is Server-controlled and untrusted; it is kept as an attribute only and never
    rendered into ``str``/``repr``.
    """

    def __init__(self, server_id: str, code: int, message: str, data: Any = None) -> None:
        self.server_id = server_id
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"server {server_id!r} returned error {code}: {message}")

    def __repr__(self) -> str:
        return f"ServerRequestError(server_id={self.server_id!r}, code={self.code})"


class HostClosedError(MCPHostError):
    """Any call after ``Host.aclose()`` (REQ-CON-009)."""

    def __init__(self) -> None:
        super().__init__("host is closed")


class InputRequestError(MCPHostError):
    """Input request inside a catalog read failed (REQ-HSH-046)."""

    def __init__(self, server_id: str, code: InputRequestCode) -> None:
        if code not in get_args(InputRequestCode):
            raise ValueError(f"invalid InputRequestError code {code!r}")
        self.server_id = server_id
        self.code: InputRequestCode = code
        super().__init__(f"input request from {server_id!r} failed: {code}")
