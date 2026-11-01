"""Public exception hierarchy (API §3.4, Tech Design §5.3).

Expected invocation outcomes are values (``InvocationResult.outcome``), never exceptions.
These exceptions signal misuse or unavailable dependencies. ``str``/``repr`` never carry
secrets or Server-supplied payloads (Constitution Art. 5).
"""

from __future__ import annotations

from typing import Any, Literal, get_args

InputRequestCode = Literal["sampling_denied", "input_rounds_exceeded", "undeclared_input_request"]
AuthorizationReason = Literal[
    "not_auth_required",
    "state_mismatch",
    "iss_mismatch",
    "access_denied",
    "timeout",
    "cancelled",
    "client_id_required",
    "registration_failed",
    "token_error",
    "discovery_failed",
    "flow_error",
    "secret_store_unavailable",
]
"""``AuthorizationError.reason`` codes (API §3.4); never SDK message text (Art. 5)."""


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


class UnknownToolError(MCPHostError, KeyError):
    """``Toolset.resolve`` / ``Toolset.call`` of an alias not in the current listing
    (REQ-TLS-004, API §3.4). Also a ``KeyError``, so ``except KeyError`` code keeps working;
    adapters convert only this error into "Tool is no longer available" (API §4.13)."""

    def __init__(self, name: str) -> None:
        self.name = str(name)
        super().__init__(self.name)

    def __str__(self) -> str:
        return f"unknown tool {self.name!r}"


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
    """OAuth flow aborted or failed (REQ-AUTH-003/006/008/010).

    ``reason`` is one of the API §3.4 codes; the message never carries SDK text, codes,
    ``state`` or tokens. ``client_id_required`` names the config key to set (REQ-AUTH-003).
    """

    def __init__(self, server_id: str, reason: AuthorizationReason) -> None:
        if reason not in get_args(AuthorizationReason):
            raise ValueError(f"invalid AuthorizationError reason {reason!r}")
        self.server_id = server_id
        self.reason: AuthorizationReason = reason
        message = f"authorization for {server_id!r} failed: {reason}"
        if reason == "client_id_required":
            message += f" (set [servers.{server_id}.oauth] client_id in mcphost.toml)"
        super().__init__(message)


SecretStoreOperation = Literal["delete"]
"""``SecretStoreError.operation`` values (API §3.4)."""


class SecretStoreError(MCPHostError):
    """The secret store (keyring or the ``0600`` file) failed while an operation the
    Consumer asked for needed it (REQ-AUTH-007, REQ-NFR-020).

    Raised by ``ConnectionManager.sign_out`` when the stored credentials could not be
    deleted; the in-memory credentials are already forgotten (fail closed). The message is
    fixed and the backend exception is not chained: backend text never reaches a caller,
    a traceback or the UI (Art. 5).
    """

    def __init__(self, server_id: str, operation: SecretStoreOperation) -> None:
        if operation not in get_args(SecretStoreOperation):
            raise ValueError(f"invalid SecretStoreError operation {operation!r}")
        self.server_id = server_id
        self.operation: SecretStoreOperation = operation
        super().__init__(
            f"secret store unavailable: could not {operation} the credentials of {server_id!r}"
        )


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
