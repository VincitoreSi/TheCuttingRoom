#!/usr/bin/env python3
"""engine/ig.py — the Instagram surface engine: guest bootstrap, burner-opt-in surfaces,
hydration, reel sampling, and the 3-strike circuit breaker (AutoSearch/PIPELINE.md §1
SAFETY — read this file next to CLAUDE.md §SAFETY before touching anything here).

Reimplements (never imports) ReelScraper's `platforms/instagram/find_profiles.py` and
`scrape.py` engines: same Chrome UA + `X-IG-App-ID` header shape, same guest-only
`web_profile_info` hydration + HTML regex fallback, same `clips/user` reel sampling, same
burner `session.txt` loader, same 3-strike `RateLimited` breaker + backoff. Every pacing
constant here is STRICTLY >= the scraper's equivalent (§1.3) — never lower them.

SAFETY invariants enforced in code, not just comments:
  * `GuestSession.bootstrap()` asserts `"sessionid" not in` the cookie jar it collects.
  * Burner (`load_burner_session`) is the ONLY way a `sessionid` enters this process, and
    ONLY via `IG_SESSIONID`/`IG_CSRFTOKEN` env or a gitignored `session.txt` — never hardcoded.
  * `topsearch()` / `discover_chaining()` (login-gated) REQUIRE an explicit burner dict —
    there is no guest-mode code path into them.
  * Only public fields are ever extracted from a hydrated profile (`_PUBLIC_FIELDS`) — no
    email/phone/viewer-id/cookie is ever read out of an IG response.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from engine.limits import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_JITTER_MAX_SECONDS,
    BACKOFF_MAX_ATTEMPTS,
    CHROME_UA,
    EXPAND_DELAY,
    HYDRATE_DELAY,
    IG_APP_ID,
    MAX_429_IN_A_ROW,
    SEARCH_DELAY,
    SESSION_REFRESH_EVERY,
    STALE_SESSION_RETRY_SLEEP_SECONDS,
    SURFACE_DELAY,
)

log = logging.getLogger("as.ig")

# Re-exported under their historical names — every magic number itself lives in
# engine/limits.py (the ONE place; see that module's docstring). §1.3 pacing FLOORS are
# strictly slower than ReelScraper's scraper; `pacing_seconds` config may only raise them,
# never lower them; no flag may either (§1.0 precedence).
UA = CHROME_UA
APP_ID = IG_APP_ID


class RateLimited(Exception):
    """Raised after 3 consecutive HTTP 429s — the breaker for IG surfaces (§1.4)."""


# ---- transport hook: real urlopen by default, swappable for offline tests/CI -----------
_TRANSPORT = urllib.request.urlopen


def install_fake_transport(fn) -> None:
    """Swap the low-level opener — used ONLY by tests/CI so `smoke`/unit tests never touch
    the network. `fn(request, timeout=...)` must behave like `urllib.request.urlopen`."""
    global _TRANSPORT
    _TRANSPORT = fn


def reset_transport() -> None:
    global _TRANSPORT
    _TRANSPORT = urllib.request.urlopen


def _http(url: str, method: str = "GET", data=None, headers: dict | None = None,
         timeout: int = 30) -> tuple[int, dict, str]:
    """Low-level request. Returns (status, headers_dict, body_text). `headers_dict` carries
    an extra `_set_cookies` key: the raw list of `Set-Cookie` header values (never merged
    into a single string, so guest bootstrap can enumerate every cookie IG sets)."""
    hdrs = {
        "User-Agent": UA, "X-IG-App-ID": APP_ID, "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    hdrs.update(headers or {})
    body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with _TRANSPORT(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        msg = getattr(resp, "headers", None)
        if msg is not None and hasattr(msg, "get_all"):
            set_cookies = msg.get_all("Set-Cookie") or []
            header_dict = dict(msg.items())
        elif isinstance(msg, dict):
            raw = msg.get("Set-Cookie") or msg.get("set-cookie")
            set_cookies = [raw] if raw else []
            header_dict = dict(msg)
        else:
            set_cookies, header_dict = [], {}
        header_dict["_set_cookies"] = set_cookies
        body_text = resp.read().decode("utf-8", "replace")
    return status, header_dict, body_text


def _parse_set_cookie(headers: dict) -> dict:
    """name=value pairs from every Set-Cookie header (guest-safe: caller must still assert
    'sessionid' not in the result — a guest bootstrap should never see one, but we don't
    silently drop it here so the assertion can actually fire)."""
    jar = {}
    for raw in headers.get("_set_cookies") or []:
        if not raw:
            continue
        nv = raw.split(";", 1)[0].strip()
        if "=" in nv:
            k, v = nv.split("=", 1)
            jar[k.strip()] = v.strip()
    return jar


class GuestSession:
    """Guest-ONLY cookie/csrf state. NEVER carries a sessionid — asserted on every
    bootstrap. Force-refreshes every `SESSION_REFRESH_EVERY` requests (§1.3)."""

    def __init__(self):
        self.cookie: dict[str, str] = {}
        self.csrf: str = ""
        self._req_count = 0

    def bootstrap(self) -> bool:
        _status, headers, _body = _http(
            "https://www.instagram.com/", headers={"Referer": "https://www.instagram.com/"}
        )
        jar = _parse_set_cookie(headers)
        assert "sessionid" not in jar, "guest session unexpectedly carries a sessionid!"
        self.cookie = jar
        self.csrf = jar.get("csrftoken", "")
        self._req_count = 0
        log.info("guest session bootstrapped (no sessionid attached)",
                extra={"csrf_present": bool(self.csrf)})
        return bool(self.csrf) or bool(self.cookie)

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookie.items())

    def note_request(self) -> None:
        self._req_count += 1
        if self._req_count % SESSION_REFRESH_EVERY == 0:
            log.info("guest session force-refresh (25-request floor, §1.3)")
            self.bootstrap()


# ---- burner session loader (§1.1 — the ONLY permitted channels) ------------------------
def load_burner_session(path: str | Path = "session.txt") -> dict | None:
    """Burner sessionid, from `.env`/env `IG_SESSIONID` (+ optional `IG_CSRFTOKEN`) first,
    else a gitignored `session.txt` (ReelScraper `load_session` format). Returns None
    (never an error) if neither is present — absence of a burner is normal, not a fault."""
    env_sid = os.environ.get("IG_SESSIONID")
    if env_sid:
        jar = {"sessionid": env_sid}
        csrf = os.environ.get("IG_CSRFTOKEN", "")
        if csrf:
            jar["csrftoken"] = csrf
        log.info("burner session: present (from env)")
        return {"cookie": "; ".join(f"{k}={v}" for k, v in jar.items()), "csrf": csrf}

    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    jar: dict[str, str] = {}
    if raw.startswith("{"):
        try:
            jar = {k: str(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, AttributeError):
            jar = {}
    else:
        for part in re.split(r"[;\n]+", raw):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
    if "sessionid" not in jar:
        log.warning("session.txt has no sessionid — burner disabled (guest-only)")
        return None
    log.info("burner session: present (from session.txt)")
    return {"cookie": "; ".join(f"{k}={v}" for k, v in jar.items()), "csrf": jar.get("csrftoken", "")}


# ---- guarded request: 3-strike breaker + backoff + session refresh (§1.4) --------------
_consec_429 = 0


def _guarded(url: str, method: str = "GET", data=None, referer: str | None = None,
            session: GuestSession | None = None, burner: dict | None = None,
            timeout: int = 30) -> str:
    """§1.4: RateLimited after 3 consecutive 429s (backoff `15*(attempt+1)+jitter`, <=4
    attempts, refresh guest session per retry). 401/403 = stale session -> refresh + retry
    once (NOT a breaker strike); on a login-gated 401/403 with a burner, the caller should
    drop to guest-only for the rest of the run (§1.4)."""
    global _consec_429
    cookie = burner["cookie"] if burner else (session.cookie_header() if session else "")
    csrf = burner["csrf"] if burner else (session.csrf if session else "")
    headers = {"Cookie": cookie, "X-CSRFToken": csrf}
    if referer:
        headers["Referer"] = referer

    for attempt in range(BACKOFF_MAX_ATTEMPTS):
        try:
            _status, _h, body = _http(url, method=method, data=data, headers=headers, timeout=timeout)
            _consec_429 = 0
            if session:
                session.note_request()
            return body
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _consec_429 += 1
                if _consec_429 >= MAX_429_IN_A_ROW:
                    raise RateLimited(
                        f"{_consec_429} consecutive 429s — stopping to protect the account"
                    ) from e
                wait_s = BACKOFF_BASE_SECONDS * (attempt + 1) + random.uniform(0, BACKOFF_JITTER_MAX_SECONDS)
                log.warning("429 — backoff + session refresh",
                            extra={"wait_s": round(wait_s), "consec_429": _consec_429})
                time.sleep(wait_s)
                if session:
                    session.bootstrap()
                    cookie, csrf = session.cookie_header(), session.csrf
                    headers.update({"Cookie": cookie, "X-CSRFToken": csrf})
                continue
            if e.code in (401, 403):
                log.info("stale session (401/403) — refreshing, NOT a breaker strike",
                          extra={"code": e.code})
                if session:
                    session.bootstrap()
                    cookie, csrf = session.cookie_header(), session.csrf
                    headers.update({"Cookie": cookie, "X-CSRFToken": csrf})
                    time.sleep(STALE_SESSION_RETRY_SLEEP_SECONDS)
                    continue
                raise
            raise
    raise RuntimeError("request failed after retries")


# ---- public-field extraction (data hygiene, §1.7 — public metadata ONLY) --------------
def _extract_public_fields(u: dict, fallback_username: str) -> dict:
    return {
        "username": u.get("username") or fallback_username,
        "user_id": u.get("id"),
        "full_name": u.get("full_name", ""),
        "biography": u.get("biography", ""),
        "category": u.get("category_name") or u.get("category") or "",
        "followers": (u.get("edge_followed_by") or {}).get("count"),
        "following": (u.get("edge_follow") or {}).get("count"),
        "posts": (u.get("edge_owner_to_timeline_media") or {}).get("count"),
        "is_verified": bool(u.get("is_verified")),
        "is_private": bool(u.get("is_private")),
        "is_business": bool(u.get("is_business_account")),
        "external_url": u.get("external_url", ""),
    }


def html_profile_fallback(username: str, session: GuestSession | None = None) -> dict | None:
    """Guest profile-HTML fallback (§1.2 ALLOWED) when the JSON endpoint is unavailable —
    regex-extract just id + follower count, mirroring ReelScraper's `_page_scrape_profile`."""
    try:
        body = _guarded(f"https://www.instagram.com/{username}/",
                        referer="https://www.instagram.com/", session=session)
    except (RateLimited, urllib.error.HTTPError):
        raise
    except Exception as e:
        log.debug("html fallback failed", extra={"username": username, "err": str(e)})
        return None
    m = re.search(r'"profile_id":"(\d+)"', body) or re.search(r'"id":"(\d+)","is_private"', body)
    uid = m.group(1) if m else None
    if not uid:
        return None
    fm = (re.search(r'"edge_followed_by":\{"count":(\d+)\}', body)
          or re.search(r'"follower_count":(\d+)', body))
    followers = int(fm.group(1)) if fm else None
    return {
        "username": username, "user_id": uid, "followers": followers, "full_name": "",
        "biography": "", "category": "", "following": None, "posts": None,
        "is_verified": None, "is_private": None, "is_business": None, "external_url": "",
    }


