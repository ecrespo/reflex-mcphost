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

__all__ = ["RISKY_ENV_KEYS", "Prompter", "TrustRequest", "is_risky_env_key"]

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


class Prompter(Protocol):
    """Implemented by any UI (the Reflex UI ships one; tests use fakes)."""

    supports_elicitation: bool

    async def confirm_trust(self, request: TrustRequest) -> bool:
        """Return ``True`` only if the Consumer trusts the Server described (REQ-REG-011)."""
        ...
