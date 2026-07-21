#!/usr/bin/env python3
"""
platforms/youtube/scrape.py — YouTube Shorts scraper on the shared virality core.

Key-free: instead of the YouTube Data API v3 (quota + API key), this hits YouTube's
own internal "InnerTube" API — the same endpoints youtube.com's web player uses — with
the public web client key. No login, no personal API key, no quota billing.

Produces the raw files the shared `normalize.py` expects:
    shorts_raw.json     {channel: [ <video> ]}
    profiles_meta.json  {channel: {"followers": int}}   # subscriberCount
Each <video> mirrors the Data API v3 shape normalize.py reads:
    {id, snippet{publishedAt,title,description},
     statistics{viewCount,likeCount,commentCount},
     contentDetails{duration ISO-8601}}
Then `python run.py analyze` normalizes + scores + remembers via the shared core.

HOW IT WORKS
  1. fetch the channel's /shorts page HTML → parse `ytInitialData` for the channelId +
     subscriber count and the first grid of Shorts.
  2. page through the Shorts grid with InnerTube `browse` continuations to collect ids.
  3. per short, InnerTube `player` gives exact viewCount, length, publish date, title,
     description; `next` (best-effort) adds like/comment counts.

SAFETY: public read-only requests, randomized pacing, a 3-strike circuit breaker, and
resume (channels already in shorts_raw*.json are skipped). No credentials involved.

USAGE (run from inside this folder):
    python scrape.py                        # channels in pages.txt
    python scrape.py @MrBeast UCxx.. url    # specific channels
    python scrape.py --limit 100 --fast     # --fast skips like/comment lookups
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.logsetup import setup_logging  # noqa: E402

HERE = Path(__file__).parent
log = logging.getLogger("youtube.scrape")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
INNERTUBE = "https://www.youtube.com/youtubei/v1"
# NOT A SECRET — please read before "rotating" this or filing a secret-scanning alert.
#
# This is the PUBLIC InnerTube web-client key: a fixed constant that youtube.com ships in
# its own page JavaScript to every anonymous visitor. It is not a credential, it is not
# tied to any account, project, or quota of ours, and it grants exactly the access an
# unauthenticated browser already has. It is world-readable at https://www.youtube.com/
# (grep the HTML for "INNERTUBE_API_KEY"), and the same constant is hardcoded by other
# public clients for the same reason — see yt-dlp's youtube extractor:
# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_base.py
#
# It is here as a FALLBACK only: _get() scrapes the live key out of the first page fetch
# and overwrites `_api_key` (below). The constant just keeps the scraper working if that
# extraction fails. Removing it does not improve security; it only makes the key-free
# access path brittle.
#
# Secret scanners flag it because it pattern-matches a Google API key (`AIza...`). That is
# a false positive, and it does NOT contradict SECURITY.md's "never commit secrets" — no
# real credential (IG/X session cookies, GEMINI_API_KEY, ANTHROPIC_API_KEY, NVIDIA_API_KEY)
# is ever committed; those are read from gitignored per-agent .env files by name only.
DEFAULT_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
DEFAULT_CLIENT_VERSION = "2.20240101.00.00"

PAGE_DELAY = (2.0, 5.0)         # between continuation pages
VIDEO_DELAY = (0.6, 1.6)        # between per-video metadata calls
CREATOR_DELAY = (8.0, 16.0)     # between channels
MAX_ERR_IN_A_ROW = 3            # circuit breaker

# discovered from the first page fetch (fall back to constants)
_api_key = DEFAULT_KEY
_client_version = DEFAULT_CLIENT_VERSION
_consec_err = 0


class Blocked(Exception):
    pass


# ── http ───────────────────────────────────────────────────────────────────────
def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _context() -> dict:
    return {"client": {"clientName": "WEB", "clientVersion": _client_version, "hl": "en", "gl": "US"}}


def _innertube(endpoint: str, body: dict, timeout: int = 30) -> dict:
    """POST to an InnerTube endpoint with the 3-strike circuit breaker."""
    global _consec_err
    url = f"{INNERTUBE}/{endpoint}?key={_api_key}"
    payload = json.dumps({"context": _context(), **body}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json", "Accept-Language": "en"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _consec_err = 0
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _consec_err += 1
                if _consec_err >= MAX_ERR_IN_A_ROW:
                    raise Blocked(f"{_consec_err} consecutive 429s — stopping to avoid an IP block")
                wait = 20 * (attempt + 1) + random.uniform(0, 5)
                log.warning("rate limited — backing off", extra={"endpoint": endpoint, "wait_s": round(wait), "consec": _consec_err})
                time.sleep(wait)
                continue
            log.warning("http error, retrying", extra={"endpoint": endpoint, "code": e.code, "attempt": attempt})
            time.sleep(4 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("network error, retrying", extra={"endpoint": endpoint, "attempt": attempt, "err": str(e)})
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"innertube {endpoint} failed after retries")


# ── parsing helpers ──────────────────────────────────────────────────────────────
def _balanced_json(text: str, marker: str):
    """Return the JSON object that immediately follows `marker` in `text` (brace-matched)."""
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i)
    if i < 0:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except Exception:
                        return None
    return None


def _deep_find_all(obj, key):
    """Yield every value stored under `key` anywhere in a nested dict/list."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == key:
                    yield v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


