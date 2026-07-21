#!/usr/bin/env python3
"""engine/analyze.py — compose the prompt, analyze the clip, validate, and repair.

Per-clip pipeline (companion D1 "The run loop", steps 4-5):
  * build the user instruction from the queue item (content_id, url, duration, and the audio_*
    fields the hub passes through — the model can't read IG metadata, so the hub supplies them),
  * call Gemini in JSON mode against the FRESH File API URI (re-uploading once if it expired),
  * validate with jsonschema + semantic checks, and on failure run a targeted REPAIR pass that
    feeds the exact validator errors back to the model (DEFECT FIX: the scratch's SCHEMA was
    never used — here validation is real and drives repair),
  * stamp the canonical identity fields + audio passthrough (D3b) and return the blueprint.
"""
from __future__ import annotations

import json
import logging

from engine import schema
from engine.gemini import GeminiFileExpired

log = logging.getLogger("ae.analyze")

_AUDIO_PASSTHROUGH = [
    "audio_id", "audio_title", "audio_artist",
    "audio_is_original", "audio_is_reusable", "sound_page_url",
]


class Analyzer:
    def __init__(self, gemini, temperature: float = 0.4, max_output_tokens: int = 64000,
                 max_repairs: int = 2):
        self.gemini = gemini
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.max_repairs = max_repairs

    # ---- prompt construction --------------------------------------------------------
    def _context_block(self, item: dict, is_reference: bool) -> str:
        dur = item.get("duration_s") or item.get("duration")
        lines = [
            "## This clip (from the hub — use these EXACT identity values, do not invent them)",
            f"- content_id: {item.get('content_id')}",
            f"- url: {item.get('url')}",
            f"- known duration_s: {dur}",
            f"- caption: {item.get('caption')}",
            f"- is_reference: {is_reference}",
        ]
        audio_known = {k: item.get(k) for k in _AUDIO_PASSTHROUGH if item.get(k) is not None}
        if audio_known:
            lines.append(
                "- hub-supplied audio metadata (copy verbatim into the `audio` block; the model "
                f"cannot read IG metadata): {json.dumps(audio_known, ensure_ascii=False)}"
            )
        return "\n".join(lines)

    def _first_pass_prompt(self, item: dict, is_reference: bool) -> str:
        return (
            "Analyze the attached vertical short-form video EXHAUSTIVELY, shot by shot, and return "
            "ONLY the schema_version 2 blueprint JSON described in your system instruction. Every "
            "shot needs start/end/duration and a self-contained `generation_prompt` + "
            "`negative_prompt`; `regeneration_guide.shot_prompt_sequence` must contain the FULL "
            "prompt text for each shot IN ORDER (never placeholder strings); transcribe voiceover / "
            "overlay text VERBATIM; use concrete hex palettes; infer the `audio_strategy` block.\n\n"
            + self._context_block(item, is_reference)
        )

    def _repair_prompt(self, errors: list[str]) -> str:
        bullets = "\n".join(f"- {e}" for e in errors[:40])
        return (
            "Your previous JSON blueprint FAILED validation. Fix ONLY these problems and return the "
            "COMPLETE corrected schema_version 2 blueprint JSON (no commentary, no markdown fences). "
            "Keep everything that was already correct; do not drop shots.\n\n"
            "Validation errors:\n" + bullets
        )

    # ---- generation with expiry-safe file handling ----------------------------------
    def _generate(self, system_prompt: str, user_prompt: str, get_file_uri) -> dict:
        uri = get_file_uri(force_refresh=False)
        try:
            return self.gemini.generate_json(
                system_prompt, user_prompt, file_uri=uri,
                temperature=self.temperature, max_output_tokens=self.max_output_tokens,
            )
        except GeminiFileExpired:
            log.warning("File API URI expired — re-uploading fresh")
            uri = get_file_uri(force_refresh=True)
            return self.gemini.generate_json(
                system_prompt, user_prompt, file_uri=uri,
                temperature=self.temperature, max_output_tokens=self.max_output_tokens,
            )

    # ---- public: full analyze + repair loop -----------------------------------------
    def analyze(self, item: dict, platform: str, system_prompt: str, get_file_uri,
                is_reference: bool = False) -> tuple[dict, list[str]]:
        """Returns (blueprint, remaining_errors). remaining_errors is [] on a clean result."""
        prompt = self._first_pass_prompt(item, is_reference)
        blueprint = self._generate(system_prompt, prompt, get_file_uri)
        blueprint = self.enrich(blueprint, item, platform, is_reference)

        errors = schema.all_errors(blueprint)
        attempt = 0
        while errors and attempt < self.max_repairs:
            attempt += 1
            log.info("repair pass", extra={"attempt": attempt, "errors": len(errors)})
            repaired = self._generate(system_prompt, self._repair_prompt(errors), get_file_uri)
            repaired = self.enrich(repaired, item, platform, is_reference)
            new_errors = schema.all_errors(repaired)
            if len(new_errors) < len(errors):
                blueprint, errors = repaired, new_errors
            else:
                errors = new_errors
                break
        return blueprint, errors

    def refine_with_gaps(self, item: dict, platform: str, system_prompt: str, get_file_uri,
                         gaps: list[str], is_reference: bool = False) -> dict:
        """Regenerate the blueprint addressing the judge's gaps (companion D1 step 7 refine)."""
        bullets = "\n".join(f"- {g}" for g in gaps[:40]) or "- improve overall quality"
        prompt = (
            "Your blueprint was judged and needs revision. Address these gaps and return the "
            "COMPLETE corrected schema_version 2 blueprint JSON only (no commentary, no fences). "
            "Keep everything already correct; do not drop shots; keep shot_prompt_sequence as FULL "
            "per-shot prompts in order.\n\nGaps to fix:\n" + bullets
        )
        blueprint = self._generate(system_prompt, prompt, get_file_uri)
        return self.enrich(blueprint, item, platform, is_reference)

    # ---- canonical identity + audio passthrough (D3b) -------------------------------
    def enrich(self, blueprint: dict, item: dict, platform: str, is_reference: bool) -> dict:
        """Stamp the canonical join/identity fields and pass through the hub's audio metadata."""
        blueprint.pop("_finish_reason", None)
        blueprint["schema_version"] = schema.SCHEMA_VERSION
        blueprint["content_id"] = item.get("content_id")  # from the queue, NEVER invented
        blueprint["analyzed_by"] = "AnalysisEngine"
        blueprint.setdefault("model", getattr(self.gemini, "model", None))
        if item.get("url"):
            blueprint.setdefault("url", item.get("url"))
        if is_reference:
            blueprint["is_reference"] = True

        audio = blueprint.setdefault("audio", {})
        for k in _AUDIO_PASSTHROUGH:
            if item.get(k) is not None:
                audio[k] = item.get(k)  # hub is source of truth for IG metadata
        return blueprint
