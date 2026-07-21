#!/usr/bin/env python3
"""engine/gemini.py — Gemini REST client for AutoSearch's optional LLM calls.

Stdlib `urllib` only, no SDK — the same choice `AnalysisEngine/engine/gemini.py` makes, and
the reason AutoSearch now has no third-party runtime dependency beyond `jsonschema`. This
client is text-only: AnalysisEngine needs the File API to upload video, AutoSearch never
sends anything but JSON, so none of that machinery is duplicated here.

WHY GEMINI, AND WHY OPTIONAL. This agent used Anthropic and declared `ANTHROPIC_API_KEY`
`required: True`, which was wrong twice over: the code always degraded gracefully without it
(`cli.py` falls back to the seed keywords verbatim), and it asked for a second paid provider
when the pipeline already standardises on `GEMINI_API_KEY` for AnalysisEngine and
SimilarContent. One key now covers every agent that can spend credits, and discovery does
not spend any unless `term_expansion_enabled` is turned on.

  * `expand_terms()`     — 1 call/run: niche + seed keywords + factors + prior trending
                           insight -> {keywords[], hashtags[], audio_terms[]}.
  * `score_candidates()` — batched: niche + compact candidate list -> {scores:[...]}.
                           NOT WIRED IN TODAY — `engine/search.py` calls
                           `combine_relevance(heuristic, None, None)`, so candidate scoring
                           is 100% heuristic. Ported to keep parity with the Anthropic
                           client it replaces; wire it there or delete both together.

Errors: any transport/API failure is a breaker strike (`engine/circuit.py`, 3 strikes ->
clean partial-exit via CircuitTripped), matching the old client's contract exactly so the
caller's error handling did not have to change.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from engine.circuit import CircuitBreaker
from engine.limits import BREAKER_MAX_STRIKES, LLM_MAX_TOKENS
from engine.schema import KEYWORD_EXPANSION_SCHEMA, RELEVANCE_SCORE_SCHEMA

log = logging.getLogger("as.gemini")

_BASE = "https://generativelanguage.googleapis.com"

# Same order AnalysisEngine resolves in (`AnalysisEngine/cli.py:41`), so one exported
# variable works for every agent and an operator never has to remember which name goes where.
GEMINI_ENV_VARS = ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY")

# Flash, not Pro. Term expansion is bounded JSON extraction over a few hundred tokens — the
# task Flash is built for — and this call only ever happens because an operator deliberately
# opted into spending credits, so the default should be the cheap one. Override with the
# `model` config knob on the agent's desk.
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiError(RuntimeError):
    """A non-retryable-by-us Gemini call outcome (refusal, truncation, bad JSON, etc.)."""


def resolve_api_key() -> str:
    """First non-empty of the accepted env names, or "" — never raises, never logs a value."""
    for name in GEMINI_ENV_VARS:
        val = os.environ.get(name)
        if val:
            return val
    return ""


class GeminiClient:
    """Thin wrapper. Pass `transport=` to inject a fake for tests — the real key is only
    resolved when no transport is supplied, so unit tests never need a Gemini key set."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 breaker: CircuitBreaker | None = None, system_prefix: str = "",
                 transport: Any | None = None, timeout: int = 60):
        self.model = model
        self.transport = transport
        self.api_key = api_key if api_key is not None else ("" if transport else resolve_api_key())
        if not self.api_key and transport is None:
            raise GeminiError(
                "no Gemini key in env (GEMINI_API_KEY / GEMINI_KEY / GOOGLE_API_KEY)")
        self.breaker = breaker or CircuitBreaker(max_strikes=BREAKER_MAX_STRIKES, pace_seconds=0.0)
        self.system_prefix = system_prefix
        self.timeout = timeout

    # ---- low-level HTTP ----------------------------------------------------------------
    def _post(self, url: str, payload: dict) -> tuple[int, str]:
        if self.transport is not None:            # tests inject here
            return self.transport(url, payload)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            raise GeminiError(f"transport error calling Gemini: {e.reason}") from e

    # ---- structured JSON call ----------------------------------------------------------
    def _call(self, system: str, user: str, schema: dict,
              max_output_tokens: int = LLM_MAX_TOKENS) -> dict:
        self.breaker.pace()
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": max_output_tokens,
                # responseSchema makes Gemini emit the shape directly. engine/schema.py
                # still validates the result — a model honouring a schema is not the same
                # as a model being correct, and the caller already falls back to seeds.
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(schema),
            },
        }
        url = f"{_BASE}/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        try:
            status, text = self._post(url, payload)
        except GeminiError as e:
            # Transport-class failure is exactly what the breaker tracks (record_failure may
            # itself raise CircuitTripped — let it through).
            self.breaker.record_failure(str(e))
            raise
        if status >= 300:
            self.breaker.record_failure(f"HTTP {status}")
            raise GeminiError(f"generateContent {status}: {text[:500]}")

        self.breaker.record_success()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise GeminiError(f"Gemini response was not JSON: {e}") from e

        cands = data.get("candidates") or []
        if not cands:
            fb = (data.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(f"no candidates returned (blockReason={fb}): {text[:300]}")
        cand = cands[0]
        finish = cand.get("finishReason")
        if finish == "SAFETY":
            raise GeminiError("Gemini blocked the request (finishReason=SAFETY)")
        if finish == "MAX_TOKENS":
            raise GeminiError("Gemini response truncated (finishReason=MAX_TOKENS) — "
                              "retry with a smaller input or higher max_output_tokens")
        out = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        if not out.strip():
            raise GeminiError(f"empty text in candidate (finishReason={finish})")
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise GeminiError(f"Gemini did not return valid JSON: {e}") from e

    # ---- public calls ------------------------------------------------------------------
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


def _to_gemini_schema(schema: dict) -> dict:
    """Strip JSON-Schema keywords Gemini's responseSchema subset rejects.

    engine/schema.py's validators are full JSON Schema and stay the source of truth for
    validation; this is only the generation hint. Passing `additionalProperties` or `$schema`
    through makes the API 400, which would turn every expansion into a fallback-to-seeds and
    look like the feature silently not working.
    """
    drop = {"additionalProperties", "$schema", "$id", "title", "default", "examples",
            "minimum", "maximum", "minItems", "maxItems", "uniqueItems", "pattern"}
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in drop:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _to_gemini_schema(v)
        else:
            out[k] = v
    return out
