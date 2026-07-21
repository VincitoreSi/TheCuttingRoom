#!/usr/bin/env python3
"""engine/circuit.py — 3-strike circuit breaker + request pacing (verbatim AnalysisEngine/
ReelScraper convention, PIPELINE.md §1.4 / AutoSearch/PIPELINE.md §1 rule 4).

Stops after N consecutive failures (default 3) so a bad key, a rate-limited surface, or a
hub outage doesn't burn the whole run/beat. A success resets the strike count. Also
provides a pacer so paced actions (Anthropic calls, hub writes) never fire faster than
`pace_seconds` apart — a courtesy floor, separate from the stricter §1.3 IG pacing floors
enforced in engine/ig.py.
"""
from __future__ import annotations

import logging
import time

from engine.limits import BREAKER_DEFAULT_PACE_SECONDS, BREAKER_MAX_STRIKES

log = logging.getLogger("as.circuit")


class CircuitTripped(RuntimeError):
    """Raised when consecutive failures reach the breaker's limit."""


class CircuitBreaker:
    def __init__(self, max_strikes: int = BREAKER_MAX_STRIKES,
                pace_seconds: float = BREAKER_DEFAULT_PACE_SECONDS):
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
