"""Agent-facing tool aliases (REQ-TLS-002; API §4.12 alias algorithm, delta D2).

Aliases are a pure function of the *set* of FQNs in one Toolset listing, so the same
listing always yields the same map whatever its order, every alias matches
``^[A-Za-z0-9_-]{1,64}$`` and no two tools share one — even when a Server names a tool
after another tool's hashed alias (D2 step 3) or two 8-hex hashes collide (D2 step 5).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from reflex_mcphost.core.types import Fqn

__all__ = ["MAX_ALIAS_LENGTH", "assign_aliases", "sanitize"]

MAX_ALIAS_LENGTH = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_HASH_PREFIX = 55  # 55 + "_" + 8 hex = 64
_ORDINAL_PREFIX = 61  # 61 + "_" + 2 digits = 64


def sanitize(value: str) -> str:
    """Replace every character outside ``[A-Za-z0-9_-]`` with ``_``."""
    return _UNSAFE.sub("_", value)


def _digest(fqn: Fqn) -> str:
    return hashlib.sha256(str(fqn).encode("utf-8")).hexdigest()[:8]


def assign_aliases(fqns: Iterable[Fqn]) -> dict[Fqn, str]:
    """Map every FQN of one listing to its alias (API §4.12 steps 1-5)."""
    tools = sorted(set(fqns), key=str)
    candidate: dict[Fqn, str] = {}
    hashed: dict[Fqn, str] = {}
    unsafe: set[Fqn] = set()
    for fqn in tools:
        server, name = sanitize(fqn.server_id), sanitize(fqn.name)
        candidate[fqn] = c = f"{server}__{name}"
        hashed[fqn] = f"{c[:_HASH_PREFIX]}_{_digest(fqn)}"
        if server != fqn.server_id or name != fqn.name or len(c) > MAX_ALIAS_LENGTH:
            unsafe.add(fqn)
    # Step 2: sanitized, too long, or sharing a candidate with another tool.
    counts = Counter(candidate.values())
    in_h = unsafe | {f for f in tools if counts[candidate[f]] > 1}
    # Step 3: a plain candidate spelled like a hashed alias is hashed too, to a fixpoint.
    while True:
        taken = {hashed[f] for f in in_h}
        joining = {f for f in tools if f not in in_h and candidate[f] in taken}
        if not joining:
            break
        in_h |= joining
    # Step 4.
    alias = {f: hashed[f] if f in in_h else candidate[f] for f in tools}
    # Step 5: equal hashes (8 hex digits) — the first by str(fqn) keeps the alias.
    groups: defaultdict[str, list[Fqn]] = defaultdict(list)
    for fqn in tools:  # already sorted by str(fqn)
        groups[alias[fqn]].append(fqn)
    in_use = set(alias.values())
    for shared, members in groups.items():
        k = 0
        for fqn in members[1:]:
            while True:
                k += 1
                value = f"{shared[:_ORDINAL_PREFIX]}_{k:02d}"
                if value not in in_use:
                    break
            alias[fqn] = value
            in_use.add(value)
    return alias
