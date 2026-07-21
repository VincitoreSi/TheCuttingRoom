#!/usr/bin/env python3
"""platforms/youtube/normalize.py — YouTube Shorts raw -> core.schema.Content.

Expected raw per video (YouTube Data API v3 shape):
  id, snippet{publishedAt, title, description}, statistics{viewCount, likeCount,
  commentCount}, contentDetails{duration ISO-8601}
Mapping:
  plays <- viewCount   likes <- likeCount   comments <- commentCount
  shares/saves <- None (YouTube doesn't expose these)
  followers <- channel subscriberCount (in profiles_meta.json)
"""
import json, re
from datetime import datetime, timezone
from pathlib import Path
from core.schema import Content

HERE = Path(__file__).parent

def _iso_ts(s):
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None

def _dur(iso):  # PT1M5S -> seconds
    if not iso: return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso) 
    if not m: return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h*3600 + mi*60 + s

def load_records():
    raw = {}
    for f in sorted(HERE.glob("shorts_raw*.json")):
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
        for v in items:
            sn = v.get("snippet") or {}; st = v.get("statistics") or {}
            cd = v.get("contentDetails") or {}
            vid = v.get("id") if isinstance(v.get("id"), str) else (v.get("id") or {}).get("videoId")
            records.append(Content(
                platform="youtube", creator=creator, creator_followers=followers,
                content_id=str(vid), url=f"https://www.youtube.com/shorts/{vid}",
                posted_ts=_iso_ts(sn.get("publishedAt")),
                plays=int(st["viewCount"]) if st.get("viewCount") else None,
                likes=int(st["likeCount"]) if st.get("likeCount") else None,
                comments=int(st["commentCount"]) if st.get("commentCount") else None,
                shares=None, saves=None,
                duration_s=_dur(cd.get("duration")),
                caption=(sn.get("title") or "") + ("\n" + sn["description"] if sn.get("description") else ""),
            ))
    return records