def web_profile_info(username: str, session: GuestSession | None = None,
                     burner: dict | None = None) -> dict | None:
    """Guest hydration (§1.2 ALLOWED `web_profile_info`) with the HTML-fallback path.
    `burner` is accepted only so a caller in `guest_only=false` mode can retry via the
    burner if guest hydration is blocked — it is never required."""
    url = "https://www.instagram.com/api/v1/users/web_profile_info/?" + urllib.parse.urlencode(
        {"username": username})
    ref = f"https://www.instagram.com/{username}/"
    try:
        body = _guarded(url, referer=ref, session=session)
    except RateLimited:
        raise
    except Exception as e:
        log.info("guest hydration failed; trying burner then HTML fallback",
                 extra={"username": username, "err": str(e)})
        if burner:
            try:
                body = _guarded(url, referer=ref, burner=burner)
            except Exception:
                return html_profile_fallback(username, session=session)
        else:
            return html_profile_fallback(username, session=session)
    u = (json.loads(body).get("data") or {}).get("user") or {}
    if not u:
        return html_profile_fallback(username, session=session)
    return _extract_public_fields(u, username)


def clips_user(user_id: str, username: str, max_id: str | None = None,
              session: GuestSession | None = None, page_size: int = 12) -> dict:
    """Read-only reel sample (§1.2 ALLOWED `POST /api/v1/clips/user/`)."""
    params = {"target_user_id": user_id, "page_size": str(page_size), "include_feed_video": "true"}
    if max_id:
        params["max_id"] = max_id
    body_data = urllib.parse.urlencode(params)
    ref = f"https://www.instagram.com/{username}/reels/"
    raw = _guarded("https://www.instagram.com/api/v1/clips/user/", method="POST",
                   data=body_data, referer=ref, session=session)
    return json.loads(raw)


