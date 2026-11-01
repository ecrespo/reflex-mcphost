"""Per-Server log ring buffer and the stdio stderr pipe reader (REQ-CON-007, spike S-2 Q2).

``StderrPipe`` owns one ``os.pipe()`` per connection attempt: the write end is handed to the
child as ``errlog`` (it needs a real ``fileno()``), the read end is drained by an anyio task
— POSIX: ``anyio.wait_readable`` + non-blocking ``os.read`` (no thread); Windows:
``anyio.to_thread`` on a dedicated single-worker limiter (``wait_readable`` does not support
pipes there). Draining must never stop while the child lives, or it blocks on a full pipe.

``LogRing`` is the bounded per-Server log (``HostConfig.log_buffer_size``). Every entry is
redacted before it is stored or handed to ``on_entry`` (Art. 5): stderr is untrusted text.
"""

from __future__ import annotations

import collections
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TextIO, cast

import anyio
import anyio.to_thread
from pydantic import BaseModel, ConfigDict

from reflex_mcphost.core.config.models import LogLevel
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.secrets import redact

__all__ = ["MAX_LINE_BYTES", "LineSink", "LogEntry", "LogRing", "StderrPipe"]

MAX_LINE_BYTES = 64 * 1024
"""Longer lines (or unterminated output) are cut into an entry ending in `` [truncated]``."""
_READ_CHUNK = 64 * 1024
LineSink = Callable[[str], object]
"""Receives one raw (unredacted) stderr line."""
_log = get_logger(__name__)


class LogEntry(BaseModel):
    """One Server log line (API §4.5 ``logs``, §4.11 ``server.log``); ``text`` is redacted."""

    model_config = ConfigDict(frozen=True)

    level: LogLevel
    logger: str | None
    timestamp: datetime
    text: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LogRing:
    """Bounded ring of ``LogEntry``; oldest entries are dropped first."""

    def __init__(
        self,
        maxlen: int,
        *,
        on_entry: Callable[[LogEntry], None] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if maxlen < 1:
            raise ValueError("log buffer size must be >= 1")
        self._entries: collections.deque[LogEntry] = collections.deque(maxlen=maxlen)
        self._on_entry = on_entry
        self._clock = clock

    @property
    def maxlen(self) -> int:
        return self._entries.maxlen or 0

    def __len__(self) -> int:
        return len(self._entries)

    def add(
        self, text: str, *, level: LogLevel = "info", logger: str | None = "stderr"
    ) -> LogEntry:
        """Redact ``text``, store it and hand the entry to ``on_entry`` (errors are logged)."""
        entry = LogEntry(level=level, logger=logger, timestamp=self._clock(), text=redact(text))
        self._entries.append(entry)
        if self._on_entry is not None:
            try:
                self._on_entry(entry)
            except Exception:
                _log.exception("log entry subscriber failed")
        return entry

    def snapshot(self) -> list[LogEntry]:
        return list(self._entries)


class StderrPipe:
    """One stderr pipe per connection attempt (spike S-2)."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self._encoding = encoding
        self._r, w = os.pipe()  # PEP 446: non-inheritable; subprocess dup2()s w onto fd 2
        if sys.platform != "win32":
            os.set_blocking(self._r, False)
        self._wfile = cast(TextIO, os.fdopen(w, "w", encoding=encoding))
        self._closed = False
        self._limiter: anyio.CapacityLimiter | None = None
        self.eof = anyio.Event()

    def child_end(self) -> TextIO:
        """The write end, passed to ``stdio_client`` as ``errlog``."""
        return self._wfile

    def close_parent_write_end(self) -> None:
        """Close our copy after the spawn, so EOF means every writer is gone."""
        if not self._wfile.closed:
            self._wfile.close()

    def close(self) -> None:
        self.close_parent_write_end()
        if not self._closed:
            self._closed = True
            os.close(self._r)

    def _decode(self, raw: bytes) -> str:
        return raw.decode(self._encoding, "replace")

    async def _read(self) -> bytes:
        if sys.platform == "win32":  # pragma: no cover - Windows CI only (REQ-NFR-030)
            if self._limiter is None:  # one worker per pipe, never the default limiter
                self._limiter = anyio.CapacityLimiter(1)
            return await anyio.to_thread.run_sync(
                os.read, self._r, _READ_CHUNK, limiter=self._limiter, abandon_on_cancel=True
            )
        while True:
            await anyio.wait_readable(self._r)
            try:
                return os.read(self._r, _READ_CHUNK)
            except BlockingIOError:  # pragma: no cover - spurious wake-up
                continue

    async def pump(self, sink: LineSink) -> None:
        """Feed complete lines to ``sink`` until EOF; ``eof`` is set on return."""

        def emit(raw: bytes) -> None:
            if len(raw) > MAX_LINE_BYTES:
                raw = raw[:MAX_LINE_BYTES] + b" [truncated]"
            try:
                sink(self._decode(raw))
            except Exception:
                _log.exception("stderr sink failed")

        buf = b""
        try:
            while chunk := await self._read():
                buf += chunk
                *complete, buf = buf.split(b"\n")
                for raw in complete:
                    emit(raw.removesuffix(b"\r"))
                if len(buf) > MAX_LINE_BYTES:
                    emit(buf)
                    buf = b""
            if buf:
                emit(buf)
        finally:
            self.eof.set()
