#!/usr/bin/env python3
# SUPERSEDED by the AutoSearch agent (/AutoSearch) for hub-gated, ongoing discovery — same
# guest-first/burner-opt-in engines, now Claude-scored and human-approved via
# POST /api/discovery/{p} + the Dashboard. This script remains for manual/offline use.
"""
find_profiles.py — discover Instagram profiles by niche (SAFE guest mode + optional burner expand)

Finds candidate profiles for a niche (default: Kannada + Finance) via two engines:
  * Engine A — keyword search  (GUEST mode, NO cookie/sessionid — fully safe)
  * Engine B — seed-expand     (related profiles; uses a BURNER sessionid ONLY here)
Then hydrates each unique candidate (bio, followers, category) and scores relevance.

Output: Kannada_Finance_Profiles.xlsx (sorted strong->none) + profiles_raw.json (resume/backup).
The output handles feed straight into:  python scrape_reels.py --file Kannada_Finance_Profiles.xlsx

SAFETY:
  * Keyword search + profile hydration run in GUEST mode (no sessionid). A hard
    assertion guarantees guest calls never carry a sessionid.
  * The burner sessionid (read from session.txt) is attached ONLY to Engine B's
    related-profiles calls. If session.txt is missing, Engine B is skipped.
  * Randomized human-like pacing + a 3-consecutive-429 circuit breaker.
  * Resume — usernames already in profiles_raw.json are not re-hydrated.

USAGE (run from inside this folder):
  python find_profiles.py                       # keyword search with built-in Kannada-finance terms
  python find_profiles.py --per-query 30        # fewer results per keyword (faster/gentler)
  python find_profiles.py --max-candidates 40   # cap total profiles hydrated (good for test runs)
  python find_profiles.py --no-expand           # keyword search only, skip Engine B
  python find_profiles.py --keywords my_terms.txt --seeds seeds.txt

OPTIONS:
  --keywords FILE       one search term per line (default: built-in list, or keywords.txt if present)
  --seeds FILE          seed handles for Engine B (default: seeds.txt if present)
  --session FILE        burner cookie file for Engine B (default: session.txt if present)
  --out NAME            output xlsx (default Kannada_Finance_Profiles.xlsx)
  --per-query N         max users collected per keyword (default 50)
  --max-candidates N    cap total profiles hydrated (0 = no cap)
  --no-expand           skip Engine B (seed-expand) entirely

Requires: pip install openpyxl
"""
import sys, json, time, re, random, argparse
from http.cookiejar import CookieJar
from pathlib import Path
import urllib.request, urllib.parse, urllib.error
import openpyxl

# Windows consoles default to cp1252 and crash printing Kannada script — force utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent

# ---- constants ----
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
APP = "936619743392459"          # public web app id (constant)
QUERY_DELAY = (3.0, 6.0)         # between keyword searches
HYDRATE_DELAY = (2.0, 4.0)       # between profile hydrations
EXPAND_DELAY = (5.0, 10.0)       # between Engine B related-profile calls (burner session)
MAX_429_IN_A_ROW = 3             # circuit breaker

# ---- niche signal vocab ----
FINANCE_KW = [
    "finance", "financial", "stock", "stocks", "share market", "sharemarket", "shares",
    "invest", "investing", "investor", "investment", "mutual fund", "mutualfund",
    "trading", "trader", "money", "wealth", "nifty", "sensex", "sip", "ipo",
    "crypto", "budget", "savings", "tax", "banking", "market", "equity", "demat",
    "personal finance", "fintech", "ಹಣಕಾಸು", "ಷೇರು", "ಹೂಡಿಕೆ", "ಮಾರುಕಟ್ಟೆ",
    "ಹಣ", "ಉಳಿತಾಯ", "ಆದಾಯ", "ಹೂಡಿಕೆದಾರ",
]
KANNADA_KW = [
    "kannada", "karnataka", "bengaluru", "bangalore", "mysuru", "mysore", "namma",
    "ಕನ್ನಡ", "ಕರ್ನಾಟಕ", "ಬೆಂಗಳೂರು",
]
KANNADA_SCRIPT = re.compile(r"[ಀ-೿]")  # Kannada Unicode block

