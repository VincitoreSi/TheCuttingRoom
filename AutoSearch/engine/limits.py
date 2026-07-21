#!/usr/bin/env python3
"""engine/limits.py — the ONE place every AutoSearch magic number lives.

These are the hard SAFETY floors/ceilings from AutoSearch/PIPELINE.md §1.3/§1.4 (pacing,
breaker, per-run surface caps) plus the handful of other numeric constants scattered
through the agent (LLM call shape, relevance blend weights, breaker defaults). Every
other module imports from here instead of hardcoding a literal, so a single file answers
"what are this agent's non-negotiable numbers" for an operator or an auditor.

**§1.0 precedence still applies**: the pacing floors and surface ceilings below may only be
made MORE conservative by config (`pacing_seconds` in `CONFIG_SCHEMA` raises the floor;
`per_term_limit`/`daily_search_cap` may only lower a ceiling) — no config value, flag, or
refactor may relax them. Consolidating them here does not make them configurable; it makes
them auditable in one place.
"""
from __future__ import annotations

# ---- §1.3 pacing FLOORS (seconds; always `random.uniform(*tuple)`, jittered) -----------
SEARCH_DELAY = (6.0, 12.0)
EXPAND_DELAY = (10.0, 20.0)
HYDRATE_DELAY = (4.0, 8.0)
SURFACE_DELAY = (15.0, 30.0)

# ---- §1.3/§1.4 breaker + session-refresh ------------------------------------------------
MAX_429_IN_A_ROW = 3               # RateLimited trips after this many consecutive 429s
SESSION_REFRESH_EVERY = 25         # force a fresh guest session every N requests
BACKOFF_BASE_SECONDS = 15          # backoff = BACKOFF_BASE_SECONDS * (attempt + 1) + jitter
BACKOFF_MAX_ATTEMPTS = 4
BACKOFF_JITTER_MAX_SECONDS = 5.0
STALE_SESSION_RETRY_SLEEP_SECONDS = 2.0  # after a 401/403 refresh, before retrying once

# ---- §1.3 per-run surface CEILINGS (config `per_term_limit`/`daily_search_cap` may only
# lower these, never raise them) ---------------------------------------------------------
MAX_TOPSEARCH_PER_RUN = 20
MAX_EXPAND_PER_RUN = 20
MAX_HYDRATIONS_PER_RUN = 150

# ---- §2b: cooldown a tripped breaker imposes on the rest of the day's beats ------------
BREAKER_COOLDOWN_SECONDS = 30 * 60

# ---- Instagram surface identity (the one non-numeric "magic value" worth centralizing
# alongside the numbers — matches ReelScraper's scraper exactly, §1.2) ------------------
IG_APP_ID = "936619743392459"
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# ---- engine/circuit.py CircuitBreaker defaults -----------------------------------------
BREAKER_MAX_STRIKES = 3
BREAKER_DEFAULT_PACE_SECONDS = 2.0

# ---- engine/gemini.py call shape --------------------------------------------------------
# Named for the role, not the vendor: this agent has already moved Anthropic -> Gemini once,
# and the constant did not need to change when it did.
LLM_MAX_TOKENS = 4096

# ---- engine/score.py relevance blend (heuristic vs. LLM judgment) ---------------------
# NOTE: the LLM half is NOT reachable today. engine/search.py:85 calls
# `combine_relevance(heuristic, None, None)`, so `score_candidates()` is never invoked and
# candidate relevance is 100% heuristic regardless of any key. These weights only take
# effect if that call site starts passing a real score.
HEURISTIC_RELEVANCE_WEIGHT = 0.4
LLM_RELEVANCE_WEIGHT = 0.6
