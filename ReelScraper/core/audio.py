#!/usr/bin/env python3
"""
core/audio.py — shared, platform-agnostic SOUND intelligence.

Parallel to core/virality.py but for AUDIO. `audio_id` is the sound join key
(the way `content_id` is the content join key). We derive "trending now" from the
audio metadata already embedded in scraped reels (MVP — a dedicated trending-audio
scraper is a later upgrade; see PIPELINE.md §8: this is trending WITHIN your tracked
creators, not the true platform-wide chart).

Given the normalized content rows (which carry the audio_* fields from normalize.py),
we aggregate by `audio_id` and compute a `sound_trend_score` = adoption velocity:
distinct recent reels using a sound, recency-weighted and scaled by those reels'
virality_score, then percentile-normalized 0-100 exactly like the virality engine.

Buckets: Rising | Hot | Saturated | Evergreen. A representative viral reel is kept
per sound. Everything is null-tolerant — reels without audio metadata are ignored.
"""
import bisect
import time


def _pct_ranker(values):
    """Percentile rank in [0,1] over non-null values (mirrors core.virality)."""
    s = sorted(v for v in values if v is not None)
    n = len(s)
    def rank(v):
        return None if (v is None or n == 0) else bisect.bisect_right(s, v) / n
    return rank


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _bucket(uses, recent_share, span_days, trend_score):
    """Classify a sound by adoption shape. Deterministic, defensible thresholds.

    'Hot' requires MULTIPLE adopters (uses >= 2) at the top of the velocity distribution —
    a sound used by one reel is not yet trending, so it reads as 'Rising'. 'Saturated' =
    widely used but stale; 'Evergreen' = used steadily across a long span."""
    ts = trend_score or 0
    if uses >= 8 and recent_share < 0.3:
        return "Saturated"
    if span_days >= 45 and uses >= 4 and recent_share <= 0.6:
        return "Evergreen"
    if uses >= 2 and ts >= 70:
        return "Hot"
    if recent_share >= 0.5 or uses >= 2:
        return "Rising"               # newly appearing / climbing within the window
    return "Rising" if ts >= 50 else "Saturated"


def collect_sounds(rows, now=None, window_days=14):
    """rows: content dicts with audio_id/audio_*/virality_score/posted_ts/url/content_id.
    Returns a list of sound dicts sorted by trend_score desc. Pure function, no I/O."""
    now = now or time.time()
    window_s = max(window_days, 1) * 86400.0

    by_sound = {}
    for r in rows:
        aid = r.get("audio_id")
        if not aid:
            continue
        by_sound.setdefault(str(aid), []).append(r)

    sounds = []
    for aid, uses in by_sound.items():
        # a representative reel = highest virality using this sound
        example = max(uses, key=lambda r: (_f(r.get("virality_score")) or 0))
        n = len(uses)
        recent = 0
        raw = 0.0
        oldest = newest = None
        for r in uses:
            ts = _f(r.get("posted_ts"))
            vs = _f(r.get("virality_score")) or 0.0
            if ts is not None:
                oldest = ts if oldest is None else min(oldest, ts)
                newest = ts if newest is None else max(newest, ts)
                age_s = max(now - ts, 0.0)
                recency = max(0.0, 1.0 - age_s / window_s)  # 1 = brand new, 0 = at/over window edge
                if recency > 0:
                    recent += 1
                # adoption velocity contribution, scaled by the reel's virality
                raw += recency * (1.0 + vs / 100.0)
            else:
                raw += 0.1 * (1.0 + vs / 100.0)
        span_days = ((newest - oldest) / 86400.0) if (oldest is not None and newest is not None) else 0.0
        f = example
        sounds.append({
            "audio_id": aid,
            "title": f.get("audio_title"),
            "artist": f.get("audio_artist"),
            "is_original": f.get("audio_is_original"),
            "is_reusable": f.get("audio_is_reusable"),
            "sound_page_url": f.get("sound_page_url"),
            "uses_in_corpus": n,
            "uses_count_meta": f.get("audio_uses_count"),
            "recent_uses": recent,
            "_raw": raw,
            "_recent_share": (recent / n) if n else 0.0,
            "_span_days": span_days,
            "example": {
                "content_id": example.get("content_id"),
                "url": example.get("url"),
                "virality_score": _f(example.get("virality_score")),
            },
        })

    ranker = _pct_ranker([s["_raw"] for s in sounds])
    for s in sounds:
        p = ranker(s["_raw"])
        s["trend_score"] = round(100 * p, 1) if p is not None else None
        s["bucket"] = _bucket(s["uses_in_corpus"], s["_recent_share"], s["_span_days"], s["trend_score"])
        for k in ("_raw", "_recent_share", "_span_days"):
            s.pop(k, None)

    sounds.sort(key=lambda s: -(s.get("trend_score") or 0))
    return sounds
