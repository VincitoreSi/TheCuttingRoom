#!/usr/bin/env python3
"""
core/schema.py — the ONE normalized content record every platform emits.

Each platform scraper is different (Instagram reels, X posts, YouTube Shorts) and
its raw payload differs. Each platform ships a `normalize.py` that converts its raw
data into a list of these records. The shared virality engine (core/virality.py) and
memory layer (core/memory.py) then work on this common shape — write once, reuse for
every platform.

Field meanings are deliberately generic so each platform can map its own vocabulary:
  plays      <- IG "play_count" / YouTube "viewCount" / X "impression_count"
  likes      <- likes / favorites
  comments   <- comments / replies
  shares     <- IG "reshare_count" / YouTube n/a / X reposts+quotes
  saves      <- IG "save_count" / YouTube n/a / X bookmarks
Anything a platform can't provide should be left as None (the engine handles gaps).
"""
from dataclasses import dataclass, asdict, field
from typing import Optional
import time

# Canonical column order used by outputs + memory
FIELDS = [
    "platform", "creator", "creator_followers", "content_id", "url",
    "posted_ts", "posted_iso", "plays", "likes", "comments", "shares", "saves",
    "engagements", "duration_s", "caption", "thumbnail_url", "media_url",
    # audio / sound intelligence (audio_id is the sound join key, parallel to content_id).
    # Null-tolerant: older raws / platforms without sound metadata leave these None.
    "audio_id", "audio_title", "audio_artist", "audio_is_original",
    "audio_is_reusable", "sound_page_url", "audio_uses_count",
]


@dataclass
class Content:
    platform: str                      # "instagram" | "x" | "youtube"
    creator: str
    content_id: str
    url: str
    creator_followers: Optional[int] = None
    posted_ts: Optional[int] = None    # unix seconds
    plays: Optional[int] = None        # views / impressions
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    duration_s: Optional[float] = None
    caption: str = ""
    thumbnail_url: str = ""
    media_url: str = ""
    # --- audio / sound (the audio_id join key) — all optional, null-tolerant ---
    audio_id: Optional[str] = None            # IG audio_cluster_id (licensed) / audio_asset_id (original)
    audio_title: Optional[str] = None
    audio_artist: Optional[str] = None
    audio_is_original: Optional[bool] = None  # original audio vs licensed/commercial track
    audio_is_reusable: Optional[bool] = None  # public original you can reuse vs licensed (attach manually)
    sound_page_url: Optional[str] = None      # https://www.instagram.com/reels/audio/<audio_id>/
    audio_uses_count: Optional[str] = None    # reels-using count if present in metadata (may be a "1.2K" string)
    raw: dict = field(default_factory=dict)  # platform-specific extras, not scored

    @property
    def posted_iso(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.posted_ts)) if self.posted_ts else ""

    @property
    def engagements(self) -> int:
        return sum(v for v in (self.likes, self.comments, self.shares, self.saves) if v)

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["posted_iso"] = self.posted_iso
        d["engagements"] = self.engagements
        return {k: d.get(k) for k in FIELDS}

    def memory_text(self) -> str:
        """Compact text used for semantic/lexical recall of this piece of content."""
        bits = [self.platform, self.creator]
        if self.caption:
            bits.append(self.caption)
        return "  ".join(b for b in bits if b)


def from_dict(d: dict) -> "Content":
    known = {f.name for f in Content.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return Content(**{k: v for k, v in d.items() if k in known})
