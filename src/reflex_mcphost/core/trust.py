"""Trust store for project-level Server definitions (REQ-REG-011, DM §2.4, Q-T1).

A definition from a project source (``.mcphost.toml``, ``.mcp.json``, ``.cursor/mcp.json``,
``.vscode/mcp.json``) is launched or contacted only after the Consumer confirmed it via
``Prompter.confirm_trust``. Confirmations are remembered per ``(server_id, fingerprint)``;
the fingerprint is an install-keyed HMAC (DD-007) over everything that decides what runs or
where secrets go, so any change asks again:

- stdio: ``{transport, command, resolved_command, args, cwd, origin_path, env: {name:
  value}}`` — ``resolved_command`` is the executable the launch would run (security review
  2026-10-31), so a ``PATH`` change that picks a different binary asks again;
- http (delta 2026-10-project-trust): ``{transport, url, origin_path, headers: {name: value}}``
  with the *expanded* URL and header values — a project file could otherwise send host
  environment secrets to an endpoint of its choosing on auto-connect. The prompt shows the
  authoritative ``destination`` (``scheme://host[:port]`` of the expanded URL) next to the
  URL as written in the file without userinfo (``url_template``; amendment and security
  review 2026-10-31).

Values are covered by the keyed hash but never stored or shown (Art. 5). Rejections are not
stored. Every failure path — decline, prompter exception, no prompter, store error — is
"untrusted" (fail closed, Art. 3).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from sqlmodel import select

from reflex_mcphost.core.config.models import (
    PROJECT_SOURCES,
    HttpTransport,
    ServerDefinition,
    StdioTransport,
)
from reflex_mcphost.core.connection.transports import resolve_stdio_command
from reflex_mcphost.core.db import Database
from reflex_mcphost.core.db_models import TrustedServerRow
from reflex_mcphost.core.hmac_key import InstallKey
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.prompter import Prompter, TrustRequest, is_risky_env_key
from reflex_mcphost.core.secrets import reveal
from reflex_mcphost.core.types import new_request_id

__all__ = [
    "TrustStore",
    "build_trust_request",
    "destination",
    "requires_trust",
    "resolve_command",
    "strip_userinfo",
]

_log = get_logger(__name__)


def requires_trust(definition: ServerDefinition) -> bool:
    """Whether ``definition`` comes from a project source and so needs confirmation."""
    return definition.origin.kind in PROJECT_SOURCES


def _origin_path(definition: ServerDefinition) -> str:
    path = definition.origin.path
    return str(path) if path is not None else ""


def destination(url: str) -> str:
    """``scheme://host[:port]`` of ``url``: where requests really go, nothing else (no
    userinfo, path, query or fragment — a path may embed a secret)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return f"{parts.scheme}://{netloc}"


def strip_userinfo(template: str) -> str:
    """``template`` without ``user:pass@`` in its authority (everything up to the last
    ``@``), so ``https://good.com@evil.com/`` cannot pose as ``good.com``."""
    scheme, sep, rest = template.partition("://")
    if not sep:
        return template
    end = min((i for i in (rest.find(c) for c in "/?#") if i >= 0), default=len(rest))
    authority = rest[:end]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{rest[end:]}"


def _fingerprint_material(
    definition: ServerDefinition, resolved_command: str | None
) -> dict[str, Any]:
    transport = definition.transport
    if isinstance(transport, StdioTransport):
        return {
            "transport": "stdio",
            "command": transport.command,
            "resolved_command": resolved_command,
            "args": list(transport.args),
            "cwd": str(transport.cwd) if transport.cwd is not None else None,
            "origin_path": _origin_path(definition),
            "env": {k: reveal(v) for k, v in transport.env.items()},
        }
    return {
        "transport": "http",
        "url": str(transport.url),
        "origin_path": _origin_path(definition),
        "headers": {k: reveal(v) for k, v in transport.headers.items()},
    }