def sample_reels(user_id: str, username: str, session: GuestSession | None = None,
                 limit: int = 12) -> tuple[list[str], float | None]:
    """One page of public reel URLs + the median play count (used for `min_median_plays`
    gating and the `median_plays` candidate field) — never stores captions/likes/comments,
    only the URL + play count (§1.7 data hygiene)."""
    j = clips_user(user_id, username, session=session, page_size=limit)
    items = [x.get("media", x) for x in (j.get("items") or [])][:limit]
    urls: list[str] = []
    plays: list[float] = []
    for m in items:
        code = m.get("code")
        if code:
            urls.append(f"https://www.instagram.com/reel/{code}/")
        pc = m.get("play_count") or m.get("view_count")
        if pc:
            plays.append(float(pc))
    plays.sort()
    median = plays[len(plays) // 2] if plays else None
    return urls, median


# ---- burner-only opt-in surfaces (§1.2 ALLOWED, ONLY with a burner session) -----------
def topsearch(query: str, burner: dict, per_query: int = 20) -> list[dict]:
    """Login-gated keyword search. Requires an explicit burner dict — no guest path."""
    if not burner:
        raise ValueError("topsearch requires a burner session (login-gated)")
    url = "https://www.instagram.com/web/search/topsearch/?" + urllib.parse.urlencode(
        {"context": "blended", "query": query, "include_reel": "false"})
    body = _guarded(url, referer="https://www.instagram.com/", burner=burner)
    j = json.loads(body)
    out = []
    for e in (j.get("users") or [])[:per_query]:
        u = e.get("user") or {}
        if u.get("username"):
            out.append({"username": u["username"], "user_id": u.get("pk"),
                       "full_name": u.get("full_name", "")})
    return out


def discover_chaining(user_id: str, username: str, burner: dict, per_seed: int = 30) -> list[dict]:
    """Login-gated related-creator chaining. Requires an explicit burner dict — no guest path."""
    if not burner:
        raise ValueError("discover_chaining requires a burner session (login-gated)")
    url = f"https://www.instagram.com/api/v1/discover/chaining/?target_id={user_id}"
    body = _guarded(url, referer=f"https://www.instagram.com/{username}/", burner=burner)
    j = json.loads(body)
    out = []
    for u in (j.get("users") or [])[:per_seed]:
        if u.get("username"):
            out.append({"username": u["username"], "user_id": u.get("pk"),
                       "full_name": u.get("full_name", "")})
    return out
