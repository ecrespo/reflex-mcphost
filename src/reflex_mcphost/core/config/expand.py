"""``${VAR}`` / ``${VAR:-default}`` expansion of Server definition values (DD-013).

Supported: ``${NAME}`` and ``${NAME:-default}`` (default used when ``NAME`` is unset *or*
empty, as in POSIX shells), and VS Code's ``${workspaceFolder}`` (the project directory).
Every other ``${…}`` form — ``${input:…}``, ``${env:…}``, nested placeholders, invalid
names, an unterminated ``${`` — raises ``UnsupportedVariableError`` (REQ-REG-005). A ``$`` not
followed by ``{`` is literal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
WORKSPACE_FOLDER = "workspaceFolder"


class VariableError(Exception):
    """A placeholder that cannot be expanded; ``name`` is the placeholder's content."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(name)


class UnresolvedVariableError(VariableError):
    """``${NAME}`` with ``NAME`` unset and no default (ConfigProblem ``unresolved_variable``)."""


class UnsupportedVariableError(VariableError):
    """A placeholder form we do not expand (ConfigProblem ``unsupported_variable``)."""


def _resolve(inner: str, env: Mapping[str, str], cwd: Path | None) -> str:
    name, sep, default = inner.partition(":-")
    if _NAME.fullmatch(name) is None or "${" in default:
        raise UnsupportedVariableError(inner)
    if name == WORKSPACE_FOLDER and not sep:
        if cwd is None:
            raise UnsupportedVariableError(inner)
        return str(cwd)
    value = env.get(name)
    if value:
        return value
    if sep:
        return default
    if value is not None:  # set but empty, no default
        return value
    raise UnresolvedVariableError(name)


def expand(value: str, env: Mapping[str, str], *, cwd: Path | None = None) -> str:
    """Return ``value`` with every placeholder substituted; raise ``VariableError``."""
    if "${" not in value:
        return value
    out: list[str] = []
    pos = 0
    for match in _PLACEHOLDER.finditer(value):
        out.append(value[pos : match.start()])
        out.append(_resolve(match.group(1), env, cwd))
        pos = match.end()
    tail = value[pos:]
    if "${" in tail:  # unterminated placeholder
        raise UnsupportedVariableError(tail[tail.index("${") + 2 :])
    out.append(tail)
    return "".join(out)