def _deep_find_first(obj, key):
    for v in _deep_find_all(obj, key):
        return v
    return None


def _parse_count(s):
    """'1,234' / '1.2K subscribers' / '3.4M views' -> int."""
    if s is None:
        return None
    m = re.search(r"([\d.,]+)\s*([KMB]?)", str(s), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2).upper(), 1)
    return int(n * mult)


def _secs_to_iso(secs):
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return None
    h, rem = divmod(secs, 3600)
    mi, s = divmod(rem, 60)
    out = "PT" + (f"{h}H" if h else "") + (f"{mi}M" if mi else "") + (f"{s}S" if s or (not h and not mi) else "")
    return out


# ── channel resolution ───────────────────────────────────────────────────────────
def _channel_url(entry: str) -> str:
    e = entry.strip()
    m = re.search(r"youtube\.com/(channel/UC[\w-]+|@[\w.\-]+|c/[\w.\-]+|user/[\w.\-]+)", e)
    if m:
        base = m.group(1)
    elif e.startswith("UC") and len(e) == 24:
        base = f"channel/{e}"
    elif e.startswith("@"):
        base = e
    else:
        base = f"@{e.lstrip('@')}"
    return f"https://www.youtube.com/{base}/shorts"


def load_channel(entry: str):
    """Fetch the channel's /shorts page. Returns a dict:
    {key, followers, video_ids, continuation} where `key` is the readable @handle
    when available (falls back to the UC id, then the raw entry)."""
    global _api_key, _client_version
    html = _get(_channel_url(entry))
    km = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    if km:
        _api_key = km.group(1)
    cm = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html) or re.search(r'"clientVersion":"([^"]+)"', html)
    if cm:
        _client_version = cm.group(1)

    data = _balanced_json(html, "var ytInitialData =") or _balanced_json(html, "ytInitialData =") or {}
    channel_id = None
    meta = _deep_find_first(data, "channelMetadataRenderer") or {}
    if isinstance(meta, dict):
        channel_id = meta.get("externalId")
    if not channel_id:
        channel_id = _deep_find_first(data, "channelId") or _deep_find_first(data, "browseId")

    hm = (re.search(r'"canonicalBaseUrl":"/@([A-Za-z0-9_.\-]+)"', html)
          or re.search(r'vanityChannelUrl":"http[^"]*/@([A-Za-z0-9_.\-]+)"', html))
    handle = hm.group(1) if hm else None

    followers = None
    for txt in _deep_find_all(data, "content"):
        if isinstance(txt, str) and "subscriber" in txt.lower():
            followers = _parse_count(txt)
            break
    if followers is None:
        sc = _deep_find_first(data, "subscriberCountText")
        if isinstance(sc, dict):
            followers = _parse_count(sc.get("simpleText") or json.dumps(sc))

    video_ids, continuation = _extract_shorts(data)
    key = handle or channel_id or entry.lstrip("@")
    return {"key": key, "followers": followers, "video_ids": video_ids, "continuation": continuation}


