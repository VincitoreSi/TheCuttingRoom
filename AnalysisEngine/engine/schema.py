#!/usr/bin/env python3
"""engine/schema.py — the canonical analysis blueprint (schema_version 2) + real validation.

This is the SINGLE canonical analysis document (PIPELINE.md §4, companion D1 + D3b). It is a
superset: rich generation-ready sections PLUS the lean `virality_formula` block the hub `brief`
endpoint reads, PLUS the top-level `audio_strategy` block (D3b).

Defect fixes vs. the VideoAnalysis scratch (companion "Defects to fix"):
  * The scratch carried a SCHEMA dict that was never used — here `validate()` actually runs
    jsonschema and `semantic_errors()` adds the checks jsonschema can't express.
  * The scratch degraded `shot_prompt_sequence` to placeholders like "shot_1_generation_prompt";
    `semantic_errors()` HARD-FAILS placeholder sequences.

`validate()` = structural (jsonschema). `semantic_errors()` = the meaning-level rules the judge
also enforces. `all_errors()` = both, and is what the analyze/repair loop feeds back to Gemini.
"""
from __future__ import annotations

import re
from typing import Any

import jsonschema

SCHEMA_VERSION = 2

AUDIO_TYPES = ["voiceover_led", "trending_sound_led", "music_only", "hybrid"]
REUSE_RECOMMENDATIONS = ["reuse_original", "substitute_equivalent", "pick_trending"]

# Detects the scratch's placeholder pattern, e.g. "shot_1_generation_prompt",
# "shot 3 prompt", "<shot_2>", "PLACEHOLDER", etc.
_PLACEHOLDER_RE = re.compile(
    r"(shot[_\s-]*\d+[_\s-]*(generation[_\s-]*)?prompt|placeholder|<[^>]*shot[^>]*>|tbd|todo)",
    re.IGNORECASE,
)

_hex = {"type": "string", "pattern": r"^#?[0-9a-fA-F]{3,8}$"}
_str = {"type": "string"}
_num = {"type": "number"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    d: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": True}
    if required:
        d["required"] = required
    return d


BLUEPRINT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AnalysisEngine blueprint (schema_version 2)",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version", "content_id", "analyzed_by",
        "video_metadata", "global_style", "audio", "audio_strategy",
        "shots", "regeneration_guide", "virality_formula",
    ],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "content_id": {"type": "string", "minLength": 1},
        "url": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "analyzed_by": {"type": "string"},
        "is_reference": {"type": "boolean"},
        "video_metadata": _obj(
            {
                "estimated_duration_seconds": _num,
                "aspect_ratio": _str,
                "resolution": _str,
                "fps": {"type": "number"},
                "content_type": _str,
                "one_line_summary": _str,
                "detailed_summary": _str,
                "target_platform": _str,
                "likely_ai_generated": {"type": "boolean"},
                "ai_generation_signals": {"type": "array", "items": _str},
                "total_shots": {"type": "integer"},
            },
            required=["one_line_summary", "content_type", "estimated_duration_seconds"],
        ),
        "global_style": _obj(
            {
                "overall_mood": _str,
                "genre": _str,
                "visual_style": _str,
                "color_grading": _str,
                "dominant_color_palette_hex": {"type": "array", "items": _hex, "minItems": 1},
                "lighting_style": _str,
                "pacing": _str,
                "editing_style": _str,
                "recurring_visual_motifs": {"type": "array", "items": _str},
                "film_look_reference": _str,
            },
            required=["overall_mood", "visual_style", "dominant_color_palette_hex"],
        ),
        "audio": _obj(
            {
                "music_description": _str,
                "music_genre": _str,
                "tempo_bpm_estimate": {"type": ["number", "string"]},
                "music_mood": _str,
                "has_voiceover": {"type": "boolean"},
                "voiceover_transcript": {"type": ["string", "null"]},
                "has_lyrics": {"type": "boolean"},
                "lyrics_transcript": {"type": ["string", "null"]},
                "sound_effects": {"type": "array", "items": _str},
                "audio_sync_notes": _str,
                # Passed through from the hub queue item (the model cannot read IG metadata):
                "audio_id": {"type": ["string", "null"]},
                "audio_title": {"type": ["string", "null"]},
                "audio_artist": {"type": ["string", "null"]},
                "audio_is_original": {"type": ["boolean", "null"]},
                "audio_is_reusable": {"type": ["boolean", "null"]},
                "sound_page_url": {"type": ["string", "null"]},
            },
            required=["has_voiceover"],
        ),
        "audio_strategy": _obj(
            {
                "audio_type": {"type": "string", "enum": AUDIO_TYPES},
                "voiceover_role": _str,
                "music_role": _str,
                "beat_markers_s": {"type": "array", "items": _num},
                "reuse_recommendation": {"type": "string", "enum": REUSE_RECOMMENDATIONS},
                "substitute_brief": _str,
                "sync_notes": _str,
            },
            required=["audio_type"],
        ),
        "characters_and_subjects": {
            "type": "array",
            "items": _obj(
                {
                    "id": _str,
                    "role": _str,
                    "detailed_appearance": _str,
                    "appears_in_shots": {"type": "array", "items": {"type": "integer"}},
                },
                required=["id", "detailed_appearance"],
            ),
        },
        "text_overlays": {
            "type": "array",
            "items": _obj(
                {
                    "start_time": _num,
                    "end_time": _num,
                    "text": _str,
                    "font_style": _str,
                    "color": _str,
                    "position": _str,
                    "animation": _str,
                },
                required=["text"],
            ),
        },
        "shots": {
            "type": "array",
            "minItems": 1,
            "items": _obj(
                {
                    "shot_index": {"type": "integer"},
                    "start_time": _num,
                    "end_time": _num,
                    "duration": _num,
                    "description": {"type": "string", "minLength": 1},
                    "subjects_present": {"type": "array", "items": _str},
                    "setting_location": _str,
                    "action_motion": _str,
                    "camera_shot_size": _str,
                    "camera_angle": _str,
                    "camera_movement": _str,
                    "lens_feel": _str,
                    "lighting": _str,
                    "color_palette_hex": {"type": "array", "items": _hex},
                    "mood": _str,
                    "on_screen_text": {"type": ["string", "null"]},
                    "transition_in": _str,
                    "transition_out": _str,
                    "generation_prompt": {"type": "string", "minLength": 20},
                    "negative_prompt": {"type": "string", "minLength": 1},
                },
                required=[
                    "shot_index", "start_time", "end_time", "duration",
                    "description", "generation_prompt", "negative_prompt",
                ],
            ),
        },
        "regeneration_guide": _obj(
            {
                "recommended_models": {"type": "array", "items": _str},
                "master_style_prompt": {"type": "string", "minLength": 1},
                "global_negative_prompt": {"type": "string", "minLength": 1},
                "consistency_notes": {"type": "string", "minLength": 1},
                "assembly_instructions": {"type": "string", "minLength": 1},
                "shot_prompt_sequence": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 20},
                },
            },
            required=["master_style_prompt", "shot_prompt_sequence", "assembly_instructions"],
        ),
        "virality_formula": _obj(
            {
                "hook": _obj(
                    {
                        "type": _str,
                        "first_seconds": _str,
                        "on_screen_text": {"type": ["string", "null"]},
                    }
                ),
                "retention_devices": {"type": "array", "items": _str},
                "pacing": _obj({"cuts": _num, "avg_shot_len_s": _num}),
                "cta": _obj({"present": {"type": "boolean"}, "text": {"type": ["string", "null"]}}),
                "replicable_formula": {"type": "string", "minLength": 1},
                "tags": {"type": "array", "items": _str},
            },
            required=["hook", "replicable_formula"],
        ),
        "evaluation": _obj(
            {
                "score_0_100": {"type": "number"},
                "per_criterion": {"type": "object"},
                "passes": {"type": "integer"},
                "gaps_remaining": {"type": "array", "items": _str},
                "accepted": {"type": "boolean"},
                "judge_model": _str,
                "verdict": _str,
            }
        ),
    },
}

