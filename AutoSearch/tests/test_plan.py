#!/usr/bin/env python3
"""tests/test_plan.py — the weekly-plan + beat-gating unit test (no network, no hub, no
an LLM).

Runnable two ways:
  * pytest:  `pytest -q`  (collects the `test_*` functions below)
  * script:  `python -m tests.test_plan`  (exit 0 = pass, 1 = fail)

Verifies (AutoSearch/PIPELINE.md §7.2):
  * a FIXED seed yields a deterministic plan (same seed -> byte-identical plan).
  * day-targets sum ≈ weekly_search_budget.
  * exactly `active_days_per_week` active days; the rest are rest days with target 0.
  * no single day exceeds ~35% of the weekly budget.
  * every window falls inside `active_hours`.
  * the beat-gate no-ops on rest days / out-of-window / over-cap / probability, and acts
    otherwise — using a STUBBED clock + RNG (no real datetime.now()/random.random()).

The beat-gate test writes a daily ledger; to avoid polluting the repo's memory/caps dir it
redirects `plan.CAPS_DIR` to a throwaway temp dir for the duration of the test.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from engine import plan as planlib


def _check(label: str, cond: bool, detail: str = "") -> None:
    assert cond, (label + (f"  {detail}" if detail else ""))


WEEK_START = date(2026, 7, 13)  # a Monday
WEEKLY_BUDGET = 120
ACTIVE_DAYS = 5
ACTIVE_HOURS = [9, 23]


def _make_plan():
    return planlib.generate_plan(WEEK_START, WEEKLY_BUDGET, ACTIVE_DAYS, ACTIVE_HOURS, seed=917342)


def test_deterministic() -> None:
    p1 = _make_plan()
    p2 = _make_plan()
    _check("same seed -> identical plan", p1 == p2)

    p3 = planlib.generate_plan(WEEK_START, WEEKLY_BUDGET, ACTIVE_DAYS, ACTIVE_HOURS)  # derived seed
    p4 = planlib.generate_plan(WEEK_START, WEEKLY_BUDGET, ACTIVE_DAYS, ACTIVE_HOURS)
    _check("week_seed()-derived plan is ALSO deterministic (crash-safe resume)", p3 == p4)


def test_distribution() -> None:
    plan = _make_plan()
    days = plan["days"]

    _check("plan has exactly 7 days", len(days) == 7, str(len(days)))

    active = {iso: d for iso, d in days.items() if d["target"] > 0}
    rest = {iso: d for iso, d in days.items() if d["target"] == 0}
    _check(f"exactly {ACTIVE_DAYS} active days", len(active) == ACTIVE_DAYS, f"got {len(active)}")
    _check(f"exactly {7 - ACTIVE_DAYS} rest days", len(rest) == 7 - ACTIVE_DAYS, f"got {len(rest)}")

    for iso, d in rest.items():
        _check(f"rest day {iso} has target 0", d["target"] == 0)
        _check(f"rest day {iso} has no windows", d["windows"] == [], str(d["windows"]))

    total = sum(d["target"] for d in days.values())
    _check(f"day-targets sum ≈ weekly_search_budget ({WEEKLY_BUDGET})",
           abs(total - WEEKLY_BUDGET) <= 1, f"got {total}")

    cap = WEEKLY_BUDGET * 0.35 * 1.15  # ~35% + slack for integer rounding across few active days
    for iso, d in active.items():
        _check(f"active day {iso} target ({d['target']}) does not exceed ~35% of the week",
               d["target"] <= cap, f"cap={cap:.1f}")
        _check(f"active day {iso} has 1-2 windows", 1 <= len(d["windows"]) <= 2, str(d["windows"]))
        for w in d["windows"]:
            _check(f"  window {w} inside active_hours {ACTIVE_HOURS}",
                   ACTIVE_HOURS[0] <= w[0] < w[1] <= ACTIVE_HOURS[1], str(w))


def _cfg(**overrides):
    cfg = {"daily_search_cap": 300, "beat_action_probability": 0.35}
    cfg.update(overrides)
    return cfg


def test_beat_gate() -> None:
    plan = _make_plan()
    active_iso = next(iso for iso, d in plan["days"].items() if d["target"] > 0)
    rest_iso = next(iso for iso, d in plan["days"].items() if d["target"] == 0)
    active_day = plan["days"][active_iso]
    win = active_day["windows"][0]
    in_window_hour = (win[0] + win[1]) // 2

    y, m, d = map(int, active_iso.split("-"))

    # Redirect the ledger dir to a throwaway temp dir so the test never writes into memory/caps.
    orig_caps = planlib.CAPS_DIR
    tmp = Path(tempfile.mkdtemp(prefix="as-plan-test-"))
    planlib.CAPS_DIR = tmp
    try:
        # 1. rest day -> no-op
        ry, rm, rd = map(int, rest_iso.split("-"))
        now = datetime(ry, rm, rd, 12, 0)
        should, reason = planlib.gate_beat(_cfg(), plan, now=now, rand=lambda: 0.0)
        _check("rest day -> no-op (rest_day)", not should and reason == "rest_day", reason)

        # 2. active day but out of window -> no-op. ACTIVE_HOURS is [9,23], so 3am is guaranteed
        # outside every window regardless of how many windows the day has.
        now = datetime(y, m, d, 3, 0)
        should, reason = planlib.gate_beat(_cfg(), plan, now=now, rand=lambda: 0.0)
        _check("active day, out of window -> no-op (out_of_window)",
               not should and reason == "out_of_window", reason)

        # 3. in window, over cap -> no-op
        planlib.save_ledger(active_iso, {"date": active_iso, "done": active_day["target"],
                                         "breaker_cooldown_until": None})
        now = datetime(y, m, d, in_window_hour, 0)
        should, reason = planlib.gate_beat(_cfg(), plan, now=now, rand=lambda: 0.0)
        _check("in window, over cap -> no-op (over_cap)", not should and reason == "over_cap", reason)

        # 4. in window, under cap, breaker cooldown active -> no-op
        planlib.save_ledger(active_iso, {"date": active_iso, "done": 0,
                                         "breaker_cooldown_until": now.timestamp() + 3600})
        should, reason = planlib.gate_beat(_cfg(), plan, now=now, rand=lambda: 0.0)
        _check("in window, under cap, cooldown active -> no-op (breaker_cooldown)",
               not should and reason == "breaker_cooldown", reason)

        # 5. in window, under cap, no cooldown, probability roll fails -> no-op
        planlib.save_ledger(active_iso, {"date": active_iso, "done": 0, "breaker_cooldown_until": None})
        should, reason = planlib.gate_beat(_cfg(beat_action_probability=0.35), plan, now=now,
                                           rand=lambda: 0.99)
        _check("in window, under cap, probability fails -> no-op (probability)",
               not should and reason == "probability", reason)

        # 6. everything aligned -> ACT
        should, reason = planlib.gate_beat(_cfg(beat_action_probability=0.35), plan, now=now,
                                           rand=lambda: 0.0)
        _check("in window, under cap, no cooldown, probability passes -> ACT",
               should and reason == "act", reason)
    finally:
        planlib.CAPS_DIR = orig_caps
        shutil.rmtree(tmp, ignore_errors=True)


_TESTS = (test_deterministic, test_distribution, test_beat_gate)


def main() -> int:
    """Legacy CLI entry point (kept so `python -m tests.test_plan` still works)."""
    failures = []
    for fn in _TESTS:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except AssertionError as e:
            failures.append(fn.__name__)
            print(f"FAIL: {fn.__name__}: {e}")
    print("\nRESULT:", "ALL PASS" if not failures else "FAILURES")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
