"""Library logging (REQ-NFR-040, Tech Design §7.1).

Loggers live under ``reflex_mcphost.*``. The library installs no handlers (only a
``NullHandler`` on the package logger); applications configure output. Every logger obtained
through ``get_logger`` carries a ``SecretRedactionFilter`` that masks registered secrets in
the message, its arguments, extras, exception and stack text (REQ-REG-007, REQ-NFR-010).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from reflex_mcphost.core.secrets import SecretRegistry, default_registry

ROOT_LOGGER = "reflex_mcphost"
CONTEXT_FIELDS = ("server_id", "fqn", "invocation_id", "request_id", "era")
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}
_formatter = logging.Formatter()


class SecretRedactionFilter(logging.Filter):
    """Mask registered secret values everywhere in a record before emission."""

    def __init__(self, registry: SecretRegistry = default_registry) -> None:
        super().__init__()
        self._registry = registry

    def filter(self, record: logging.LogRecord) -> bool:
        redact = self._registry.redact
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = record.exc_text or _formatter.formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        for key, value in list(vars(record).items()):
            if key in _STANDARD_ATTRS:
                continue
            if isinstance(value, str):
                setattr(record, key, redact(value))
            elif value is not None and not isinstance(value, int | float | bool):
                text = repr(value)
                masked = redact(text)
                if masked != text:
                    setattr(record, key, masked)
        return True


_FILTER = SecretRedactionFilter()


def _ensure_filter(logger: logging.Logger) -> logging.Logger:
    if _FILTER not in logger.filters:
        logger.addFilter(_FILTER)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a redacting logger under the ``reflex_mcphost`` namespace."""
    if name != ROOT_LOGGER and not name.startswith(ROOT_LOGGER + "."):
        name = f"{ROOT_LOGGER}.{name}"
    return _ensure_filter(logging.getLogger(name))


class ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    """Attach structured context (server_id, fqn, invocation_id, request_id, era)."""

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        unknown = set(context) - set(CONTEXT_FIELDS)
        if unknown:
            raise ValueError(f"unknown log context fields: {sorted(unknown)}")
        super().__init__(_ensure_filter(logger), context)

    def bind(self, **context: Any) -> ContextAdapter:
        return ContextAdapter(self.logger, **{**dict(self.extra or {}), **context})

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra: Mapping[str, Any] = kwargs.get("extra") or {}
        kwargs["extra"] = {**dict(self.extra or {}), **extra}
        return msg, kwargs


_ensure_filter(logging.getLogger(ROOT_LOGGER))
if not any(isinstance(h, logging.NullHandler) for h in logging.getLogger(ROOT_LOGGER).handlers):
    logging.getLogger(ROOT_LOGGER).addHandler(logging.NullHandler())
