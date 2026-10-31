"""Process-wide registry of secret values to redact (Constitution Art. 5).

Every secret the Host handles (env values, header values, tokens) is registered here when it
is loaded or revealed; ``redact`` masks every registered value in a string. Logging applies
it through ``SecretRedactionFilter`` (``core.logging``).
"""

from __future__ import annotations

import threading

from pydantic import SecretStr

MASK = "**********"


class SecretRegistry:
    """Thread-safe set of secret values; redaction replaces longest values first."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()
        self._ordered: tuple[str, ...] = ()

    def register(self, value: str) -> None:
        if not value:
            return
        with self._lock:
            if value not in self._values:
                self._values.add(value)
                self._ordered = tuple(sorted(self._values, key=len, reverse=True))

    def forget(self, value: str) -> None:
        with self._lock:
            if value in self._values:
                self._values.discard(value)
                self._ordered = tuple(sorted(self._values, key=len, reverse=True))

    def redact(self, text: str) -> str:
        for value in self._ordered:  # immutable snapshot: no lock needed to read
            if value in text:
                text = text.replace(value, MASK)
        return text


default_registry = SecretRegistry()


def register_secret(value: str) -> None:
    default_registry.register(value)


def redact(text: str) -> str:
    return default_registry.redact(text)


def reveal(secret: SecretStr, *, registry: SecretRegistry = default_registry) -> str:
    """Return the raw value of ``secret`` after registering it for redaction."""
    raw = secret.get_secret_value()
    registry.register(raw)
    return raw
