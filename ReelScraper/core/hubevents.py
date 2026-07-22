"""hubevents.py — the central-log client for stages the hub spawns blind.

WHY THIS EXISTS. The hub runs a stage as a bare subprocess and drains it with one blocking
`p.communicate()`, so nothing the child prints reaches the UI before it exits — and what
survives is the last 1200 chars on `JOBS[...].tail`, which the Dashboard renders only when the
job ERRORED. The scrapers already log per-creator progress in detail, but through
`core/logsetup`, whose console handler writes to stderr and whose JSONL handler writes a file
the hub never reads. So a six-minute scrape showed an elapsed clock and nothing else, and
"working" was indistinguishable from "hung".

`POST /api/logs` was already able to carry all of it — `LogIn` requires only `agent`, allows
extras, and `_stage_env()` already injects BACKEND_API into every spawned stage. Nothing needed
inventing; the scrapers just never spoke. This is the smallest thing that makes them speak.

DELIBERATELY NOT A GENERAL HUB CLIENT. AnalysisEngine has one of those (engine/hub.py, typed,
60s timeout, raises). This one only ever posts telemetry, so every design choice runs the other
way: it never raises, never blocks for long, and disables itself rather than slowing a run down
to talk about a run.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request

log = logging.getLogger("core.hubevents")

# Seconds. NOT AnalysisEngine's 60: these calls sit inside the scrape loop, between a file
# write and the next network fetch, so an unreachable hub must cost milliseconds per creator
# rather than a minute.
DEFAULT_TIMEOUT = 3.0

# After this many consecutive failures the emitter switches itself off for the life of the
# process. Without it, a hub that went down mid-run would cost DEFAULT_TIMEOUT on every event
# for the rest of a run that may have hundreds — the telemetry would become the bottleneck it
# exists to report on. Three strikes caps the total cost of an absent hub at ~9 seconds.
MAX_FAILURES = 3

# The floor between two heartbeats. Long enough that a 50-creator run adds hundreds of records
# rather than thousands (logs/agents.jsonl has no rotation and is re-read on every GET
# /api/logs), short enough to stay well inside the Dashboard's 45s staleness threshold — so a
# card that has gone quiet really has gone quiet.
HEARTBEAT_SEC = 30.0


class HubEvents:
    """Best-effort lifecycle events. Every method is a no-op when it cannot do better."""

    def __init__(self, agent: str, run_id: str | None = None, platform: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.agent = agent
        self.run_id = run_id
        self.platform = platform
        self.timeout = timeout
        # NO 127.0.0.1:8787 DEFAULT, and that is the point. `cli.py scrape` is a bare
        # passthrough and only `cli.py start` exports BACKEND_API, so a default would make a
        # hand-run scrape blind-POST into whatever hub happens to own that port — possibly
        # another checkout's, whose Board would then show this run's creators against that
        # corpus. Unset means silent.
        self.base = (os.environ.get("BACKEND_API") or "").strip().rstrip("/")
        self._failures = 0
        self._off = not self.base
        self._last: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return not self._off

    def emit(self, event: str, *, level: str = "info", content_id: str | None = None,
             msg: str | None = None, data: dict | None = None) -> None:
        """Post one event. Never raises, never retries."""
        if self._off:
            return
        payload = {"agent": self.agent, "event": event, "level": level,
                   "run_id": self.run_id, "platform": self.platform,
                   "content_id": content_id, "msg": msg, "data": data or {},
                   "ts": time.time()}
        req = urllib.request.Request(
            f"{self.base}/api/logs", data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
            self._failures = 0
        except Exception as e:                     # bare: telemetry must never break a run
            self._failures += 1
            log.debug("hub event %s failed (%d/%d): %s",
                      event, self._failures, MAX_FAILURES, e)
            if self._failures >= MAX_FAILURES:
                self._off = True
                log.debug("hub events disabled after %d consecutive failures", MAX_FAILURES)

    def emit_throttled(self, event: str, *, key: str = "",
                       min_interval: float = HEARTBEAT_SEC, **kw) -> None:
        """`emit`, but at most once per `min_interval`. The first call always goes.

        Throttled on a MONOTONIC clock, not wall time: a run can outlive an NTP correction,
        and a clock that steps backwards would otherwise silence the heartbeat for the
        duration of the step — precisely when an operator is watching to see whether anything
        is still alive.
        """
        now = time.monotonic()
        k = key or event
        last = self._last.get(k)
        if last is not None and now - last < min_interval:
            return
        self._last[k] = now
        self.emit(event, **kw)
