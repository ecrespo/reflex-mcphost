"""Toolset ``include`` / ``exclude`` filters (REQ-TLS-003; API §4.12, delta D5).

An entry containing ``::`` is a case-sensitive glob on ``str(fqn)`` (``github::*``,
``*::search``); an entry without ``::`` is a case-sensitive glob on the server id, so the
default ``"*"`` selects everything and ``"github"`` selects exactly Server ``github``.
Exclude wins.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatchcase

from reflex_mcphost.core.types import FQN_SEPARATOR, Fqn

__all__ = ["check_patterns", "selected"]


def check_patterns(name: str, patterns: Sequence[str]) -> tuple[str, ...]:
    """Validate one filter argument; a bare ``str`` is rejected (it would iterate chars)."""
    if isinstance(patterns, str):
        raise TypeError(f"{name} must be a sequence of patterns, not a string: [{patterns!r}]")
    values = tuple(patterns)
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{name} entries must be strings, got {type(value).__name__}")
    return values


def _matches(fqn: Fqn, pattern: str) -> bool:
    if FQN_SEPARATOR in pattern:
        return fnmatchcase(str(fqn), pattern)
    return fnmatchcase(fqn.server_id, pattern)


def selected(fqn: Fqn, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """Whether ``fqn`` passes the filters (in ``include`` and not in ``exclude``)."""
    return any(_matches(fqn, p) for p in include) and not any(_matches(fqn, p) for p in exclude)
