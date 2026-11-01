"""Shared pieces of the host-side handlers (API §4.10, §4.14; Tech Design Flow C step 6).

Each handler has one era-neutral core (``handle``) and two thin adapters: the modern
``InputRequestRouter`` slot (``as_input_handler``) and the legacy SDK callback
(``as_callback``), so MRTR input requests and legacy server-initiated requests run the same
code and consent rules (REQ-HSH-045). A refusal is a value of the core; the adapters turn
it into the era's channel (spike S-1): modern → a client-side abort (``InputHandlerError``
or ``SamplingDeniedError``, never ``ErrorData`` in ``inputResponses``), legacy → the
``ErrorData`` the SDK sends back as the JSON-RPC error.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import mcp_types as types

from reflex_mcphost.core.mrtr import InputContext

__all__ = ["Attribute", "Refusal"]

Attribute = Callable[[str, str], InputContext]
"""Legacy attribution: ``(method, key)`` → the context of a server-initiated request (the
supervisor's in-flight registry, REQ-HSH-045/047). ``key`` is the JSON-RPC request id."""


@dataclass(frozen=True)
class Refusal:
    """A request the Host will not fulfil. ``reason`` is the stable code reported as
    ``InputHandlerError.reason`` (modern) and audited where relevant; ``legacy`` is the
    JSON-RPC error a legacy Server receives; ``denied`` marks a sampling denial (modern
    ``SamplingDeniedError`` → outcome ``denied/sampling_denied``)."""

    reason: str
    legacy: types.ErrorData
    denied: bool = False