async def resolve_command(definition: ServerDefinition) -> str | None:
    """The executable a stdio definition would run (worker thread); ``None`` for HTTP."""
    transport = definition.transport
    if isinstance(transport, StdioTransport):
        return await resolve_stdio_command(transport)
    return None


def build_trust_request(
    definition: ServerDefinition, *, resolved_command: str | None = None
) -> TrustRequest:
    """The prompt shown to the Consumer: names only, never values (API §4.10)."""
    transport = definition.transport
    common: dict[str, Any] = {
        "id": new_request_id(),
        "server_id": definition.id,
        "origin": definition.origin,
    }
    if isinstance(transport, HttpTransport):
        return TrustRequest(
            **common,
            transport="http",
            destination=destination(str(transport.url)),
            url_template=(
                strip_userinfo(transport.url_template)
                if transport.url_template is not None
                else None
            ),
            header_keys=tuple(sorted(transport.headers)),
        )
    env_keys = tuple(sorted(transport.env))
    return TrustRequest(
        **common,
        transport="stdio",
        command=transport.command,
        resolved_command=resolved_command,
        args=transport.args,
        cwd=transport.cwd,
        env_keys=env_keys,
        risky_env_keys=tuple(k for k in env_keys if is_risky_env_key(k)),
    )


class TrustStore:
    """Q-T1 lookups and inserts through ``Database.run`` (single DB worker, Art. 8)."""

    def __init__(self, db: Database, key: InstallKey) -> None:
        self.db = db
        self._key = key

    def fingerprint(
        self, definition: ServerDefinition, *, resolved_command: str | None = None
    ) -> str:
        return self._key.keyed_hash(_fingerprint_material(definition, resolved_command))

    async def is_trusted(
        self, definition: ServerDefinition, *, resolved_command: str | None = None
    ) -> bool:
        fingerprint = self.fingerprint(definition, resolved_command=resolved_command)

        def lookup() -> bool:
            with self.db.session() as s:
                stmt = select(TrustedServerRow).where(
                    TrustedServerRow.server_id == definition.id,
                    TrustedServerRow.fingerprint == fingerprint,
                )
                return s.exec(stmt).first() is not None

        return await self.db.run(lookup)

    async def remember(
        self, definition: ServerDefinition, *, resolved_command: str | None = None
    ) -> None:
        row = TrustedServerRow(
            server_id=definition.id,
            fingerprint=self.fingerprint(definition, resolved_command=resolved_command),
            origin_path=_origin_path(definition),
            created_at_us=time.time_ns() // 1000,
        )

        def insert() -> None:
            with self.db.session() as s:
                if s.get(TrustedServerRow, (row.server_id, row.fingerprint)) is None:
                    s.add(row)
                    s.commit()

        await self.db.run(insert)

    async def require_trust(self, definition: ServerDefinition, prompter: Prompter | None) -> bool:
        """``True`` when ``definition`` needs no trust, is already trusted or is confirmed now;
        ``False`` on decline, prompter exception, no prompter or store error (fail closed)."""
        if not requires_trust(definition):
            return True
        log = ContextAdapter(_log, server_id=definition.id)
        try:
            resolved = await resolve_command(definition)
            if await self.is_trusted(definition, resolved_command=resolved):
                return True
        except Exception as exc:
            log.error("trust store unavailable (%s); server untrusted", type(exc).__name__)
            return False
        if prompter is None:
            log.warning("project server needs trust and no prompter is set; server untrusted")
            return False
        try:
            request = build_trust_request(definition, resolved_command=resolved)
            answer = await prompter.confirm_trust(request)
        except Exception as exc:
            log.warning(
                "prompter raised %s during confirm_trust; server untrusted", type(exc).__name__
            )
            return False
        if answer is not True:
            log.info("trust declined; server untrusted")
            return False
        try:
            await self.remember(definition, resolved_command=resolved)
        except Exception as exc:  # confirmed for this launch; asked again next time
            log.error("could not store trust decision (%s)", type(exc).__name__)
        return True
