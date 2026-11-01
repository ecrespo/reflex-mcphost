"""The human-in-the-loop seam (API §4.10).

One protocol implemented by any UI: consent prompts (``ask_consent``, F3-03), sampling
response review and elicitation (signatures used from Phase 4) and project-Server trust
(``confirm_trust``, F2-05b). Exceptions raised by a prompter are treated as deny / ``cancel``
/ untrusted (fail closed, Art. 3). ``DenyAllPrompter`` is the explicit headless choice.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from mcp_types import CreateMessageRequestParams, CreateMessageResult
from pydantic import BaseModel, ConfigDict, Field

from reflex_mcphost.core.config.models import ConfigOrigin
from reflex_mcphost.core.types import ServerId

if TYPE_CHECKING:  # consent models import this module (SamplingReview)
    from reflex_mcphost.core.consent.models import ConsentAnswer, ConsentRequest

__all__ = [
    "RISKY_ENV_KEYS",
    "DenyAllPrompter",
    "ElicitationRequest",
    "ElicitationResponse",
    "Prompter",
    "SamplingResponseReview",
    "SamplingReview",
    "SamplingVerdict",
    "TrustRequest",
    "is_risky_env_key",
]

RISKY_ENV_KEYS = frozenset(
    {
        "NODE_OPTIONS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PERL5OPT",
        "RUBYOPT",
        "JAVA_TOOL_OPTIONS",
        "BASH_ENV",
        "ENV",
        # security review 2026-10-31: executable / module search paths and helper commands
        "PATH",
        "PATHEXT",
        "NODE_PATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "GIT_SSH_COMMAND",
        "LD_AUDIT",
        "DYLD_FRAMEWORK_PATH",
    }
)
"""Environment variables known to make an interpreter load extra code or change which
binary runs (API §4.10). Upper-case; match with ``is_risky_env_key`` (case-insensitive)."""


def is_risky_env_key(name: str) -> bool:
    """Whether ``name`` is in ``RISKY_ENV_KEYS``, ignoring case (Windows env names)."""
    return name.upper() in RISKY_ENV_KEYS


class TrustRequest(BaseModel):
    """Ask the Consumer to trust a project-level Server before it is launched or contacted.

    Carries names only, never values (Art. 5): ``env_keys``/``header_keys`` list variable
    and header names. ``destination`` is authoritative: ``scheme://host[:port]`` of the
    *expanded* URL (no userinfo, path, query or fragment). ``url_template`` is the URL as
    written in the config file (``${VAR}`` unexpanded) with any userinfo removed; ``None``
    for definitions built through the API. ``resolved_command`` is the absolute path of
    the executable that would run (``None`` if it cannot be resolved, in which case the
    launch fails with ``command_not_found``). For ``transport="http"`` the stdio fields are
    empty, and vice versa.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    server_id: ServerId
    origin: ConfigOrigin
    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    resolved_command: str | None = None
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env_keys: tuple[str, ...] = ()
    risky_env_keys: tuple[str, ...] = ()
    destination: str | None = None
    url_template: str | None = None
    header_keys: tuple[str, ...] = ()


class SamplingReview(BaseModel):
    """What a sampling consent prompt shows (API §4.10; used from Phase 4, REQ-HSH-002).

    *Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*
    """

    model_config = ConfigDict(frozen=True)

    server_id: ServerId
    params: CreateMessageRequestParams


class SamplingResponseReview(BaseModel):
    """A provider result shown for review before it reaches the Server (REQ-HSH-004)."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    server_id: ServerId
    result: CreateMessageResult


class SamplingVerdict(BaseModel):
    """Answer to ``review_sampling_response``; ``edited_result`` replaces the result."""

    model_config = ConfigDict(frozen=True)

    approved: bool
    edited_result: CreateMessageResult | None = None


class ElicitationRequest(BaseModel):
    """A Server's elicitation shown to the Consumer (REQ-HSH-010…015; used from Phase 4).

    ``requested_schema`` (form mode) is already validated against the spec's allowed
    subset; ``errors`` maps field → message when the Host re-prompts (REQ-HSH-013).
    ``url`` (url mode) is the full URL: the UI shows it and opens it only if the Consumer
    accepts — core never opens a browser (REQ-HSH-015). ``attribution`` is
    ``"unattributed"`` for a legacy request no single in-flight Host request owns
    (REQ-HSH-047; informs the UI only, elicitation is not consent-gated).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    server_id: ServerId
    server_name: str
    message: str
    mode: Literal["form", "url"] = "form"
    requested_schema: dict[str, object] | None = None
    url: str | None = None
    errors: Mapping[str, str] = Field(default_factory=dict)
    attribution: Literal["exact", "unattributed"] = "exact"


class ElicitationResponse(BaseModel):
    """The Consumer's elicitation answer (REQ-HSH-010/011)."""

    model_config = ConfigDict(frozen=True)

    action: Literal["accept", "decline", "cancel"]
    content: dict[str, str | int | float | bool | list[str]] | None = None


class Prompter(Protocol):
    """Implemented by any UI (the Reflex UI ships one; tests use fakes)."""

    supports_elicitation: bool

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        """Ask the Consumer about an operation no rule decided (REQ-CNS-002, REQ-HSH-002)."""
        ...

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        """Approve, edit or reject a sampling result before it is returned (REQ-HSH-004)."""
        ...

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        """Collect elicitation input from the Consumer (REQ-HSH-010/011)."""
        ...

    async def confirm_trust(self, request: TrustRequest) -> bool:
        """Return ``True`` only if the Consumer trusts the Server described (REQ-REG-011)."""
        ...


class DenyAllPrompter:
    """Headless prompter that refuses everything (API §4.10).

    Equivalent to ``prompter=None`` except that it is explicit and declares no elicitation
    either: consent is denied (``source="prompt"``), sampling results are rejected,
    elicitations are declined and project Servers stay untrusted.
    """

    supports_elicitation = False

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        # deferred: consent models import this module (SamplingReview)
        from reflex_mcphost.core.consent.models import (
            ConsentAnswer,
            ConsentDecision,
        )

        return ConsentAnswer(decision=ConsentDecision.DENY)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        return SamplingVerdict(approved=False)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        return ElicitationResponse(action="decline")

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False
