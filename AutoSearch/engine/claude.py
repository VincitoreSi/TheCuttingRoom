#!/usr/bin/env python3
"""engine/claude.py — Anthropic usage (AutoSearch/PIPELINE.md §5).

`anthropic.Anthropic()` (zero-arg — `ANTHROPIC_API_KEY` from env; never hardcoded). Model
from config, default `claude-opus-4-8`. Both call points use
`client.messages.create(..., output_config={"format": {"type": "json_schema", "schema": ...}})`
— `thinking` is deliberately OMITTED (cheapest/lowest-latency for bounded extraction; Opus
4.8 runs without thinking when the param is absent).

  * `expand_terms()`     — 1 call/run: niche + seed keywords + factors + prior trending
                           insight -> {keywords[], hashtags[], audio_terms[]}.
  * `score_candidates()` — batched (~10 candidates/call): niche + compact candidate list ->
                           {scores:[{handle, score, reasons[]}]}. Combine with heuristic
                           signals (engine/score.py) into `relevance={score, reasons}`.

Errors: the SDK auto-retries 429/5xx internally, but any exception that still escapes
`messages.create` is treated as a breaker strike (`engine/circuit.py`, 3 strikes -> clean
partial-exit via CircuitTripped). `stop_reason` is guarded: `refusal` -> skip the item,
`max_tokens` -> caller should retry with a smaller input. The first text block's JSON is
parsed with `json.loads`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from engine.circuit import CircuitBreaker
from engine.limits import BREAKER_MAX_STRIKES, CLAUDE_MAX_TOKENS
from engine.schema import KEYWORD_EXPANSION_SCHEMA, RELEVANCE_SCORE_SCHEMA

log = logging.getLogger("as.claude")

DEFAULT_MODEL = "claude-opus-4-8"


class ClaudeError(RuntimeError):
    """A non-retryable-by-us Claude call outcome (refusal, truncation, bad JSON, etc.)."""


class ClaudeClient:
    """Thin wrapper. Pass `client=` to inject a fake transport for tests — the real
    `anthropic.Anthropic()` is only constructed when no client is supplied, so unit tests
    never need ANTHROPIC_API_KEY set."""

    def __init__(self, model: str = DEFAULT_MODEL, client: Any | None = None,
                 breaker: CircuitBreaker | None = None, system_prefix: str = ""):
        self.model = model
        self.client = client if client is not None else anthropic.Anthropic()
        self.breaker = breaker or CircuitBreaker(max_strikes=BREAKER_MAX_STRIKES, pace_seconds=0.0)
        self.system_prefix = system_prefix

    # ---- low-level call: json_schema structured output, NO thinking -----------------
    def _call(self, system: str, user: str, schema: dict,
             max_tokens: int = CLAUDE_MAX_TOKENS) -> dict:
        self.breaker.pace()
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as e:
            # Any transport/API failure here is exactly the 429/5xx-class case §5 asks the
            # breaker to track (record_failure may itself raise CircuitTripped — let it).
            self.breaker.record_failure(str(e))
            raise ClaudeError(f"Claude call failed: {e}") from e

        self.breaker.record_success()

        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "refusal":
            raise ClaudeError(f"Claude refused the request (stop_reason=refusal): "
                              f"{getattr(resp, 'stop_details', None)}")
        if stop_reason == "max_tokens":
            raise ClaudeError("Claude response truncated (stop_reason=max_tokens) — "
                              "retry with a smaller input or higher max_tokens")

        text = None
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text:
            raise ClaudeError("no text content block in Claude response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ClaudeError(f"Claude did not return valid JSON: {e}") from e

    # ---- public calls -----------------------------------------------------------------
    def expand_terms(self, niche: str, seed_keywords: list[str], factors: Any = None,
                     trending_insight: str | None = None) -> dict:
        system = (
            (self.system_prefix + "\n\n" if self.system_prefix else "")
            + "You expand a content-discovery niche into a bounded set of Instagram search "
              "terms. Return ONLY the JSON object matching the schema — no commentary. Stay "
              "grounded in the niche; do not invent unrelated terms."
        )
        user = json.dumps({
            "niche": niche, "seed_keywords": seed_keywords, "factors": factors,
            "trending_insight": trending_insight,
        }, ensure_ascii=False)
        return self._call(system, user, KEYWORD_EXPANSION_SCHEMA)

    def score_candidates(self, niche: str, candidates: list[dict]) -> dict:
        system = (
            (self.system_prefix + "\n\n" if self.system_prefix else "")
            + "You score Instagram creator candidates for fit with a content niche, on a "
              "0-1 scale, with brief reasons grounded in the supplied fields (bio, category, "
              "followers, sample reel captions if present). Return ONLY the JSON object "
              "matching the schema — no commentary."
        )
        user = json.dumps({"niche": niche, "candidates": candidates}, ensure_ascii=False)
        return self._call(system, user, RELEVANCE_SCORE_SCHEMA)
