#!/usr/bin/env python3
"""
platforms/instagram/normalize.py — Instagram raw reels -> core.schema.Content.

Reads this platform's scrape output (reels_raw*.json + profiles_meta*.json) and maps
Instagram's vocabulary onto the shared normalized record the core engine understands.
This is the ONLY Instagram-specific mapping the rest of the system needs.
"""
import json
from pathlib import Path

from scrape import flatten          # same-dir Instagram scraper
from core.schema import Content

HERE = Path(__file__).parent


def audio_fields(m):
    """Extract the shared per-reel audio/sound fields from an Instagram raw item.

    IG exposes `clips_metadata.music_info.music_asset_info` for LICENSED tracks and
    `clips_metadata.original_sound_info` for ORIGINAL audio. `audio_id` is the sound
    join key (audio_cluster_id for licensed, audio_asset_id for original). Fully
    null-tolerant — older raws that lack this metadata just yield an empty dict.
    """
    cm = m.get("clips_metadata") or {}
    mai = (cm.get("music_info") or {}).get("music_asset_info") or {}
    osi = cm.get("original_sound_info") or {}
    if mai:  # licensed / commercial track — attach manually on IG, usually not reusable
        aid = mai.get("audio_cluster_id") or mai.get("audio_asset_id")
        return {
            "audio_id": str(aid) if aid else None,
            "audio_title": mai.get("title") or mai.get("sanitized_title"),
            "audio_artist": mai.get("display_artist") or mai.get("artist_name"),
            "audio_is_original": False,
            "audio_is_reusable": bool(mai.get("allows_saving")),
            "sound_page_url": (f"https://www.instagram.com/reels/audio/{aid}/" if aid else None),
            "audio_uses_count": None,
        }
    if osi:  # original audio — public originals are typically reusable
        aid = osi.get("audio_asset_id") or osi.get("audio_cluster_id")
        return {
            "audio_id": str(aid) if aid else None,
            "audio_title": osi.get("original_audio_title") or "Original audio",
            "audio_artist": (osi.get("ig_artist") or {}).get("username"),
            "audio_is_original": True,
            "audio_is_reusable": (not bool(osi.get("is_reuse_disabled"))),
            "sound_page_url": (f"https://www.instagram.com/reels/audio/{aid}/" if aid else None),
            "audio_uses_count": osi.get("formatted_clips_media_count"),
        }
    return {}


def load_records():
    raw = {}
    for f in sorted(HERE.glob("reels_raw*.json")):
        try:
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                if len(v) >= len(raw.get(k, [])):
                    raw[k] = v
        except Exception:
            pass
    meta = {}
    for f in sorted(HERE.glob("profiles_meta*.json")):
        try:
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                meta.setdefault(k, v)
        except Exception:
            pass

    records = []
    for creator, items in raw.items():
        followers = (meta.get(creator) or {}).get("followers")
        for m in items:
            r = flatten(m, creator, followers)
            records.append(Content(
                platform="instagram",
                creator=creator,
                creator_followers=followers,
                content_id=r.get("id") or r.get("shortcode"),
                url=r.get("url"),
                posted_ts=r.get("taken_at"),
                plays=r.get("play_count") or r.get("ig_play_count"),
                likes=r.get("like_count"),
                comments=r.get("comment_count"),
                shares=r.get("reshare_count"),
                saves=r.get("save_count"),
                duration_s=r.get("video_duration"),
                caption=r.get("caption") or "",
                thumbnail_url=r.get("thumbnail_best") or "",
                media_url=r.get("video_url_best") or "",
                raw={"shortcode": r.get("shortcode")},
                **audio_fields(m),
            ))
    return records