_VALIDATOR = jsonschema.Draft202012Validator(BLUEPRINT_SCHEMA)


def validate(blueprint: dict) -> list[str]:
    """Structural validation against the schema. Returns a list of human-readable errors."""
    errors = []
    for e in sorted(_VALIDATOR.iter_errors(blueprint), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"[schema] {loc}: {e.message}")
    return errors


def semantic_errors(blueprint: dict) -> list[str]:
    """Meaning-level checks jsonschema can't express — the defects the judge hard-fails."""
    errors: list[str] = []
    shots = blueprint.get("shots") or []
    guide = blueprint.get("regeneration_guide") or {}
    seq = guide.get("shot_prompt_sequence") or []

    # DEFECT FIX: shot_prompt_sequence must be FULL prompt text, one per shot, in order —
    # never placeholder strings.
    for i, item in enumerate(seq):
        if not isinstance(item, str) or _PLACEHOLDER_RE.search(item) or len(item.strip()) < 40:
            errors.append(
                f"[semantic] regeneration_guide/shot_prompt_sequence[{i}]: looks like a "
                f"placeholder or is too short — must be the FULL ready-to-run prompt for shot {i}"
            )
    if shots and seq and len(seq) != len(shots):
        errors.append(
            f"[semantic] shot_prompt_sequence has {len(seq)} entries but there are "
            f"{len(shots)} shots — one full prompt per shot, in order, is required"
        )

    # Each shot's generation_prompt must not be a placeholder either.
    for i, s in enumerate(shots):
        gp = (s or {}).get("generation_prompt", "")
        if not isinstance(gp, str) or _PLACEHOLDER_RE.search(gp) or len(gp.strip()) < 40:
            errors.append(
                f"[semantic] shots[{i}]/generation_prompt: placeholder or too short — must be a "
                f"self-contained text-to-video prompt"
            )

    # Audio strategy rules (D3b rubric additions).
    strat = blueprint.get("audio_strategy") or {}
    audio = blueprint.get("audio") or {}
    atype = strat.get("audio_type")
    if atype not in AUDIO_TYPES:
        errors.append(f"[semantic] audio_strategy/audio_type must be one of {AUDIO_TYPES}")
    if atype == "trending_sound_led" and not (strat.get("beat_markers_s")):
        errors.append(
            "[semantic] audio_strategy/beat_markers_s must be present (non-empty) when "
            "audio_type is trending_sound_led"
        )
    if atype == "voiceover_led":
        vo = audio.get("voiceover_transcript")
        if not (audio.get("has_voiceover") and isinstance(vo, str) and vo.strip()):
            errors.append(
                "[semantic] audio.voiceover_transcript must be present and verbatim when "
                "audio_type is voiceover_led"
            )

    return errors


def all_errors(blueprint: dict) -> list[str]:
    """Structural + semantic errors — the full validation the run loop enforces."""
    return validate(blueprint) + semantic_errors(blueprint)


def is_valid(blueprint: dict) -> bool:
    return not all_errors(blueprint)
