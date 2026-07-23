#!/usr/bin/env python3
"""engine/search.py — topsearch (burner), discover/chaining (burner), term -> surface
orchestration, caps, resume (AutoSearch/PIPELINE.md §3 layout, §1 SAFETY caps).

`discover_via_terms()` is the run-loop's one entry point: for each search term it runs a
guest-first (burner opt-in) surface, hydrates + scores a few candidates, and yields
candidate dicts ready for `POST /api/discovery/{p}` via the `on_candidate` callback — the
caller (cli.py) owns posting + lifecycle logging so search.py never talks to the hub.

Resume: `<platform>_raw.json` keyed by username, skip existing, re-save after each unit
(§1.4). Caps enforced via `Budget` (§1.3): <=20 topsearch + <=20 expand/run, <=150
hydrations/run, `per_term_limit` candidates hydrated+scored per term.
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

from engine import ig
from engine import score as scorelib
from engine.circuit import CircuitBreaker
from engine.limits import MAX_EXPAND_PER_RUN, MAX_HYDRATIONS_PER_RUN, MAX_TOPSEARCH_PER_RUN
from engine.schema import to_pages_handle

log = logging.getLogger("as.search")

ROOT = Path(__file__).resolve().parents[1]

# The floor between two `hydrate.progress` heartbeats, mirroring scrape.py's HubEvents
# throttle (ReelScraper core/hubevents.HEARTBEAT_SEC): one liveness ping per hydrating term
# is a moving number the board can watch, not one-event-per-item spam.
HYDRATE_PROGRESS_SEC = 30.0


class Budget:
    """Per-run/per-beat surface caps (§1.3) — config may only LOWER these, never raise."""

    def __init__(self, cfg: dict):
        self.per_term_limit = int(cfg.get("per_term_limit", 5))
        self.max_topsearch = MAX_TOPSEARCH_PER_RUN
        self.max_expand = MAX_EXPAND_PER_RUN
        self.max_hydrations = MAX_HYDRATIONS_PER_RUN
        self.topsearch_used = 0
        self.expand_used = 0
        self.hydrations_used = 0

    def can_topsearch(self) -> bool:
        return self.topsearch_used < self.max_topsearch

    def can_expand(self) -> bool:
        return self.expand_used < self.max_expand

    def can_hydrate(self) -> bool:
        return self.hydrations_used < self.max_hydrations


def raw_cache_path(platform: str) -> Path:
    return ROOT / f"{platform}_raw.json"


def load_cache(platform: str) -> dict:
    p = raw_cache_path(platform)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(platform: str, cache: dict) -> None:
    raw_cache_path(platform).write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _to_candidate(record: dict, cfg: dict) -> dict:
    handle_full = to_pages_handle(record["username"])
    heuristic = scorelib.heuristic_score(record, cfg)
    return {
        "handle": handle_full,
        "source_term": record.get("source_term"),
        "discovered_via": record.get("discovered_via"),
        "followers": record.get("followers"),
        "median_plays": record.get("median_plays"),
        "sample_reels": record.get("sample_reels") or [],
        "relevance": scorelib.combine_relevance(heuristic, None, None),
    }


def discover_via_terms(
    terms: list[str],
    cfg: dict,
    platform: str,
    guest: "ig.GuestSession",
    burner: dict | None,
    breaker: CircuitBreaker,
    budget: Budget,
    max_units: int | None = None,
    on_candidate=None,
    on_event=None,
) -> list[dict]:
    """Run guest-first (burner opt-in) discovery over `terms`. Returns the list of
    candidate dicts built (also passed to `on_candidate(candidate, record)` as each one is
    found, so the caller can POST + log per-item events immediately — a beat's trickle
    should show up on the board as it happens, not all at once at the end).

    `on_event(event, *, level, content_id, msg, data, throttle)` is the sibling emitter for
    per-QUERY observability — `query.start` / `query.skip` (guest-only) / `hydrate.progress`
    (throttled) / `gate.drop` / `query.result` — so a run that proposes nothing still leaves
    a legible "searched X, found N, hydrated H, dropped D" trail. Injected exactly like
    `on_candidate`: this module NEVER imports the hub; the caller (cli.py) owns posting.

    `max_units` bounds work to at most that many terms — a "work unit" = one search term
    executed (§2a). Respects ALL §1 pacing floors + per-run caps inside this call; raises
    `ig.RateLimited` up through `breaker.record_failure()` (-> possibly `CircuitTripped`)
    exactly like the scraper's convention."""
    cache = load_cache(platform)
    candidates: list[dict] = []
    guest_only = bool(cfg.get("guest_only", True)) or burner is None
    _emit = on_event or (lambda *a, **k: None)
    total = len(terms) if max_units is None else min(len(terms), max_units)

    for unit_i, term in enumerate(terms):
        if max_units is not None and unit_i >= max_units:
            break

        _emit("query.start", msg=f"search {term!r}",
              data={"term": term, "i": unit_i + 1, "of": total})

        found: dict[str, str] = {}  # username -> discovered_via

        if not guest_only and burner and budget.can_topsearch():
            try:
                time.sleep(random.uniform(*ig.SEARCH_DELAY))
                for u in ig.topsearch(term, burner, per_query=budget.per_term_limit):
                    found.setdefault(u["username"], "keyword_search")
                budget.topsearch_used += 1
                breaker.record_success()
            except ig.RateLimited as e:
                log.error("CIRCUIT BREAKER (topsearch)", extra={"err": str(e)})
                breaker.record_failure(str(e))
                raise
            except Exception as e:
                log.warning("topsearch failed", extra={"term": term, "err": str(e)})
                breaker.record_failure(str(e))
        elif guest_only:
            log.info("topsearch skipped — guest-only mode (login-gated surface, §1.1)",
                     extra={"term": term})
            _emit("query.skip", msg="topsearch skipped — guest-only mode (Instagram search "
                  "needs a burner login)", data={"term": term, "reason": "guest_only"})

        todo = [u for u in found if u not in cache][: budget.per_term_limit]
        hydrated = 0
        passed = 0
        dropped = 0
        for username in todo:
            if not budget.can_hydrate():
                break
            time.sleep(random.uniform(*ig.HYDRATE_DELAY))
            try:
                profile = ig.web_profile_info(username, session=guest,
                                              burner=None if guest_only else burner)
                budget.hydrations_used += 1
                breaker.record_success()
            except ig.RateLimited as e:
                log.error("CIRCUIT BREAKER (hydrate)", extra={"err": str(e)})
                breaker.record_failure(str(e))
                raise
            except Exception as e:
                log.warning("hydrate failed", extra={"username": username, "err": str(e)})
                breaker.record_failure(str(e))
                continue

            if not profile:
                continue

            hydrated += 1
            # A liveness heartbeat, deliberately NOT one of the board's six verbs and
            # throttled so a slow-hydrating term pings once, not once per profile.
            _emit("hydrate.progress", content_id=username,
                  msg=f"{username}: hydrated {hydrated}/{len(todo)}",
                  data={"term": term, "done": hydrated, "of": len(todo)},
                  throttle=HYDRATE_PROGRESS_SEC)

            median_plays, sample_reels = None, []
            if profile.get("user_id"):
                try:
                    sample_reels, median_plays = ig.sample_reels(
                        profile["user_id"], username, session=guest)
                except ig.RateLimited:
                    raise
                except Exception as e:
                    log.debug("reel sample failed", extra={"username": username, "err": str(e)})

            record = {
                **profile, "median_plays": median_plays, "sample_reels": sample_reels,
                "source_term": term, "discovered_via": found.get(username, "guest_hydration"),
            }
            cache[username] = record
            save_cache(platform, cache)

            if scorelib.passes_gates(record, cfg):
                passed += 1
                cand = _to_candidate(record, cfg)
                candidates.append(cand)
                if on_candidate:
                    on_candidate(cand, record)
            else:
                dropped += 1

        if dropped:
            _emit("gate.drop", msg=f"{term!r}: {dropped} hydrated below gates",
                  data={"term": term, "dropped": dropped})
        # The per-term summary, always emitted (even guest-only found=0) and never
        # throttled — the authoritative count the run.end tally is built from.
        _emit("query.result", msg=f"{term!r}: found {len(found)}, hydrated {hydrated}, "
              f"passed {passed}",
              data={"term": term, "found": len(found), "hydrated": hydrated,
                    "passed": passed, "dropped": dropped})

    return candidates
