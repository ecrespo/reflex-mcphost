"""The human-in-the-loop seam (API §4.10).

Phase 2 declares only what the trust check needs (``supports_elicitation`` and
``confirm_trust``); consent, sampling review and elicitation methods are added by F3-03.
Exceptions raised by a prompter are treated as deny / untrusted (fail closed, Art. 3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from reflex_mcphost.core.config.models import ConfigOrigin
from reflex_mcphost.core.types import ServerId

__all__ = ["RISKY_ENV_KEYS", "Prompter", "TrustRequest"]

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
    }
)
"""Environment variables known to make an interpreter load extra code (API §4.10)."""


class TrustRequest(BaseModel):
    """Ask the Consumer to trust a project-level Server before it is launched or contacted.

    Carries names only, never values (Art. 5): ``env_keys``/``header_keys`` list variable
    and header names; ``url`` is the endpoint without userinfo, query or fragment.
    For ``transport="http"`` the stdio fields are empty, and vice versa.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    server_id: ServerId
    origin: ConfigOrigin
    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env_keys: tuple[str, ...] = ()
    risky_env_keys: tuple[str, ...] = ()
    url: str | None = None
    header_keys: tuple[str, ...] = ()


class Prompter(Protocol):
    """Implemented by any UI (the Reflex UI ships one; tests use fakes)."""

    supports_elicitation: bool

    async def confirm_trust(self, request: TrustRequest) -> bool:
        """Return ``True`` only if the Consumer trusts the Server described (REQ-REG-011)."""
        ...
