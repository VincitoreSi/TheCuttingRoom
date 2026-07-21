#!/usr/bin/env python3
"""engine/score.py — heuristic signals (followers, median_plays, cadence) + threshold gates
(AutoSearch/PIPELINE.md §6 config_schema: min_followers, min_median_plays, relevance_threshold).

Two layers, combined by cli.py into the candidate's `relevance={score, reasons}`:
  * `heuristic_score()` — cheap, offline, always available (no Anthropic needed).
  * Claude's `score_candidates()` (engine/claude.py) — optional, blended in when present.
"""
from __future__ import annotations

from engine.limits import CLAUDE_RELEVANCE_WEIGHT, HEURISTIC_RELEVANCE_WEIGHT


def passes_gates(profile: dict, cfg: dict) -> bool:
    """The hard offline gate before a profile is even proposed: public, not private,
    meets the configured minimums. Never let a private account's scraped fields through
    (data hygiene, §1.7) — private accounts are skipped entirely, not scored."""
    if profile.get("is_private"):
        return False
    followers = profile.get("followers") or 0
    median_plays = profile.get("median_plays") or 0
    return (
        followers >= cfg.get("min_followers", 2000)
        and median_plays >= cfg.get("min_median_plays", 3000)
    )


def heuristic_score(profile: dict, cfg: dict) -> tuple[float, list[str]]:
    """A cheap 0-1 signal from public metadata alone — no Anthropic call needed. Used
    standalone when ANTHROPIC_API_KEY is absent, and blended with Claude's judgment
    otherwise (see combine_relevance)."""
    score = 0.0
    reasons: list[str] = []

    followers = profile.get("followers") or 0
    min_followers = cfg.get("min_followers", 2000)
    if followers >= min_followers:
        score += 0.35
        reasons.append(f"followers={followers} >= min_followers={min_followers}")

    median_plays = profile.get("median_plays") or 0
    min_median_plays = cfg.get("min_median_plays", 3000)
    if median_plays >= min_median_plays:
        score += 0.35
        reasons.append(f"median_plays={median_plays:.0f} >= min_median_plays={min_median_plays}")

    if profile.get("is_business"):
        score += 0.05
        reasons.append("business account")
    if profile.get("is_verified"):
        score += 0.05
        reasons.append("verified account")
    if profile.get("category"):
        reasons.append(f"category={profile['category']!r}")
    if profile.get("is_private"):
        score -= 0.5
        reasons.append("private account (penalty — should already be gated out)")

    return max(0.0, min(1.0, round(score, 3))), reasons


def combine_relevance(heuristic: tuple[float, list[str]], claude_score: float | None,
                      claude_reasons: list[str] | None) -> dict:
    """Blend the offline heuristic with Claude's relevance judgment (when available)
    into the final `relevance={score, reasons}` block posted to the hub."""
    h_score, h_reasons = heuristic
    if claude_score is None:
        return {"score": h_score, "reasons": h_reasons}
    combined = round(HEURISTIC_RELEVANCE_WEIGHT * h_score + CLAUDE_RELEVANCE_WEIGHT * claude_score, 3)
    reasons = list(claude_reasons or []) + h_reasons
    return {"score": combined, "reasons": reasons}
