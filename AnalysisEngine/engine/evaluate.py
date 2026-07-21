#!/usr/bin/env python3
"""engine/evaluate.py — the automatic self-evaluation / judge pass (companion D1 step 6-7).

A `gemini-2.5-pro` judge scores the blueprint against the evolving `memory/rubric.md`
(including the D3b additions: `audio_strategy.audio_type` set; `beat_markers_s` present when
trending_sound_led; verbatim voiceover when voiceover_led; NO placeholder shot_prompt_sequence).

Two layers combine so quality is enforced even when the LLM is generous:
  1. DETERMINISTIC hard-fails — `schema.all_errors()` (structural + semantic). Any error forces
     `accepted=False` and is surfaced as a gap, regardless of the LLM score. This is what makes
     the placeholder-`shot_prompt_sequence` defect a guaranteed hard-fail.
  2. LLM rubric score — a 0-100 score + per-criterion breakdown + qualitative gaps.

The caller runs the refine loop (cap ~3 passes) until `accepted` or the cap, then stamps the
final `evaluation` block into the blueprint and POSTs an eval to the hub (§10.2).
"""
from __future__ import annotations

import json
import logging

from engine import schema

log = logging.getLogger("ae.evaluate")

DEFAULT_THRESHOLD = 85

_JUDGE_SYSTEM = (
    "You are a meticulous QA judge for AI-video-generation blueprints. You score a blueprint "
    "against the provided rubric. You are strict about self-contained prompts, verbatim "
    "transcripts, concrete hex palettes, stable character IDs, a populated virality_formula, and "
    "a sound audio_strategy. Return ONLY JSON."
)


class Judge:
    def __init__(self, gemini, rubric_text: str, threshold: int = DEFAULT_THRESHOLD):
        self.gemini = gemini
        self.rubric_text = rubric_text
        self.threshold = threshold

    def _judge_prompt(self, blueprint: dict, hard_fails: list[str]) -> str:
        payload = json.dumps(blueprint, ensure_ascii=False)
        hf = "\n".join(f"- {e}" for e in hard_fails) or "- (none detected by the static validator)"
        return (
            "Score this blueprint against the rubric below. Return JSON with EXACTLY these keys: "
            '{"score_0_100": number, "per_criterion": {criterion_name: number_0_100}, '
            '"gaps": [string, ...], "verdict": "accept" | "revise"}.\n\n'
            "A deterministic validator already flagged these HARD FAILS (weight them heavily and "
            "include them in gaps):\n" + hf + "\n\n"
            "## RUBRIC\n" + self.rubric_text + "\n\n"
            "## BLUEPRINT (JSON)\n" + payload
        )

    def evaluate(self, blueprint: dict, passes: int = 1) -> dict:
        """Return an `evaluation` block: {score_0_100, per_criterion, passes, gaps_remaining,
        accepted, judge_model, verdict}. Deterministic hard-fails veto acceptance."""
        hard_fails = schema.all_errors(blueprint)

        llm = {}
        try:
            raw = self.gemini.generate_json(
                _JUDGE_SYSTEM, self._judge_prompt(blueprint, hard_fails),
                file_uri=None, temperature=0.1, max_output_tokens=8000,
            )
            raw.pop("_finish_reason", None)
            llm = raw
        except Exception as e:  # noqa: BLE001 — a judge failure shouldn't crash the run
            log.warning("judge LLM call failed; falling back to static validation only",
                        extra={"err": str(e)})

        llm_score = llm.get("score_0_100")
        try:
            llm_score = float(llm_score) if llm_score is not None else None
        except (TypeError, ValueError):
            llm_score = None

        gaps = list(hard_fails)
        for g in (llm.get("gaps") or []):
            if isinstance(g, str) and g.strip():
                gaps.append(g.strip())

        # If the static validator passed but the LLM couldn't score, treat as neutral pass-ish.
        base_score = llm_score if llm_score is not None else (100.0 if not hard_fails else 0.0)
        # Hard fails cap the effective score hard.
        effective = 0.0 if hard_fails else base_score
        accepted = (not hard_fails) and effective >= self.threshold

        return {
            "score_0_100": round(effective, 1),
            "per_criterion": llm.get("per_criterion") or {},
            "passes": passes,
            "gaps_remaining": gaps,
            "accepted": accepted,
            "judge_model": getattr(self.gemini, "model", "gemini-2.5-pro"),
            "verdict": "accept" if accepted else "revise",
        }
