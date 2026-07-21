#!/usr/bin/env python3
"""platforms/x/normalize.py — X raw posts -> core.schema.Content.

Expected raw per post (adapt to your scraper's field names):
  id, created_at(unix), text, public_metrics{impression_count, like_count,
  reply_count, retweet_count, quote_count, bookmark_count}, video{duration_s}
Mapping to the shared schema:
  plays  <- impression_count (or video view_count)
  likes  <- like_count      comments <- reply_count
  shares <- retweet_count + quote_count       saves <- bookmark_count
"""
import json
from pathlib import Path
from core.schema import Content

HERE = Path(__file__).parent

def load_records():
    raw = {}
    for f in sorted(HERE.glob("posts_raw*.json")):
        try:
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                if len(v) >= len(raw.get(k, [])): raw[k] = v
        except Exception: pass
    meta = {}
    for f in sorted(HERE.glob("profiles_meta*.json")):
        try:
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                meta.setdefault(k, v)
        except Exception: pass

    records = []
    for creator, items in raw.items():
        followers = (meta.get(creator) or {}).get("followers")
        for p in items:
            pm = p.get("public_metrics") or {}
            records.append(Content(
                platform="x", creator=creator, creator_followers=followers,
                content_id=str(p.get("id")),
                url=f"https://x.com/{creator}/status/{p.get('id')}",
                posted_ts=p.get("created_at"),
                plays=pm.get("impression_count") or (p.get("video") or {}).get("view_count"),
                likes=pm.get("like_count"), comments=pm.get("reply_count"),
                shares=(pm.get("retweet_count") or 0) + (pm.get("quote_count") or 0),
                saves=pm.get("bookmark_count"),
                duration_s=(p.get("video") or {}).get("duration_s"),
                caption=p.get("text") or "",
            ))
    return records
