#!/usr/bin/env python3
"""
platforms/x/scrape.py — X (Twitter) scraper on the shared virality core.

Pulls up to N posts per handle in platforms/x/pages.txt (metrics + text + video
duration) plus each account's follower count, and writes the raw files the shared
`normalize.py` expects:
    posts_raw.json      {handle: [ <post> ]}
    profiles_meta.json  {handle: {"followers": int}}
Then `python run.py analyze` normalizes + scores + remembers via the shared core.

── HOW IT GETS DATA ─────────────────────────────────────────────────────────────
X removed free guest access in 2023, so a logged-in session is REQUIRED. This scraper
reads ONE account's cookies and calls X's internal GraphQL API (the same endpoints the
web app uses). It never logs in for you and never stores a password — you supply the
two session cookies of an already-logged-in browser session.

⚠️  USE A BURNER ACCOUNT. These are real, authenticated requests; heavy or bot-like use
    can get the account limited or suspended. This scraper paces itself and trips a
    circuit breaker after 3 rate-limits, but the account risk is yours — do not use a
    personal account. (Instagram stays guest-only; this file does not change that.)

── PROVIDE THE SESSION (either way) ─────────────────────────────────────────────
  env:   export X_AUTH_TOKEN=xxxxxxxx   export X_CT0=yyyyyyyy
  file:  platforms/x/session.txt  (git-ignored) containing:
             auth_token=xxxxxxxx
             ct0=yyyyyyyy
Get them from a logged-in x.com browser tab → DevTools → Application → Cookies →
copy `auth_token` and `ct0`.

── IF SCRAPING SUDDENLY 400/404s ───────────────────────────────────────────────
X rotates its GraphQL query-id hashes. Update QID_USER / QID_TWEETS below (or set
X_QID_USER / X_QID_TWEETS env vars) with the current hashes from x.com's JS bundle
(DevTools → Network → filter "UserTweets" → the id in the request URL).

USAGE (run from inside this folder):
    python scrape.py                     # scrape handles in pages.txt
    python scrape.py nasa natgeo         # scrape specific handles
    python scrape.py --file pages.txt --limit 100
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.logsetup import setup_logging  # noqa: E402
from core.atomicio import write_text_atomic  # noqa: E402
from core.stopflag import install_stop_handler, stop_requested, sleep_unless_stopped  # noqa: E402

HERE = Path(__file__).parent
log = logging.getLogger("x.scrape")

# ── constants ────────────────────────────────────────────────────────────────────
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
# public web-app bearer (a constant shipped in x.com's JS; not account-specific)
BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
          "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
API = "https://x.com/i/api/graphql"
# GraphQL query-id hashes — rotate occasionally; override via env if scraping 404s.
QID_USER = os.getenv("X_QID_USER", "32pL5BWe9WKeSK1MoPvFQQ")    # UserByScreenName
QID_TWEETS = os.getenv("X_QID_TWEETS", "E3opETHurmVJflFsUBVuUQ")  # UserTweets

PAGE_SIZE = 20
PAGE_DELAY = (5.0, 12.0)        # seconds between timeline pages (slower than IG — real acct)
CREATOR_DELAY = (20.0, 40.0)    # seconds between creators
MAX_LIMIT_IN_A_ROW = 3          # circuit breaker

# feature flags X requires on these endpoints (missing ones -> 400 with a list).
_FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "articles_preview_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
}

_auth_token = ""
_ct0 = ""
_consec_limit = 0


class RateLimited(Exception):
    pass


# ── session ────────────────────────────────────────────────────────────────────
def load_session() -> bool:
    """Load auth_token + ct0 from env or platforms/x/session.txt. Never logged."""
    global _auth_token, _ct0
    _auth_token = os.getenv("X_AUTH_TOKEN", "").strip()
    _ct0 = os.getenv("X_CT0", "").strip()
    sess = HERE / "session.txt"
    if (not _auth_token or not _ct0) and sess.exists():
        for line in sess.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip().lower(), v.strip()
                if k == "auth_token" and not _auth_token:
                    _auth_token = v
                elif k == "ct0" and not _ct0:
                    _ct0 = v
            elif not _auth_token:
                _auth_token = line
            elif not _ct0:
                _ct0 = line
    return bool(_auth_token and _ct0)


def _http(url: str, timeout: int = 30):
    headers = {
        "User-Agent": UA,
        "authorization": f"Bearer {BEARER}",
        "x-csrf-token": _ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "Referer": "https://x.com/",
        "Cookie": f"auth_token={_auth_token}; ct0={_ct0}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _gql(qid: str, name: str, variables: dict, features: dict | None = None):
    """Call a GraphQL query with the 3-strike rate-limit circuit breaker."""
    global _consec_limit
    params = {"variables": json.dumps(variables, separators=(",", ":"))}
    params["features"] = json.dumps(features if features is not None else _FEATURES,
                                    separators=(",", ":"))
    url = f"{API}/{qid}/{name}?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            _, t = _http(url)
            _consec_limit = 0
            return json.loads(t)
        except urllib.error.HTTPError as e:
            if e.code in (429, 88):  # rate limited
                _consec_limit += 1
                if _consec_limit >= MAX_LIMIT_IN_A_ROW:
                    raise RateLimited(f"{_consec_limit} consecutive rate-limits — stopping to protect the account")
                wait = 30 * (attempt + 1) + random.uniform(0, 10)
                log.warning("rate limited — backing off", extra={"query": name, "wait_s": round(wait), "consec": _consec_limit})
                time.sleep(wait)
                continue
            if e.code in (401, 403):
                body = e.read().decode("utf-8", "replace")[:300]
                log.error("auth rejected — check X_AUTH_TOKEN/X_CT0 (session may be dead)",
                          extra={"code": e.code, "query": name, "body": body})
                raise RateLimited(f"session rejected ({e.code}) — refresh cookies")
            if e.code in (400, 404):
                body = e.read().decode("utf-8", "replace")[:400]
                log.error("bad request — query-id/features may be stale; update QID_%s or features" % name,
                          extra={"code": e.code, "query": name, "body": body})
                raise
            log.warning("http error, retrying", extra={"code": e.code, "query": name, "attempt": attempt})
            time.sleep(5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("network error, retrying", extra={"query": name, "attempt": attempt, "err": str(e)})
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{name} failed after retries")


# ── profile + timeline ─────────────────────────────────────────────────────────
def get_user(handle: str):
    """Resolve a handle -> {'rest_id': str, 'followers': int}."""
    variables = {"screen_name": handle, "withSafetyModeUserFields": True}
    j = _gql(QID_USER, "UserByScreenName", variables)
    result = (((j.get("data") or {}).get("user") or {}).get("result")) or {}
    if not result or result.get("__typename") == "UserUnavailable":
        return None
    rest_id = result.get("rest_id")
    legacy = result.get("legacy") or {}
    core = result.get("core") or {}
    followers = legacy.get("followers_count")
    if followers is None:
        followers = legacy.get("normal_followers_count")
    if followers is None and isinstance(core, dict):
        followers = core.get("followers_count")
    if not rest_id:
        return None
    return {"rest_id": str(rest_id), "followers": followers}


def _unwrap_tweet(result: dict) -> dict | None:
    """A tweet_results.result may be a Tweet or a TweetWithVisibilityResults wrapper."""
    if not result:
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet") or {}
    if result.get("tweet"):
        return result.get("tweet")
    return result


def _parse_created(s):
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        try:
            return int(time.mktime(time.strptime(s, "%a %b %d %H:%M:%S +0000 %Y")))
        except Exception:
            return None


def _video_duration_s(legacy: dict):
    ent = (legacy.get("extended_entities") or legacy.get("entities") or {})
    for m in ent.get("media", []) or []:
        vi = m.get("video_info") or {}
        if vi.get("duration_millis"):
            return round(vi["duration_millis"] / 1000, 1)
    return None


def _tweet_to_record(tw: dict) -> dict | None:
    legacy = tw.get("legacy") or {}
    rest_id = tw.get("rest_id") or legacy.get("id_str")
    if not rest_id or not legacy:
        return None
    views = tw.get("views") or {}
    note = ((tw.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    text = note.get("text") or legacy.get("full_text") or legacy.get("text") or ""
    return {
        "id": str(rest_id),
        "created_at": _parse_created(legacy.get("created_at")),
        "text": text,
        "public_metrics": {
            "impression_count": int(views["count"]) if str(views.get("count") or "").isdigit() else None,
            "like_count": legacy.get("favorite_count"),
            "reply_count": legacy.get("reply_count"),
            "retweet_count": legacy.get("retweet_count"),
            "quote_count": legacy.get("quote_count"),
            "bookmark_count": legacy.get("bookmark_count"),
        },
        "video": {"duration_s": _video_duration_s(legacy)},
    }


def _walk_timeline(j):
    """Yield ('tweet', tweet_dict) and ('cursor', value) from a UserTweets response."""
    user = ((j.get("data") or {}).get("user") or {}).get("result") or {}
    timeline = ((user.get("timeline_v2") or user.get("timeline") or {}).get("timeline")) or {}
    for instr in timeline.get("instructions", []):
        if instr.get("type") == "TimelineAddEntries" or "entries" in instr:
            for entry in instr.get("entries", []):
                eid = entry.get("entryId", "")
                content = entry.get("content") or {}
                if eid.startswith("cursor-bottom") or content.get("cursorType") == "Bottom":
                    val = content.get("value") or (content.get("itemContent") or {}).get("value")
                    if val:
                        yield "cursor", val
                elif eid.startswith("tweet-") or content.get("entryType") == "TimelineTimelineItem":
                    res = (((content.get("itemContent") or {}).get("tweet_results")) or {}).get("result")
                    tw = _unwrap_tweet(res)
                    if tw:
                        yield "tweet", tw
                elif content.get("entryType") == "TimelineTimelineModule":
                    for item in content.get("items", []) or []:
                        res = ((((item.get("item") or {}).get("itemContent") or {}).get("tweet_results")) or {}).get("result")
                        tw = _unwrap_tweet(res)
                        if tw:
                            yield "tweet", tw


def scrape_creator(handle: str, limit: int):
    user = get_user(handle)
    if not user:
        log.warning("could not resolve handle (suspended/typo/protected)", extra={"handle": handle})
        return None, None
    followers = user.get("followers")
    log.info("resolved", extra={"handle": handle, "followers": followers, "user_id": user["rest_id"]})
    records, cursor, page, seen = [], None, 0, set()
    while len(records) < limit:
        page += 1
        variables = {
            "userId": user["rest_id"], "count": PAGE_SIZE,
            "includePromotedContent": False, "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": True, "withV2Timeline": True,
        }
        if cursor:
            variables["cursor"] = cursor
        j = _gql(QID_TWEETS, "UserTweets", variables)
        new, next_cursor = 0, None
        for kind, val in _walk_timeline(j):
            if kind == "cursor":
                next_cursor = val
            else:
                rec = _tweet_to_record(val)
                if rec and rec["id"] not in seen:
                    seen.add(rec["id"])
                    records.append(rec)
                    new += 1
        log.info("page", extra={"handle": handle, "page": page, "added": new, "total": len(records)})
        if new == 0 or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(random.uniform(*PAGE_DELAY))
    return followers, records[:limit]


# ── input parsing ──────────────────────────────────────────────────────────────
def norm_handle(s: str):
    s = str(s).strip()
    m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)", s)
    if m:
        return m.group(1)
    s = s.lstrip("@").strip("/")
    return s if re.fullmatch(r"[A-Za-z0-9_]{1,15}", s) else None


def read_pages(path: Path):
    handles = []
    # A fresh install has no pages.txt yet (only pages.txt.example ships). Treat a missing
    # file as "no handles" so main() reaches its clear "no handles given" guidance instead
    # of dying on an uncaught FileNotFoundError traceback. instagram/scrape.py has guarded
    # this for a while; x and youtube were left behind.
    if not path.exists():
        return handles
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h = norm_handle(line)
        if h and h not in handles:
            handles.append(h)
    return handles


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    setup_logging("scrape", platform="x")
    # Own SIGTERM before anything is written: the default disposition kills the process
    # mid-write, and posts_raw.json is this platform's corpus. See core/stopflag.py.
    install_stop_handler()
    ap = argparse.ArgumentParser(description="X/Twitter virality scraper (logged-in session)")
    ap.add_argument("handles", nargs="*", help="handles or profile URLs")
    ap.add_argument("--file", help="input file (one handle/URL per line)")
    ap.add_argument("--limit", type=int, default=None, help="max posts per handle")
    args = ap.parse_args()

    if args.limit is None:
        cfg = _load_json(HERE / "niche_config.json")
        args.limit = int(cfg.get("posts_per_creator") or 100)

    creators = []
    for h in args.handles:
        nh = norm_handle(h)
        if nh and nh not in creators:
            creators.append(nh)
    if args.file:
        for h in read_pages(Path(args.file)):
            if h not in creators:
                creators.append(h)
    if not creators and (HERE / "pages.txt").exists():
        creators = read_pages(HERE / "pages.txt")
    if not creators:
        log.error("no handles given — pass handles, --file, or fill pages.txt")
        sys.exit(1)

    if not load_session():
        log.error("no X session — set X_AUTH_TOKEN + X_CT0 (or platforms/x/session.txt). "
                  "Use a BURNER account; see this file's header.")
        sys.exit(1)
    log.warning("using a REAL logged-in X session — burner account only; paced + circuit-broken",
                extra={"handles": len(creators), "limit": args.limit})

    posts_path = HERE / "posts_raw.json"
    meta_path = HERE / "profiles_meta.json"
    posts_all = _load_json(posts_path)
    meta_all = _load_json(meta_path)
    todo = [c for c in creators if c not in posts_all]
    log.info("plan", extra={"assigned": len(creators), "already_saved": len(creators) - len(todo), "scraping": len(todo)})

    stopped = False
    by_request = False
    for n, c in enumerate(todo, 1):
        # Stopping is free here and nowhere else: both files are rewritten wholesale after
        # every handle, so everything up to this point is already durable on disk.
        if stop_requested():
            log.warning("stop requested — ending after the last saved handle",
                        extra={"scraped_this_run": n - 1, "remaining": len(todo) - n + 1})
            stopped = by_request = True
            break
        log.info("creator start", extra={"i": n, "of": len(todo), "handle": c})
        try:
            followers, recs = scrape_creator(c, args.limit)
        except RateLimited as e:
            log.error("CIRCUIT BREAKER — saving partial progress and exiting", extra={"reason": str(e)})
            stopped = True
            break
        if recs is None:
            continue
        posts_all[c] = recs
        if followers is not None:
            meta_all[c] = {"followers": followers}
        # META BEFORE CORPUS: resume skips handles already present in posts_raw.json, so
        # dying between these two writes with the corpus first would leave the handle in
        # the corpus and out of profiles_meta.json — never re-fetched, no follower count,
        # and core/virality.py divides engagement_rate and reach_multiplier by followers,
        # so both signals would be permanently null for that handle. Written this way
        # round, the worst case is a meta entry the next run simply overwrites.
        write_text_atomic(meta_path, json.dumps(meta_all, ensure_ascii=False, indent=1))
        write_text_atomic(posts_path, json.dumps(posts_all, ensure_ascii=False, indent=1))
        log.info("creator done", extra={"handle": c, "posts": len(recs)})
        if n < len(todo):
            # Interruptible: PEP 475 resumes a bare sleep after the handler returns, so the
            # flag alone would leave a Stop press looking ignored for the whole delay.
            sleep_unless_stopped(random.uniform(*CREATOR_DELAY))

    total = sum(len(v) for v in posts_all.values())
    # A stop is a normal outcome, not a failure: this still exits 0, and the hub tells a
    # stop from a crash by its own marker rather than by the return code.
    status = ("STOPPED (by request)" if by_request
              else "STOPPED EARLY (rate limit)" if stopped else "DONE")
    log.info("%s — %d posts across %d handles -> %s", status, total, len(posts_all), posts_path.name,
             extra={"status": status, "posts": total, "handles": len(posts_all)})
    log.info("next: run `python run.py analyze`")


if __name__ == "__main__":
    main()
