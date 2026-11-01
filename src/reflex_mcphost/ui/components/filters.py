"""Shared settings for the per-keystroke filter inputs."""

from __future__ import annotations

__all__ = ["FILTER_DEBOUNCE_MS"]

# Reflex debounces a controlled ``rx.input`` for 300 ms by default, which alone exceeds the
# REQ-NFR-004 budget (p95 < 200 ms keystroke → list update). 50 ms still coalesces bursts.
FILTER_DEBOUNCE_MS = 50