DEFAULT_KEYWORDS = [
    "kannada finance", "kannada stock market", "kannada share market",
    "kannada money", "kannada investing", "karnataka finance",
    "kannada business", "kannada mutual fund", "kannada trading",
    "ಹಣಕಾಸು", "ಷೇರು ಮಾರುಕಟ್ಟೆ", "ಹೂಡಿಕೆ",
]

# ---- guest session state (NO login) ----
_cookie = ""; _csrf = ""

def new_guest_session():
    """Fetch a fresh GUEST csrftoken (NO login, NO sessionid)."""
    global _cookie, _csrf
    cj = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    op.open("https://www.instagram.com/", timeout=30).read()
    jar = {c.name: c.value for c in cj}
    assert "sessionid" not in jar, "guest session unexpectedly logged in!"
    _csrf = jar.get("csrftoken", "")
    _cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    return bool(_csrf)

class RateLimited(Exception):
    pass

_consec_429 = 0

def _http(url, method="GET", data=None, referer=None, cookie=None, csrf=None, timeout=30):
    """Low-level request. Defaults to the GUEST cookie; Engine B passes a burner cookie explicitly."""
    use_cookie = cookie if cookie is not None else _cookie
    use_csrf = csrf if csrf is not None else _csrf
    headers = {
        "User-Agent": UA, "X-IG-App-ID": APP, "Sec-Fetch-Site": "same-origin",
        "Cookie": use_cookie, "X-CSRFToken": use_csrf,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if referer:
        headers["Referer"] = referer
    body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def _guarded_get(url, referer=None, cookie=None, csrf=None):
    """GET with 429 circuit breaker + guest-session refresh on 401/403 (guest calls only)."""
    global _consec_429
    is_guest = cookie is None
    for attempt in range(4):
        try:
            _, t = _http(url, referer=referer, cookie=cookie, csrf=csrf)
            _consec_429 = 0
            return t
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _consec_429 += 1
                if _consec_429 >= MAX_429_IN_A_ROW:
                    raise RateLimited(f"{_consec_429} consecutive 429s — stopping to protect the IP")
                w = 15 * (attempt + 1) + random.uniform(0, 5)
                print(f"    429 -> backoff {w:.0f}s", flush=True)
                time.sleep(w)
                if is_guest:
                    try: new_guest_session()
                    except Exception: pass
                continue
            if e.code in (401, 403) and is_guest:
                print(f"    {e.code} -> refreshing guest session", flush=True)
                try: new_guest_session()
                except Exception: pass
                time.sleep(2); continue
            raise
    raise RuntimeError("request failed after retries")

# ---- Engine A: keyword search (BURNER — IG now gates search behind login) ----
def keyword_search(query, per_query, session):
    """topsearch -> list of {username, full_name, is_verified, is_private, pk}. Requires burner session."""
    url = "https://www.instagram.com/web/search/topsearch/?" + urllib.parse.urlencode(
        {"context": "blended", "query": query, "include_reel": "false"})
    try:
        t = _guarded_get(url, referer="https://www.instagram.com/",
                         cookie=session["cookie"], csrf=session["csrf"])
        j = json.loads(t)
    except RateLimited:
        raise
    except Exception as e:
        print(f"    ! search failed for '{query}': {e}", flush=True)
        return []
    out = []
    for e in (j.get("users") or [])[:per_query]:
        u = e.get("user") or {}
        if u.get("username"):
            out.append({
                "username": u["username"], "full_name": u.get("full_name", ""),
                "is_verified": u.get("is_verified"), "is_private": u.get("is_private"),
                "pk": u.get("pk"),
            })
    return out

# ---- Engine B: seed-expand (BURNER sessionid ONLY) ----
def load_session(path):
    """Read burner sessionid/csrftoken from a file. Accepts 'name=value; name=value' or JSON."""
    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    jar = {}
    if raw.startswith("{"):
        try: jar = {k: str(v) for k, v in json.loads(raw).items()}
        except Exception: jar = {}
    else:
        for part in re.split(r"[;\n]+", raw):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
    if "sessionid" not in jar:
        print("  ! session file has no sessionid — Engine B disabled", flush=True)
        return None
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    return {"cookie": cookie, "csrf": jar.get("csrftoken", "")}

def resolve_id(username):
    try:
        t = _guarded_get(f"https://www.instagram.com/{username}/", referer="https://www.instagram.com/")
    except Exception:
        return None
    m = re.search(r'"profile_id":"(\d+)"', t) or re.search(r'"id":"(\d+)","is_private"', t)
    return m.group(1) if m else None

def expand_seed(username, session, per_seed=60):
    """Related/suggested profiles for a seed — uses the BURNER sessionid."""
    uid = resolve_id(username)
    if not uid:
        print(f"    ! could not resolve seed @{username}", flush=True)
        return []
    url = f"https://www.instagram.com/api/v1/discover/chaining/?target_id={uid}"
    try:
        t = _guarded_get(url, referer=f"https://www.instagram.com/{username}/",
                         cookie=session["cookie"], csrf=session["csrf"])
        j = json.loads(t)
    except Exception as e:
        print(f"    ! expand failed for @{username}: {e}", flush=True)
        return []
    out = []
    for u in (j.get("users") or [])[:per_seed]:
        if u.get("username"):
            out.append({
                "username": u["username"], "full_name": u.get("full_name", ""),
                "is_verified": u.get("is_verified"), "is_private": u.get("is_private"),
                "pk": u.get("pk"),
            })
    return out

# ---- hydration (GUEST first, BURNER fallback) ----
def _hydrate_once(username, cookie=None, csrf=None):
    url = "https://www.instagram.com/api/v1/users/web_profile_info/?" + urllib.parse.urlencode({"username": username})
    t = _guarded_get(url, referer=f"https://www.instagram.com/{username}/", cookie=cookie, csrf=csrf)
    return (json.loads(t).get("data") or {}).get("user") or {}

_guest_blocked = False  # once guest hydration rate-limits, stop retrying guest this run

def hydrate(username, session=None):
    """Try guest hydration first; fall back to the burner session only if guest fails.
    After guest mode rate-limits once, skip guest for the rest of the run (avoids backoff storms)."""
    global _guest_blocked, _consec_429
    u = {}
    if not (_guest_blocked and session):
        try:
            u = _hydrate_once(username)
        except RateLimited:
            if not session:
                raise
            _guest_blocked = True; _consec_429 = 0
            print("    (guest hydration rate-limited — using burner for the rest of this run)", flush=True)
        except Exception as e:
            if not session:
                print(f"    ! hydrate failed for @{username}: {e}", flush=True)
                return None
    if not u and session:
        try:
            u = _hydrate_once(username, cookie=session["cookie"], csrf=session["csrf"])
        except Exception as e:
            print(f"    ! hydrate failed for @{username} (guest+burner): {e}", flush=True)
            return None
    if not u:
        return None
    return {
        "username": u.get("username") or username,
        "full_name": u.get("full_name", ""),
        "biography": u.get("biography", ""),
        "category": u.get("category_name") or u.get("category") or "",
        "followers": (u.get("edge_followed_by") or {}).get("count"),
        "following": (u.get("edge_follow") or {}).get("count"),
        "posts": (u.get("edge_owner_to_timeline_media") or {}).get("count"),
        "is_verified": u.get("is_verified"),
        "is_private": u.get("is_private"),
        "is_business": u.get("is_business_account"),
        "external_url": u.get("external_url", ""),
        "user_id": u.get("id"),
    }

# ---- scoring ----
def score(profile):
    hay = " ".join(str(profile.get(k) or "") for k in ("username", "full_name", "biography", "category")).lower()
    fin = sorted({kw for kw in FINANCE_KW if kw.lower() in hay})
    kan = sorted({kw for kw in KANNADA_KW if kw.lower() in hay})
    has_script = bool(KANNADA_SCRIPT.search(hay))
    # Contextual Kannada signal: account was surfaced by Kannada-finance seeds' "Suggested for you".
    # Treat that as a (weaker) Kannada signal so English-bio Kannada creators aren't undercounted.
    src = profile.get("source") or []
    via = profile.get("via") or []
    rec_ctx = any("seed-expand" in s for s in src) or any("suggested" in v for v in via)
    fin_score = len(fin)
    kan_score = len(kan) + (1 if has_script else 0) + (1 if rec_ctx else 0)
    if fin_score and kan_score:
        rel = "strong"
    elif fin_score or kan_score:
        rel = "weak"
    else:
        rel = "none"
    matched = fin + kan + (["<kannada-script>"] if has_script else []) + (["<recommended-by-kannada-finance>"] if rec_ctx else [])
    return fin_score, kan_score, rel, ", ".join(matched)

# ---- Creator archetype classifier (GREEN micro-investing-educator vs RED) ----
GREEN_KW = ["sip", "mutual fund", "mutualfund", "index fund", "etf", "digital gold", "gold etf",
            "silver etf", "fixed deposit", "liquid fund", "personal finance", "financial literacy",
            "financial freedom", "financial education", "start investing", "investing for beginner",
            "beginner", "compounding", "save money", "savings", "wealth", "swp", "financial planning",
            "money management", "amfi", "invest", "investor", "investment", "long term", "ಹೂಡಿಕೆ",
            "ಉಳಿತಾಯ", "ಹಣಕಾಸು", "bachat", "demat", "financial advisor", "financial planner"]
RED_TRADER = ["trader", "trading", "intraday", "forex", "xauusd", "scalp", "f&o", "f & o",
              "options", "option buying", "swing trad", "price action", "smc", "prop firm",
              "multibagger", "stock tip", "buy/sell", "nifty", "banknifty", "bank nifty",
              "chart", "candlestick", "signal", "crypto", "btc", "delta exchange", "fno"]
RED_BIZ = ["business idea", "startup", "brand", "entrepreneur", "marketing", "case stud",
           "company history", "billionaire", "shop", "wholesale"]
RED_MINDSET = ["motivation", "mindset", "self improvement", "self-improvement", "life advice",
               "podcast", "discipline", "success habit"]
RED_NEWS = ["news", "breaking", "live updates", "journalist", "current affairs", "geopolitics",
            "media platform"]

def classify_archetype(profile):
    """Heuristic GREEN/RED archetype from bio/name/category (confidence: low — no reels seen)."""
    hay = " ".join(str(profile.get(k) or "") for k in ("username", "full_name", "biography", "category")).lower()
    def hits(kws): return sorted({k for k in kws if k in hay})
    g = hits(GREEN_KW); tr = hits(RED_TRADER); bz = hits(RED_BIZ); mn = hits(RED_MINDSET); nw = hits(RED_NEWS)
    # trader signals dominate when there are strong/multiple ones and few pure-green ones
    educator = len(g)
    trader = len(tr)
    if trader >= 2 and trader >= educator:
        arch, dec = "stock-tipper", "skip"
    elif nw and not educator:
        arch, dec = "news-macro", "skip"
    elif mn and not educator and not trader:
        arch, dec = "mindset-motivation", "skip"
    elif bz and educator == 0 and trader == 0:
        arch, dec = "business-edutainment", "skip"
    elif educator >= 1 and trader == 0:
        arch, dec = "micro-investing-educator", "scrape"
    elif educator >= 1 and trader >= 1:
        # mixed: lean on which is stronger
        if educator > trader:
            arch, dec = "micro-investing-educator", "scrape"
        else:
            arch, dec = "mixed", "maybe"
    elif educator == 0 and trader == 0 and not (bz or mn or nw):
        arch, dec = "non-finance", "skip"
    else:
        arch, dec = "mixed", "maybe"
    sig = []
    if g: sig.append("green:" + "/".join(g[:4]))
    if tr: sig.append("trader:" + "/".join(tr[:3]))
    if bz: sig.append("biz:" + "/".join(bz[:2]))
    if mn: sig.append("mindset:" + "/".join(mn[:2]))
    if nw: sig.append("news:" + "/".join(nw[:2]))
    return arch, dec, "low", "; ".join(sig)

# ---- input helpers ----
def read_lines(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out

def norm_handle(s):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", str(s))
    if m: return m.group(1)
    s = str(s).strip().lstrip("@").strip("/")
    return s if re.fullmatch(r"[A-Za-z0-9_.]{1,40}", s) else None

def build_rows(raw_all, cand=None):
    """Score every cached profile into output rows. cand (optional) supplies source/via;
    otherwise they're read from each record (set by browser-driven hydration)."""
    cand = cand or {}
    rows = []
    for u, p in raw_all.items():
        fin, kan, rel, matched = score(p)
        arch, dec, conf, sig = classify_archetype(p)
        src = cand.get(u, {}).get("source") or set(p.get("source") or [])
        via = cand.get(u, {}).get("via") or set(p.get("via") or [])
        rows.append({
            **p,
            "profile_url": f"https://www.instagram.com/{u}/",
            "finance_score": fin, "kannada_score": kan, "relevance": rel,
            "matched_keywords": matched,
            "archetype": arch, "scrape_decision": dec,
            "archetype_confidence": conf, "archetype_signals": sig,
            "source": "+".join(sorted(src)) if src else "",
            "discovered_via": " | ".join(sorted(via)) if via else "",
        })
    return rows

def save_outputs(out_xlsx, raw_json, rows, raw_all):
    raw_json.write_text(json.dumps(raw_all, ensure_ascii=False, indent=1), encoding="utf-8")
    # sort: GREEN (scrape) first, then maybe, then skip; within that by followers
    decr = {"scrape": 0, "maybe": 1, "skip": 2}
    rows = sorted(rows, key=lambda r: (decr.get(r.get("scrape_decision"), 3), -(r.get("followers") or 0)))
    cols = ["username", "full_name", "profile_url", "followers", "following", "posts",
            "category", "is_verified", "is_private", "is_business", "external_url",
            "biography", "archetype", "scrape_decision", "archetype_confidence",
            "archetype_signals", "finance_score", "kannada_score", "matched_keywords",
            "relevance", "source", "discovered_via", "user_id"]
    wb = openpyxl.Workbook()
    sh = wb.active; sh.title = "Profiles"
    sh.append(cols)
    for r in rows:
        sh.append([r.get(c) for c in cols])
    s2 = wb.create_sheet("Summary")
    s2.append(["scrape_decision", "count"])
    for d in ("scrape", "maybe", "skip"):
        s2.append([d, sum(1 for r in rows if r.get("scrape_decision") == d)])
    s2.append(["TOTAL", len(rows)])
    s2.append([])
    s2.append(["archetype", "count"])
    from collections import Counter
    for a, c in Counter(r.get("archetype") for r in rows).most_common():
        s2.append([a, c])
    wb.save(out_xlsx)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords")
    ap.add_argument("--seeds", default="seeds.txt")
    ap.add_argument("--session", default="session.txt")
    ap.add_argument("--out", default="Kannada_Finance_Profiles.xlsx")
    ap.add_argument("--per-query", type=int, default=50)
    ap.add_argument("--search-terms", type=int, default=4, help="max keyword searches (light search; 0 = all)")
    ap.add_argument("--max-candidates", type=int, default=0)
    ap.add_argument("--no-expand", action="store_true")
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--score-only", action="store_true",
                    help="skip all network; score profiles_raw.json (fed by browser hydration) -> xlsx")
    args = ap.parse_args()

    # keywords
    if args.keywords and Path(args.keywords).exists():
        keywords = read_lines(args.keywords)
    elif (HERE / "keywords.txt").exists():
        keywords = read_lines(HERE / "keywords.txt")
    else:
        keywords = DEFAULT_KEYWORDS

    out_xlsx = HERE / args.out
    raw_json = HERE / "profiles_raw.json"

    # --score-only: no network. Build the xlsx from browser-hydrated profiles_raw.json.
    if args.score_only:
        raw_all = json.loads(raw_json.read_text(encoding="utf-8")) if raw_json.exists() else {}
        rows = build_rows(raw_all)
        save_outputs(out_xlsx, raw_json, rows, raw_all)
        strong = sum(1 for r in rows if r["relevance"] == "strong")
        weak = sum(1 for r in rows if r["relevance"] == "weak")
        print(f"SCORED {len(rows)} profiles -> {out_xlsx.name}  "
              f"(strong={strong}, weak={weak}, none={len(rows)-strong-weak})", flush=True)
        return

    if not new_guest_session():
        print("Could not establish guest session", flush=True); sys.exit(1)
    print("Guest session OK (no sessionid attached).", flush=True)

    # Burner session powers BOTH search (IG gates it behind login) and seed-expand.
    session = load_session(args.session)

    # candidate {username: {source set, discovered_via set}}
    cand = {}
    def add(username, src, via):
        c = cand.setdefault(username, {"source": set(), "via": set()})
        c["source"].add(src); c["via"].add(via)

    # Engine B FIRST — seed-expand (highest signal, fewest calls)
    seeds = read_lines(HERE / args.seeds) if (HERE / args.seeds).exists() else []
    seeds = [norm_handle(s) for s in seeds]; seeds = [s for s in seeds if s]
    if args.no_expand:
        print("\n== Engine B skipped (--no-expand) ==", flush=True)
    elif not session:
        print(f"\n== Engine B skipped (no {args.session} burner cookie) ==", flush=True)
    elif not seeds:
        print(f"\n== Engine B skipped (no seeds in {args.seeds}) ==", flush=True)
    else:
        print(f"\n== Engine B: seed-expand ({len(seeds)} seeds, BURNER session) ==", flush=True)
        try:
            for s in seeds:
                users = expand_seed(s, session)
                for u in users:
                    add(u["username"], "seed-expand", f"seed:{s}")
                print(f"  @{s}: +{len(users)} (unique so far {len(cand)})", flush=True)
                time.sleep(random.uniform(*EXPAND_DELAY))
        except RateLimited as e:
            print(f"!! CIRCUIT BREAKER: {e}\nProceeding to hydrate what we have.", flush=True)

    # Engine A SECOND — light keyword search (also needs the burner session)
    search_terms = keywords if args.search_terms == 0 else keywords[:args.search_terms]
    if args.no_search:
        print("\n== Engine A skipped (--no-search) ==", flush=True)
    elif not session:
        print(f"\n== Engine A skipped (search needs {args.session} burner cookie) ==", flush=True)
    else:
        print(f"\n== Engine A: keyword search ({len(search_terms)} terms, BURNER session) ==", flush=True)
        try:
            for q in search_terms:
                users = keyword_search(q, args.per_query, session)
                for u in users:
                    add(u["username"], "keyword", q)
                print(f"  '{q}': +{len(users)} (unique so far {len(cand)})", flush=True)
                time.sleep(random.uniform(*QUERY_DELAY))
        except RateLimited as e:
            print(f"!! CIRCUIT BREAKER: {e}\nProceeding to hydrate what we have.", flush=True)

    # resume: skip usernames already hydrated
    raw_all = {}
    if raw_json.exists():
        try: raw_all = json.loads(raw_json.read_text(encoding="utf-8"))
        except Exception: raw_all = {}

    todo = [u for u in cand if u not in raw_all]
    if args.max_candidates and len(todo) > args.max_candidates:
        print(f"\n(capping hydration to {args.max_candidates} of {len(todo)} candidates)", flush=True)
        todo = todo[:args.max_candidates]

    print(f"\n== Hydrating {len(todo)} new profiles ({len(raw_all)} already cached) ==", flush=True)
    rows = []
    try:
        for i, u in enumerate(todo, 1):
            p = hydrate(u, session)
            if p:
                raw_all[u] = {**p, "source": sorted(cand[u]["source"]), "via": sorted(cand[u]["via"])}
                print(f"  [{i}/{len(todo)}] @{u}  ({p.get('followers')} followers)", flush=True)
            time.sleep(random.uniform(*HYDRATE_DELAY))
    except RateLimited as e:
        print(f"!! CIRCUIT BREAKER: {e}\nSaving what we have.", flush=True)

    # build rows from full cache
    rows = build_rows(raw_all, cand)

    save_outputs(out_xlsx, raw_json, rows, raw_all)
    strong = sum(1 for r in rows if r["relevance"] == "strong")
    weak = sum(1 for r in rows if r["relevance"] == "weak")
    print(f"\nDONE: {len(rows)} profiles -> {out_xlsx.name}  "
          f"(strong={strong}, weak={weak}, none={len(rows)-strong-weak})", flush=True)

if __name__ == "__main__":
    main()
