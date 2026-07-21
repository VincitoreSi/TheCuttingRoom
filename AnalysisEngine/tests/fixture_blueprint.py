#!/usr/bin/env python3
"""Reusable schema_version 2 blueprint fixtures for the self-tests + hub smoke test."""
from __future__ import annotations


def good_blueprint(content_id: str = "test_ae_smoke") -> dict:
    """A schema-valid, semantically-clean schema_version 2 blueprint (passes schema.all_errors)."""
    return {
        "schema_version": 2,
        "content_id": content_id,
        "url": "https://www.instagram.com/reel/EXAMPLE/",
        "model": "gemini-2.5-pro",
        "analyzed_by": "AnalysisEngine",
        "video_metadata": {
            "estimated_duration_seconds": 12.4,
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "fps": 30,
            "content_type": "fashion tutorial",
            "one_line_summary": "A quick two-shot style comparison demonstrating a color swap.",
            "detailed_summary": "A model is shown first in a pale shirt, then in a mustard shirt, "
                                "with a split-screen comparison and a follow CTA.",
            "target_platform": "Instagram Reels",
            "likely_ai_generated": False,
            "ai_generation_signals": [],
            "total_shots": 2,
        },
        "global_style": {
            "overall_mood": "confident, clean",
            "genre": "fashion how-to",
            "visual_style": "minimalist studio",
            "color_grading": "warm neutral",
            "dominant_color_palette_hex": ["#F5F1E9", "#704C38", "#333333"],
            "lighting_style": "soft diffused key light",
            "pacing": "medium",
            "editing_style": "hard cuts + split screen",
            "recurring_visual_motifs": ["off-white backdrop", "side-by-side comparison"],
            "film_look_reference": "modern e-commerce lookbook",
        },
        "audio": {
            "music_description": "upbeat lo-fi hip-hop bed with a steady kick",
            "music_genre": "lo-fi hip-hop",
            "tempo_bpm_estimate": 105,
            "music_mood": "relaxed, stylish",
            "has_voiceover": False,
            "voiceover_transcript": None,
            "has_lyrics": False,
            "lyrics_transcript": None,
            "sound_effects": ["swoosh on graphic reveal"],
            "audio_sync_notes": "cuts land on the downbeat",
            "audio_id": "1789_example_audio",
            "audio_title": "Golden Hour Bounce",
            "audio_artist": "example_artist",
            "audio_is_original": False,
            "audio_is_reusable": False,
            "sound_page_url": "https://www.instagram.com/reels/audio/1789_example_audio/",
        },
        "audio_strategy": {
            "audio_type": "trending_sound_led",
            "voiceover_role": "none",
            "music_role": "drives the pacing and the reveal beat",
            "beat_markers_s": [0.0, 2.1, 4.3, 6.0],
            "reuse_recommendation": "pick_trending",
            "substitute_brief": "105 bpm relaxed lo-fi hip-hop with a clear downbeat",
            "sync_notes": "align the color reveal to the beat at 2.1s",
        },
        "characters_and_subjects": [
            {
                "id": "character_1",
                "role": "model",
                "detailed_appearance": "young adult man, dark brown skin (#704C38), black curly "
                                       "medium hair, cream trousers, white sneakers.",
                "appears_in_shots": [1, 2],
            }
        ],
        "text_overlays": [
            {"start_time": 0.2, "end_time": 3.0, "text": "AVOID", "font_style": "heavy sans",
             "color": "#FFFFFF", "position": "top-center", "animation": "pop-in"}
        ],
        "shots": [
            {
                "shot_index": 1, "start_time": 0.0, "end_time": 6.0, "duration": 6.0,
                "description": "Medium shot of the model in a pale yellow shirt against an "
                               "off-white backdrop, neutral expression, soft key light.",
                "subjects_present": ["character_1"], "setting_location": "seamless studio",
                "action_motion": "model holds still, subtle breathing", "camera_shot_size": "medium",
                "camera_angle": "eye level", "camera_movement": "static", "lens_feel": "50mm clean",
                "lighting": "soft diffused key from front-left", "color_palette_hex": ["#F5F1E9", "#EDE6C8"],
                "mood": "neutral", "on_screen_text": "AVOID", "transition_in": "cut",
                "transition_out": "hard cut",
                "generation_prompt": "Medium eye-level static shot of a young adult man with dark "
                                     "brown skin and black curly hair, wearing a pale yellow shirt "
                                     "and cream trousers, standing against a seamless off-white "
                                     "studio backdrop under soft diffused key light, 50mm clean "
                                     "lens, warm neutral grade, 9:16 vertical.",
                "negative_prompt": "no harsh shadows, no busy background, no text artifacts, no "
                                   "extra people",
            },
            {
                "shot_index": 2, "start_time": 6.0, "end_time": 12.4, "duration": 6.4,
                "description": "Split-screen comparison: the same model on the left in pale "
                               "yellow, on the right in mustard, green check over the mustard side.",
                "subjects_present": ["character_1"], "setting_location": "seamless studio",
                "action_motion": "static comparison with a graphic check animating in",
                "camera_shot_size": "medium", "camera_angle": "eye level", "camera_movement": "static",
                "lens_feel": "50mm clean", "lighting": "soft diffused key", "color_palette_hex": ["#C89B3C", "#704C38"],
                "mood": "affirming", "on_screen_text": "WEAR", "transition_in": "hard cut",
                "transition_out": "cut to logo",
                "generation_prompt": "Split-screen medium eye-level static comparison of the same "
                                     "dark-brown-skinned man, left side in a pale yellow shirt, "
                                     "right side in a mustard shirt, seamless off-white studio "
                                     "backdrop, soft diffused key light, an animated green "
                                     "check-mark over the mustard side, warm grade, 9:16 vertical.",
                "negative_prompt": "no mismatched faces, no warped hands, no flicker, no watermark",
            },
        ],
        "regeneration_guide": {
            "recommended_models": ["Veo 3", "Kling 1.5", "Runway Gen-3"],
            "master_style_prompt": "Minimalist warm-neutral studio fashion look, seamless off-white "
                                   "backdrop, soft diffused key light, 50mm clean lens, 9:16 vertical, "
                                   "consistent dark-brown-skinned male model in cream trousers.",
            "global_negative_prompt": "no harsh shadows, no busy backgrounds, no warped anatomy, "
                                      "no watermarks, no flicker",
            "consistency_notes": "Keep character_1 identical across both shots: same face, hair, "
                                 "cream trousers, white sneakers; only the shirt color changes.",
            "assembly_instructions": "Play shot 1 (0-6s), hard cut to shot 2 split-screen (6-12.4s); "
                                     "sync the color reveal to the beat at 2.1s and 6.0s; overlay "
                                     "AVOID then WEAR; end on the brand logo.",
            "shot_prompt_sequence": [
                "Medium eye-level static shot of a young adult man with dark brown skin and black "
                "curly hair in a pale yellow shirt and cream trousers, seamless off-white studio "
                "backdrop, soft diffused key light, 50mm lens, warm neutral grade, 9:16 vertical.",
                "Split-screen medium comparison of the same man, left in pale yellow and right in "
                "mustard, seamless off-white backdrop, soft key light, an animated green check over "
                "the mustard side, warm grade, 9:16 vertical.",
            ],
        },
        "virality_formula": {
            "hook": {"type": "problem/solution", "first_seconds": "bold AVOID title over the model",
                     "on_screen_text": "AVOID"},
            "retention_devices": ["before/after reveal", "list structure", "split-screen proof"],
            "pacing": {"cuts": 2, "avg_shot_len_s": 6.2},
            "cta": {"present": True, "text": "follow for more style tips"},
            "replicable_formula": "Show an unflattering color, then hard-cut to the flattering swap "
                                  "in a split-screen with a check mark, beat-synced to a trending "
                                  "lo-fi track, close on a follow CTA.",
            "tags": ["fashion", "color-theory", "before-after"],
        },
    }


def bad_blueprint(content_id: str = "test_ae_bad") -> dict:
    """A blueprint carrying the exact defects the judge must hard-fail: placeholder
    shot_prompt_sequence + a missing audio_strategy.audio_type."""
    bp = good_blueprint(content_id)
    bp["regeneration_guide"]["shot_prompt_sequence"] = [
        "shot_1_generation_prompt",
        "shot_2_generation_prompt",
    ]
    bp["audio_strategy"].pop("audio_type", None)
    return bp
