"""``map_sdk_error`` and the Invoker's result types (F3-08; Tech Design §5.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import anyio
import httpx2
import mcp_types as types
import pytest
from mcp import MCPError
from pydantic import BaseModel, ValidationError

from reflex_mcphost.core.errors import ServerUnavailableError
from reflex_mcphost.core.invoker import (
    InvocationOutcome,
    InvocationResult,
    map_sdk_error,
)
from reflex_mcphost.core.mrtr import (
    InputHandlerError,
    InputRoundsExceededError,
    SamplingDeniedError,
    UndeclaredInputRequestError,
)
from reflex_mcphost.core.types import Consumer, Fqn

Out = InvocationOutcome


def _validation_error() -> ValidationError:
    class M(BaseModel):
        x: int

    try:
        M.model_validate({"x": "no"})
    except ValidationError as exc:
        return exc
    raise AssertionError("unreachable")


@pytest.mark.parametrize(
    ("exc", "outcome", "code"),
    [
        (
            UndeclaredInputRequestError("sampling/createMessage", "k"),
            Out.protocol_error,
            "undeclared_input_request",
        ),
        (InputRoundsExceededError(8), Out.protocol_error, "input_rounds_exceeded"),
        (SamplingDeniedError(), Out.denied, "sampling_denied"),
        (InputHandlerError("roots/list", "KeyError"), Out.protocol_error, "input_handler_failed"),
        (ServerUnavailableError("s", "reconnecting"), Out.server_unavailable, "server_unavailable"),
        (
            MCPError(types.CONNECTION_CLOSED, "Connection closed"),
            Out.connection_lost,
            "connection_lost",
        ),
        (
            MCPError(types.INVALID_PARAMS, "bad params", {"x": "srv-data"}),
            Out.protocol_error,
            "-32602",
        ),
        (anyio.BrokenResourceError(), Out.connection_lost, "connection_lost"),
        (anyio.EndOfStream(), Out.connection_lost, "connection_lost"),
        (httpx2.ConnectError("refused"), Out.connection_lost, "connection_lost"),
        (
            RuntimeError("Invalid structured content returned by tool t: 1\nmore"),
            Out.protocol_error,
            "invalid_result",
        ),
        (_validation_error(), Out.protocol_error, "invalid_result"),
        (KeyError("surprise"), Out.protocol_error, "internal_error"),
        (ExceptionGroup("g", [SamplingDeniedError()]), Out.denied, "sampling_denied"),
    ],
)
def test_map_sdk_error(exc: BaseException, outcome: Out, code: str) -> None:
    """REQ-INV-015, REQ-HSH-043, REQ-HSH-044, Tech Design §5.3: SDK and loop failures map to
    one outcome and a stable error code; JSON-RPC error data never reaches the result."""
    got_outcome, error = map_sdk_error(exc)
    assert (got_outcome, error.code) == (outcome, code)
    assert "srv-data" not in error.model_dump_json()
    assert "\n" not in error.message


def test_result_text_and_ok() -> None:
    result = InvocationResult(
        id="inv_x",
        fqn=Fqn.parse("s::t"),
        consumer=Consumer.human(),
        outcome=Out.success,
        content=(
            types.TextContent(type="text", text="a"),
            types.ImageContent(type="image", data="AA==", mime_type="image/png"),
            types.TextContent(type="text", text="b"),
        ),
        started_at=datetime.now(UTC),
        duration_ms=1,
    )
    assert result.ok
    assert result.text == "ab"
    assert not result.model_copy(update={"outcome": Out.timeout}).ok
