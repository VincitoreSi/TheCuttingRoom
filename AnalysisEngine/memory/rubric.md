# Blueprint evaluation rubric (the judge scores against this)

The self-eval judge (`gemini-2.5-pro`) scores a blueprint 0-100 across the criteria below and
returns per-criterion scores + gaps. A blueprint is **accepted** only when the deterministic
validator reports NO hard-fails AND the overall score ≥ the configured threshold (default 85).
Any hard-fail forces `accepted:false` regardless of the LLM score.

## Hard-fail criteria (any one → reject)
- **schema_valid** — validates against schema_version 2 (structural jsonschema pass).
- **no_placeholder_prompts** — `regeneration_guide.shot_prompt_sequence` contains the FULL
  prompt text for each shot, in order, one per shot. Strings like `"shot_1_generation_prompt"`,
  `"placeholder"`, `"TBD"`, or trivially short entries are an automatic fail. Each shot's
  `generation_prompt` is likewise self-contained (not a placeholder).
- **audio_type_set** — `audio_strategy.audio_type` is one of
  `voiceover_led | trending_sound_led | music_only | hybrid`.
- **beat_markers_when_trending** — when `audio_type == "trending_sound_led"`,
  `audio_strategy.beat_markers_s` is present and non-empty (so a substitute sound can be
  beat-matched).
- **verbatim_voiceover_when_vo_led** — when `audio_type == "voiceover_led"`,
  `audio.voiceover_transcript` is present and verbatim.

## Scored criteria (0-100 each; the model reports per_criterion)
- **shot_coverage** — every cut/beat is a shot with numeric start/end/duration; no gaps.
- **prompt_quality** — each `generation_prompt` is concrete and self-contained (subject, action,
  setting, camera size/angle/movement, lens feel, lighting, palette, mood) — recreatable alone.
- **verbatim_text** — voiceover, lyrics, and on-screen text transcribed exactly; `text_overlays`
  timed.
- **palette_concreteness** — `dominant_color_palette_hex` and per-shot `color_palette_hex` use
  real hex codes, not colour names.
- **character_consistency** — stable character ids reused across shots; `detailed_appearance` and
  `consistency_notes` are concrete enough to keep a subject identical across shots.
- **virality_formula_populated** — `hook`, `retention_devices`, `pacing`, `cta`,
  `replicable_formula`, and `tags` are all filled (the hub `brief` endpoint consumes this).
- **audio_strategy_soundness** — `audio_type`, `voiceover_role`/`music_role`,
  `reuse_recommendation`, and `substitute_brief` are coherent with the actual audio track; if the
  hub supplied `audio_*` metadata it is carried through unchanged.
- **regeneration_completeness** — `master_style_prompt`, `global_negative_prompt`,
  `consistency_notes`, and `assembly_instructions` give a downstream agent everything to rebuild
  and edit the video.
