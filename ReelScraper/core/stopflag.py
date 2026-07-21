#!/usr/bin/env python3
"""core/stopflag.py — cooperative SIGTERM handling for the long-running stages.

The hub's Stop button sends SIGTERM to a stage's whole process group. Under Python's
DEFAULT disposition that kills the process outright: no `finally`, no flush, no chance to
finish the file it was halfway through writing. Installing a handler that does nothing but
set a flag is worth more than every atomic write in `core.atomicio`, because it removes the
mid-write kill entirely rather than making it survivable — SIGTERM can no longer interrupt
a write at all once a handler owns it. The atomic writes are the second line of defence,
for the SIGKILL the hub escalates to after its grace period.

The flag is only useful if something checks it. The scrapers check it at the top of the
per-creator loop, which is the one point where stopping is genuinely free: they save the
whole corpus after every creator, so a stop between creators keeps everything scraped so
far. That is the entire reason the Stop button is worth having.
"""
from __future__ import annotations

import logging
import signal
import threading
import time

log = logging.getLogger("stopflag")

_STOP = threading.Event()
_INSTALLED = False


def install_stop_handler(signals=(signal.SIGTERM, signal.SIGINT)) -> None:
    """Take SIGTERM (and Ctrl-C) over: set a flag, log once, and RETURN.

    Returning is the point. The default SIGTERM disposition ends the process between two
    bytecodes — which, in `save_outputs`, is between `open()`'s truncate and the write that
    would have refilled the file, leaving `reels_raw.json` short. With this handler
    installed that window does not exist for SIGTERM; the run instead stops at the next
    per-creator boundary with everything already saved intact.

    Idempotent, and never fatal: a signal module that refuses the handler (a non-main
    thread, an exotic platform) must not stop a scrape from running at all — it just means
    the process falls back to the old, blunt behaviour."""
    global _INSTALLED
    if _INSTALLED:
        return

    def _handler(signum, frame):
        _STOP.set()
        # No work beyond the flag and a log line: a signal handler runs between arbitrary
        # bytecodes, and doing anything re-entrant here (saving, closing files) would
        # reintroduce exactly the mid-write corruption this exists to prevent.
        log.warning("stop requested — finishing the current item, then saving",
                    extra={"signal": int(signum)})

    for s in signals:
        try:
            signal.signal(s, _handler)
        except (ValueError, OSError, AttributeError):
            continue
    _INSTALLED = True


def stop_requested() -> bool:
    """True once a stop signal has landed. Cheap enough to call in any loop."""
    return _STOP.is_set()


def sleep_unless_stopped(seconds: float, slice_s: float = 0.5) -> bool:
    """Sleep, but wake immediately when a stop lands. Returns True if it was cut short.

    A bare `time.sleep` cannot be shortened by the flag: since PEP 475, a sleep interrupted
    by a signal whose handler returns normally is RESUMED for the remaining time rather than
    raising. The inter-creator delay is 10-20 seconds and it is precisely the window a stop
    is most likely to land in, so without this the operator would watch a "stopping…" toast
    for twenty seconds after a button press and reasonably conclude the button was broken.

    Uses Event.wait rather than a poll loop so a stop is noticed within milliseconds while
    the process still sleeps rather than spins."""
    if seconds <= 0:
        return _STOP.is_set()
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if _STOP.wait(min(slice_s, remaining)):
            return True


def reset_for_tests() -> None:
    """Clear the flag. Tests only — a stop is one-way for the life of a real process."""
    _STOP.clear()
