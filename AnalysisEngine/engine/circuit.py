#!/usr/bin/env python3
"""engine/circuit.py — 3-strike circuit breaker + request pacing.

Reuses ReelScraper's convention: stop after N consecutive failures (default 3) so a bad
Gemini key, an exhausted quota, or a hub outage doesn't burn the whole queue. A success
resets the strike count. Also provides a small pacer so we never hammer the Gemini or hub
endpoints between clips.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("ae.circuit")


class CircuitTripped(RuntimeError):
    """Raised when consecutive failures reach the breaker's limit."""


class CircuitBreaker:
    def __init__(self, max_strikes: int = 3, pace_seconds: float = 2.0):
        self.max_strikes = max_strikes
        self.pace_seconds = pace_seconds
        self.strikes = 0
        self._last_action = 0.0

    @property
    def tripped(self) -> bool:
        return self.strikes >= self.max_strikes

    def record_success(self) -> None:
        if self.strikes:
            log.debug("breaker reset", extra={"was": self.strikes})
        self.strikes = 0

    def record_failure(self, reason: str = "") -> None:
        self.strikes += 1
        log.warning("breaker strike", extra={"strikes": self.strikes, "reason": reason})
        if self.tripped:
            raise CircuitTripped(
                f"circuit breaker tripped after {self.strikes} consecutive failures: {reason}"
            )

    def pace(self) -> None:
        """Sleep just enough to keep a minimum gap between paced actions."""
        elapsed = time.monotonic() - self._last_action
        wait = self.pace_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_action = time.monotonic()