def _extract_shorts(data):
    """Pull short videoIds (in order) + the next continuation token from a browse payload."""
    ids, seen = [], set()
    for ep in _deep_find_all(data, "reelWatchEndpoint"):
        vid = isinstance(ep, dict) and ep.get("videoId")
        if vid and vid not in seen:
            seen.add(vid)
            ids.append(vid)
    if not ids:  # older layout
        for r in _deep_find_all(data, "reelItemRenderer"):
            vid = isinstance(r, dict) and r.get("videoId")
            if vid and vid not in seen:
                seen.add(vid)
                ids.append(vid)
    continuation = None
    for c in _deep_find_all(data, "continuationCommand"):
        if isinstance(c, dict) and c.get("token"):
            continuation = c["token"]
    return ids, continuation


def more_shorts(continuation):
    """Fetch the next page of Shorts via InnerTube browse continuation."""
    j = _innertube("browse", {"continuation": continuation})
    return _extract_shorts(j)


# ── per-video metadata ───────────────────────────────────────────────────────────
def fetch_video(video_id: str, want_engagement: bool):
    j = _innertube("player", {"videoId": video_id})
    vd = j.get("videoDetails") or {}
    micro = (j.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not vd.get("videoId") and not vd.get("title"):
        return None  # unavailable / private / geo-blocked
    like_count = comment_count = None
    if want_engagement:
        like_count, comment_count = _fetch_engagement(video_id)
    return {
        "id": video_id,
        "snippet": {
            "publishedAt": micro.get("publishDate") or micro.get("uploadDate"),
            "title": vd.get("title") or micro.get("title", {}).get("simpleText", ""),
            "description": vd.get("shortDescription") or "",
        },
        "statistics": {
            "viewCount": vd.get("viewCount"),
            "likeCount": str(like_count) if like_count is not None else None,
            "commentCount": str(comment_count) if comment_count is not None else None,
        },
        "contentDetails": {"duration": _secs_to_iso(vd.get("lengthSeconds"))},
    }


def _fetch_engagement(video_id: str):
    """Best-effort like + comment counts from the InnerTube `next` endpoint. Tolerates absence."""
    try:
        j = _innertube("next", {"videoId": video_id})
    except Exception:
        return None, None
    like = None
    # modern like button carries an accessibility label like "12,345 likes"
    for a11y in _deep_find_all(j, "accessibilityText"):
        if isinstance(a11y, str) and re.search(r"\blike", a11y, re.I) and re.search(r"\d", a11y):
            like = _parse_count(a11y)
            break
    if like is None:
        for lc in _deep_find_all(j, "likeCount"):
            if isinstance(lc, (int, str)):
                like = _parse_count(lc)
                break
    comments = None
    for cc in _deep_find_all(j, "commentCount"):
        val = cc.get("simpleText") if isinstance(cc, dict) else cc
        if val is not None:
            comments = _parse_count(val)
            break
    return like, comments


# ── driver ───────────────────────────────────────────────────────────────────────
def collect_shorts(entry: str, resolved: dict, limit: int, want_engagement: bool):
    key = resolved["key"]
    followers = resolved["followers"]
    ids = list(resolved["video_ids"])
    continuation = resolved["continuation"]
    pages = 0
    while len(ids) < limit and continuation:
        pages += 1
        time.sleep(random.uniform(*PAGE_DELAY))
        more, continuation = more_shorts(continuation)
        before = len(ids)
        seen = set(ids)
        ids.extend(v for v in more if v not in seen)
        log.info("shorts page", extra={"channel": entry, "page": pages, "added": len(ids) - before, "total": len(ids)})
        if len(ids) == before:
            break
    ids = ids[:limit]

    videos = []
    for n, vid in enumerate(ids, 1):
        try:
            rec = fetch_video(vid, want_engagement)
        except Blocked:
            raise
        except Exception as e:
            log.warning("video metadata failed", extra={"channel": entry, "video": vid, "err": str(e)})
            rec = None
        if rec:
            videos.append(rec)
        if n % 10 == 0 or n == len(ids):
            log.info("videos", extra={"channel": entry, "done": n, "of": len(ids), "kept": len(videos)})
        time.sleep(random.uniform(*VIDEO_DELAY))
    return followers, videos


def read_pages(path: Path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    setup_logging("scrape", platform="youtube")
    ap = argparse.ArgumentParser(description="YouTube Shorts virality scraper (key-free InnerTube)")
    ap.add_argument("channels", nargs="*", help="@handles, UC ids, or channel URLs")
    ap.add_argument("--file", help="input file (one channel per line)")
    ap.add_argument("--limit", type=int, default=None, help="max shorts per channel")
    ap.add_argument("--fast", action="store_true", help="skip like/comment lookups (faster)")
    args = ap.parse_args()

    if args.limit is None:
        cfg = _load_json(HERE / "niche_config.json")
        args.limit = int(cfg.get("shorts_per_creator") or 200)

    channels = list(args.channels)
    if args.file:
        for c in read_pages(Path(args.file)):
            if c not in channels:
                channels.append(c)
    if not channels and (HERE / "pages.txt").exists():
        channels = read_pages(HERE / "pages.txt")
    if not channels:
        log.error("no channels given — pass @handles/ids, --file, or fill pages.txt")
        sys.exit(1)

    shorts_path = HERE / "shorts_raw.json"
    meta_path = HERE / "profiles_meta.json"
    shorts_all = _load_json(shorts_path)
    meta_all = _load_json(meta_path)

    # cheap pre-filter on the raw entry (handle or UC id); a channel whose resolved
    # @handle is already saved is caught again after the (single, cheap) resolve below.
    done_keys = {k.lstrip("@") for k in shorts_all}
    todo = [c for c in channels if c.lstrip("@") not in done_keys]
    log.info("plan", extra={"assigned": len(channels), "scraping": len(todo), "limit": args.limit, "engagement": not args.fast})

    stopped = False
    for n, c in enumerate(todo, 1):
        log.info("channel start", extra={"i": n, "of": len(todo), "channel": c})
        try:
            resolved = load_channel(c)
        except Exception as e:
            log.error("could not resolve channel, skipping", extra={"channel": c, "err": str(e)})
            continue
        key = resolved["key"]
        log.info("resolved", extra={"channel": c, "key": key, "followers": resolved["followers"],
                                    "first_batch": len(resolved["video_ids"])})
        if key.lstrip("@") in done_keys:
            log.info("already saved, skipping", extra={"channel": c, "key": key})
            continue
        try:
            followers, videos = collect_shorts(c, resolved, args.limit, want_engagement=not args.fast)
        except Blocked as e:
            log.error("CIRCUIT BREAKER — saving partial progress and exiting", extra={"reason": str(e)})
            stopped = True
            break
        except Exception as e:
            log.error("channel failed, skipping", extra={"channel": c, "err": str(e)})
            continue
        shorts_all[key] = videos
        done_keys.add(key.lstrip("@"))
        if followers is not None:
            meta_all[key] = {"followers": followers}
        shorts_path.write_text(json.dumps(shorts_all, ensure_ascii=False, indent=1), encoding="utf-8")
        meta_path.write_text(json.dumps(meta_all, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("channel done", extra={"channel": c, "key": key, "shorts": len(videos)})
        if n < len(todo):
            time.sleep(random.uniform(*CREATOR_DELAY))

    total = sum(len(v) for v in shorts_all.values())
    status = "STOPPED EARLY (rate limit)" if stopped else "DONE"
    log.info("%s — %d shorts across %d channels -> %s", status, total, len(shorts_all), shorts_path.name,
             extra={"status": status, "shorts": total, "channels": len(shorts_all)})
    log.info("next: run `python run.py analyze`")


if __name__ == "__main__":
    main()
