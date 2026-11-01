"""Per-Server client capability plan (DD-003, REQ-CON-003, REQ-HSH-006/014, delta D8).

In the SDK, registering a callback *is* declaring the capability, so the plan decides
which callbacks a Server's ``Client`` gets:

* ``elicitation`` iff the prompter ``supports_elicitation`` (REQ-HSH-014);
* ``sampling`` iff a ``SamplingProvider`` is set (REQ-HSH-006; no sampling
  sub-capabilities such as ``tools`` are declared, Art. 2);
* ``roots`` iff the Server's effective roots are non-empty;
* ``log_level``: the runtime level set with ``set_log_level``, else the definition's
  ``log_level``, else none (REQ-HSH-031);
* ``mcp_apps``: the MCP Apps extension ``io.modelcontextprotocol/ui`` (``mimeTypes:
  ["text/html;profile=mcp-app"]``) through ``mcp.client.advertise`` iff
  ``Host.set_mcp_apps(True)`` (REQ-APP-007, DD-012). It declares no input-request method.

``logging_callback`` is always registered (it declares nothing). The ``InputRequestRouter``
gates modern input requests on the same plan (``methods``), so a method the Host did not
declare to *that* Server is ``undeclared_input_request`` (REQ-HSH-044). Modern Clients are
rebuilt when their plan changes; legacy Clients keep theirs until ``reconnect()`` (DD-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from mcp.client import advertise
from mcp.client.session import ElicitationFnT, ListRootsFnT, SamplingFnT

from reflex_mcphost.core.apps.constants import EXTENSION_ID, MIME_TYPE
from reflex_mcphost.core.config.models import LogLevel
from reflex_mcphost.core.handlers.base import Attribute
from reflex_mcphost.core.mrtr import ELICITATION, ROOTS, SAMPLING

__all__ = ["CapabilityPlan", "HandlerSet"]


@dataclass(frozen=True)
class CapabilityPlan:
    """What the Host declares to one Server."""

    elicitation: bool = False
    sampling: bool = False
    roots: bool = False
    log_level: LogLevel | None = None
    mcp_apps: bool = False

    @property
    def methods(self) -> frozenset[str]:
        """The input-request methods this plan declares."""
        declared = {ELICITATION: self.elicitation, SAMPLING: self.sampling, ROOTS: self.roots}
        return frozenset(method for method, on in declared.items() if on)


class _ElicitationSource(Protocol):
    def as_callback(self, attribute: Attribute) -> ElicitationFnT: ...


class _SamplingSource(Protocol):
    def as_callback(self, attribute: Attribute) -> SamplingFnT: ...


class _RootsSource(Protocol):
    def as_callback(self, attribute: Attribute) -> ListRootsFnT: ...


@dataclass(frozen=True)
class HandlerSet:
    """The Host's handler cores; a supervisor builds its legacy callbacks from them."""

    elicitation: _ElicitationSource | None = None
    sampling: _SamplingSource | None = None
    roots: _RootsSource | None = None

    def client_kwargs(self, plan: CapabilityPlan, attribute: Attribute) -> dict[str, Any]:
        """``Client(...)`` keyword arguments for ``plan``: a callback only for each declared
        capability that has a handler, plus ``log_level`` and the MCP Apps ``extensions``."""
        kwargs: dict[str, Any] = {}
        if plan.elicitation and self.elicitation is not None:
            kwargs["elicitation_callback"] = self.elicitation.as_callback(attribute)
        if plan.sampling and self.sampling is not None:
            kwargs["sampling_callback"] = self.sampling.as_callback(attribute)
        if plan.roots and self.roots is not None:
            kwargs["list_roots_callback"] = self.roots.as_callback(attribute)
        if plan.log_level is not None:
            kwargs["log_level"] = plan.log_level
        if plan.mcp_apps:
            kwargs["extensions"] = [advertise(EXTENSION_ID, {"mimeTypes": [MIME_TYPE]})]
        return kwargs
