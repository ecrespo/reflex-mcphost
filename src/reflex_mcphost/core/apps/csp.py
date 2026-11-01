"""The View Content-Security-Policy (API §7 "CSP", REQ-APP-002/011/014, delta D3).

``build_csp(csp)`` takes the ``_meta.ui.csp`` object of the App resource's ``contents``
item and returns the pinned policy with the **valid** declared domains only. An entry is
valid iff it is an ``https://`` origin (or ``wss://`` in ``connectDomains``) whose host is a
public DNS name — optionally with one leading ``*.`` label and a port — and nothing else: no
path (not even ``/``), query, fragment, userinfo, quotes, whitespace, ``;`` or ``,``. IP
literals, ``localhost`` / ``*.localhost``, a bare ``*``, scheme-only sources and keywords are
never valid. At most 32 entries per list and 253 characters per entry are considered.

Stricter than the grammar's letter, to keep "public DNS name" honest: a host needs at least
two labels (``*.`` needs two more: ``*.com`` is refused), its last label starts with a
letter (so no dotted IPv4), and special-use / non-public top-level names (``localhost``,
``local``, ``localdomain``, ``internal``, ``arpa``, ``invalid``, ``test``) are refused.
Schemes must be lowercase.

The policy (API §7) never contains ``'unsafe-eval'``, ``'strict-dynamic'``, nonces, hashes
or ``'self'`` outside ``base-uri``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

__all__ = [
    "CSP_LISTS",
    "DEFAULT_POLICY",
    "MAX_ENTRIES",
    "MAX_ENTRY_CHARS",
    "CspResult",
    "DroppedEntry",
    "build_csp",
    "valid_entry",
]

CSP_LISTS: Final = ("connectDomains", "resourceDomains", "frameDomains", "baseUriDomains")
MAX_ENTRIES = 32
MAX_ENTRY_CHARS = 253
_MAX_DROPPED_PER_LIST = 64
"""Dropped entries recorded per list; the rest is summarized in one ``<N more>`` record."""
_CONNECT = "connectDomains"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_TLD = r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(
    rf"(?P<wild>\*\.)?(?P<host>(?:{_LABEL}\.)+{_TLD})(?::(?P<port>[0-9]{{1,5}}))?"
)
_NON_PUBLIC_TLDS = frozenset(
    {"localhost", "local", "localdomain", "internal", "arpa", "invalid", "test"}
)


@dataclass(frozen=True)
class DroppedEntry:
    """A declared CSP entry left out of the policy (logged and shown in the notice)."""

    list_name: str
    entry: str
    """The entry's text cut to 253 characters (``…`` appended), or ``<type>`` for a
    non-string value."""


@dataclass(frozen=True)
class CspResult:
    """The View policy plus what the notices need (REQ-APP-011/014)."""

    policy: str
    external: bool
    """``True`` iff at least one valid external domain was declared (REQ-APP-011 notice)."""
    dropped: tuple[DroppedEntry, ...] = ()
    domains: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType(dict.fromkeys(CSP_LISTS, ()))
    )
    """The valid entries per list (``hostCapabilities.sandbox.csp``)."""

    def csp_lists(self) -> dict[str, list[str]]:
        """The validated lists in the wire shape of ``McpUiResourceCsp``."""
        return {name: list(self.domains.get(name, ())) for name in CSP_LISTS}


def valid_entry(entry: object, list_name: str) -> bool:
    """Whether ``entry`` is a valid source for ``list_name`` (REQ-APP-014)."""
    if not isinstance(entry, str) or not entry or len(entry) > MAX_ENTRY_CHARS:
        return False
    if entry.startswith("https://"):
        rest = entry[len("https://") :]
    elif list_name == _CONNECT and entry.startswith("wss://"):
        rest = entry[len("wss://") :]
    else:
        return False
    match = _HOST_RE.fullmatch(rest)
    if match is None:
        return False
    labels = match["host"].lower().split(".")
    if match["wild"] and len(labels) < 2:  # pragma: no cover - the regex needs 2 labels
        return False
    if labels[-1] in _NON_PUBLIC_TLDS:
        return False
    port = match["port"]
    return port is None or 1 <= int(port) <= 65535


def _shown(value: object) -> str:
    if not isinstance(value, str):
        return f"<{type(value).__name__}>"
    return value if len(value) <= MAX_ENTRY_CHARS else value[:MAX_ENTRY_CHARS] + "…"


def _validate(name: str, raw: object) -> tuple[tuple[str, ...], list[DroppedEntry]]:
    dropped: list[DroppedEntry] = []
    if raw is None:
        return (), dropped
    if not isinstance(raw, list | tuple):
        return (), [DroppedEntry(name, _shown(raw))]
    valid: dict[str, None] = {}
    for index, entry in enumerate(raw):
        if index < MAX_ENTRIES and valid_entry(entry, name):
            valid[entry] = None
            continue
        if len(dropped) == _MAX_DROPPED_PER_LIST:
            rest = len(raw) - index
            dropped.append(DroppedEntry(name, f"<{rest} more>"))
            break
        dropped.append(DroppedEntry(name, _shown(entry)))
    return tuple(valid), dropped


def _directive(name: str, fixed: tuple[str, ...], sources: tuple[str, ...]) -> str:
    return " ".join((name, *fixed, *sources))


def _policy(r: tuple[str, ...], c: tuple[str, ...], f: tuple[str, ...], b: tuple[str, ...]) -> str:
    return "; ".join(
        (
            "default-src 'none'",
            _directive("script-src", ("'unsafe-inline'",), r),
            _directive("style-src", ("'unsafe-inline'",), r),
            _directive("img-src", ("data:", "blob:"), r),
            _directive("font-src", ("data:",), r),
            _directive("media-src", ("data:", "blob:"), r),
            _directive("connect-src", (), c) if c else "connect-src 'none'",
            _directive("frame-src", (), f) if f else "frame-src 'none'",
            _directive("base-uri", (), b) if b else "base-uri 'self'",
            "object-src 'none'",
            "form-action 'none'",
        )
    )


DEFAULT_POLICY: Final = _policy((), (), (), ())
"""The policy when no (valid) domain is declared (API §7)."""


def build_csp(csp: object) -> CspResult:
    """The View policy from ``_meta.ui.csp`` (``None`` or a non-object → the default)."""
    meta: Mapping[str, object] = csp if isinstance(csp, Mapping) else {}
    domains: dict[str, tuple[str, ...]] = {}
    dropped: list[DroppedEntry] = []
    for name in CSP_LISTS:
        valid, bad = _validate(name, meta.get(name))
        domains[name] = valid
        dropped.extend(bad)
    policy = _policy(
        domains["resourceDomains"],
        domains["connectDomains"],
        domains["frameDomains"],
        domains["baseUriDomains"],
    )
    return CspResult(
        policy=policy,
        external=any(domains.values()),
        dropped=tuple(dropped),
        domains=MappingProxyType(domains),
    )
