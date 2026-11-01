"""Connect-failure classification of the supervisor (API §4.5 transition table)."""

from __future__ import annotations

import mcp_types as types
from mcp import MCPError

from reflex_mcphost.core.connection.supervisor import _classify
from reflex_mcphost.core.connection.transports import CommandNotFoundError


def test_legacy_handshake_with_unsupported_version_is_fatal() -> None:
    """REQ-CON-004: a legacy initialize negotiating an unsupported version → failed, with
    the version the Server offered in the reason."""
    exc = RuntimeError("Unsupported protocol version from the server: 1999-01-01")
    outcome = _classify(exc, None)
    assert outcome.kind == "fatal"
    assert outcome.reason == "unsupported_protocol_version: server offered 1999-01-01"


def test_unsupported_version_without_data_is_fatal() -> None:
    """REQ-CON-004: -32022 with unparseable data still fails without retry."""
    exc = MCPError(types.UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", None)
    outcome = _classify(exc, None)
    assert (outcome.kind, outcome.reason) == ("fatal", "unsupported_protocol_version")


def test_grouped_command_not_found_is_unwrapped() -> None:
    """REQ-CON-007: errors wrapped in exception groups are classified by their leaf."""
    group = ExceptionGroup("tg", [ExceptionGroup("inner", [CommandNotFoundError("npx")])])
    outcome = _classify(group, None)
    assert (outcome.kind, outcome.reason) == ("fatal", "command_not_found: npx")


def test_other_errors_are_transient_and_name_only_the_type() -> None:
    """REQ-CON-005: anything else is a transport error; the reason carries no message text
    (which could contain URLs or Server-supplied data, Art. 5)."""
    outcome = _classify(ConnectionError("http://user:pw@host/ failed"), None)
    assert (outcome.kind, outcome.reason) == ("transient", "transport_error: ConnectionError")
