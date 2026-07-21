#!/usr/bin/env python3
"""engine/plan.py — weekly-plan generation/reload (§2a), daily ledger, beat-gating logic
(§2b) (AutoSearch/PIPELINE.md §2 "Cadence — WEEKLY budget -> RANDOM daily -> HEARTBEAT").

The weekly plan is DETERMINISTIC from a per-week seed (`week_seed()` hashes `week_start`),
so a crashed/restarted process resumes the SAME plan, never a fresh random one. Rest days
get `target:0`; active days split `weekly_budget` with no single day exceeding ~35% of the
week (water-filling cap + redistribution); each active day gets 1-2 random hour windows
inside `active_hours`.

The beat-gate (`gate_beat`) is a pure function of (config, plan, now, rand) so it is
trivially unit-testable with a stubbed clock + RNG (tests/test_plan.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

log = logging.getLogger("as.plan")

ROOT = Path(__file__).resolve().parents[1]
MEM_DIR = ROOT / "memory"
PLAN_PATH = MEM_DIR / "plan.json"
CAPS_DIR = MEM_DIR / "caps"

DAY_CAP_SHARE = 0.35  # "no single day exceeds ~35% of the week" (§2a)


# ---- tiny JSON helpers (no cross-project dependency) -----------------------------------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---- §2a: the weekly plan ---------------------------------------------------------------
def week_start(d: date_cls) -> date_cls:
    """Monday of the ISO week containing `d`."""
    return d - timedelta(days=d.weekday())


def week_seed(week_start_iso: str) -> int:
    """Deterministic seed derived from the week_start date string alone."""
    h = hashlib.sha1(week_start_iso.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _cap_shares(weights: list[float], cap_share: float) -> list[float]:
    """Water-filling: normalize weights to shares summing to 1, then iteratively cap any
    share above `cap_share` and redistribute the excess proportionally among the
    still-uncapped days. Guarantees (barring an cap_share*n < 1 pathology) no day exceeds
    the cap by more than a rounding sliver."""
    n = len(weights)
    if n == 0:
        return []
    total = sum(weights) or 1.0
    shares = [w / total for w in weights]
    capped = [False] * n
    for _ in range(n):
        remaining_cap_budget = 1.0 - sum(s for s, c in zip(shares, capped) if c)
        uncapped_total = sum(s for s, c in zip(shares, capped) if not c)
        changed = False
        for i in range(n):
            if capped[i]:
                continue
            projected = (shares[i] / uncapped_total * remaining_cap_budget
                        if uncapped_total > 0 else 0.0)
            if projected > cap_share + 1e-9:
                shares[i] = cap_share
                capped[i] = True
                changed = True
        if not changed:
            break
    # final pass: renormalize the uncapped shares to fill exactly the remaining budget
    remaining_cap_budget = 1.0 - sum(s for s, c in zip(shares, capped) if c)
    uncapped_total = sum(s for s, c in zip(shares, capped) if not c)
    if uncapped_total > 0:
        for i in range(n):
            if not capped[i]:
                shares[i] = shares[i] / uncapped_total * remaining_cap_budget
    return shares


def generate_plan(week_start_date: date_cls, weekly_budget: int, active_days_per_week: int,
                  active_hours: list[int], seed: int | None = None) -> dict:
    """Deterministic weekly plan (AutoSearch/PIPELINE.md §2a)."""
    if seed is None:
        seed = week_seed(week_start_date.isoformat())
    rng = random.Random(seed)

    active_days_per_week = max(1, min(7, int(active_days_per_week)))
    days = [week_start_date + timedelta(days=i) for i in range(7)]
    active_idx = sorted(rng.sample(range(7), active_days_per_week))

    weights = [rng.uniform(0.5, 1.5) for _ in active_idx]
    shares = _cap_shares(weights, DAY_CAP_SHARE)
    raw_targets = [weekly_budget * s for s in shares]
    day_targets = [int(round(t)) for t in raw_targets]

    # Spread rounding drift round-robin across active days (never dump it all on one day,
    # which could push a single day back over the ~35% cap).
    drift = weekly_budget - sum(day_targets)
    step = 1 if drift > 0 else -1
    i = 0
    while drift != 0 and day_targets:
        idx = i % len(day_targets)
        if day_targets[idx] + step >= 0:
            day_targets[idx] += step
            drift -= step
        i += 1
        if i > 10_000:  # pathological guard; should never trigger
            break

    start_h, end_h = active_hours[0], active_hours[1]
    result_days: dict[str, dict] = {}
    for i, d in enumerate(days):
        iso = d.isoformat()
        if i in active_idx:
            pos = active_idx.index(i)
            target = max(0, day_targets[pos])
            n_windows = rng.choice([1, 2])
            windows = []
            for _ in range(n_windows):
                span = max(1, end_h - start_h)
                w_start = rng.randint(start_h, max(start_h, end_h - 1))
                w_len = rng.randint(1, max(1, min(3, end_h - w_start)))
                w_end = min(end_h, w_start + w_len)
                windows.append([w_start, w_end])
            windows.sort()
            result_days[iso] = {"target": target, "windows": windows}
        else:
            result_days[iso] = {"target": 0, "windows": []}

    return {
        "week_start": week_start_date.isoformat(),
        "weekly_budget": weekly_budget,
        "seed": seed,
        "active_days_per_week": active_days_per_week,
        "active_hours": list(active_hours),
        "days": result_days,
    }


def load_or_generate_plan(cfg: dict, now: datetime | None = None) -> dict:
    """Load memory/plan.json, regenerating when the ISO week rolls over or the cadence
    config changed. Deterministic — a crashed/restarted process resumes the SAME plan."""
    now = now or datetime.now()
    ws = week_start(now.date())
    weekly_budget = cfg.get("weekly_search_budget", 120)
    active_days_per_week = cfg.get("active_days_per_week", 5)
    active_hours = cfg.get("active_hours", [9, 23])

    existing = _read_json(PLAN_PATH, None)
    if (existing
            and existing.get("week_start") == ws.isoformat()
            and existing.get("weekly_budget") == weekly_budget
            and existing.get("active_days_per_week") == active_days_per_week
            and existing.get("active_hours") == list(active_hours)):
        return existing

    plan = generate_plan(ws, weekly_budget, active_days_per_week, active_hours)
    _write_json(PLAN_PATH, plan)
    log.info("weekly plan (re)generated", extra={"week_start": plan["week_start"],
                                                 "seed": plan["seed"]})
    return plan


# ---- daily ledger -----------------------------------------------------------------------
def _ledger_path(date_str: str) -> Path:
    return CAPS_DIR / f"{date_str}.json"


def load_ledger(date_str: str) -> dict:
    return _read_json(_ledger_path(date_str), {"date": date_str, "done": 0,
                                               "breaker_cooldown_until": None})


def save_ledger(date_str: str, ledger: dict) -> None:
    _write_json(_ledger_path(date_str), ledger)


def increment_ledger(date_str: str, n: int = 1) -> dict:
    ledger = load_ledger(date_str)
    ledger["done"] = int(ledger.get("done", 0)) + n
    save_ledger(date_str, ledger)
    return ledger


def set_breaker_cooldown(date_str: str, until_ts: float) -> dict:
    ledger = load_ledger(date_str)
    ledger["breaker_cooldown_until"] = until_ts
    save_ledger(date_str, ledger)
    return ledger


# ---- §2b: the beat gate -------------------------------------------------------------------
def gate_beat(cfg: dict, plan: dict, now: datetime | None = None,
             rand: Callable[[], float] | None = None) -> tuple[bool, str]:
    """Pure function: should THIS beat act, and why/why-not. Stubbable clock (`now`) + RNG
    (`rand`) make this trivially unit-testable. Checks, in order (first failure wins):
      1. today not a rest day (target > 0)
      2. now inside one of today's windows
      3. today's ledger done < min(target, daily_search_cap)
      4. the breaker cooldown (if any) has elapsed
      5. random() < beat_action_probability
    "Most beats no-op — that is the point" (organic scatter, §2b)."""
    now = now or datetime.now()
    rand = rand or random.random
    date_str = now.date().isoformat()

    day = (plan.get("days") or {}).get(date_str)
    if not day or int(day.get("target", 0)) <= 0:
        return False, "rest_day"

    windows = day.get("windows") or []
    hour = now.hour + now.minute / 60.0
    if not any(w[0] <= hour < w[1] for w in windows):
        return False, "out_of_window"

    ledger = load_ledger(date_str)
    cap = min(int(day["target"]), int(cfg.get("daily_search_cap", 300)))
    if int(ledger.get("done", 0)) >= cap:
        return False, "over_cap"

    cooldown_until = ledger.get("breaker_cooldown_until")
    if cooldown_until and now.timestamp() < float(cooldown_until):
        return False, "breaker_cooldown"

    if rand() >= float(cfg.get("beat_action_probability", 0.35)):
        return False, "probability"

    return True, "act"
