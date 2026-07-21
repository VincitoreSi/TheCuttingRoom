#!/usr/bin/env python3
# SUPERSEDED by the AutoSearch agent (/AutoSearch) for hub-gated, ongoing discovery — same
# guest-first/burner-opt-in engines, now Claude-scored and human-approved via
# POST /api/discovery/{p} + the Dashboard. This script remains for manual/offline use.
"""
discover_pages.py — auto-discover MORE pages in your niche (opt-in, config-driven).

Reads the "discovery" block of niche_config.json and finds candidate Instagram pages
in that niche two ways, then hydrates + niche-scores each and writes a ranked list
that feeds straight into scrape_reels.py.

  Engine A — keyword search  (niche_config.discovery.keywords)
  Engine B — seed-expand     (related profiles for niche_config.discovery.seeds)

IMPORTANT — this step is NOT guest-only:
  Instagram now gates BOTH search and related-profiles behind a login, so both
  engines require a **burner** sessionid in session.txt (never your real account).
  Profile hydration still runs in guest mode first. If session.txt is missing, this
  script explains that and exits — the handpicked-pages flow (pages.txt -> scrape ->
  analyze) stays fully guest-safe and needs none of this.

OUTPUT:
  <Niche>_Pages.xlsx     ranked candidates (Profiles + Summary sheets)
  discovered_pages.txt   handles passing the follower + relevance filters
  discovered_raw.json    hydration cache (resume)

USAGE:
  python discover_pages.py                 # uses niche_config.json discovery block
  python discover_pages.py --max 40        # cap hydration (quick test)
  python discover_pages.py --score-only    # rebuild outputs from cache, no network
"""
import sys, json, time, re, random, argparse
from pathlib import Path
import openpyxl

from find_profiles import (
    new_guest_session, load_session, keyword_search, expand_seed, hydrate,
    norm_handle, RateLimited, QUERY_DELAY, EXPAND_DELAY, HYDRATE_DELAY,
)

HERE = Path(__file__).parent
RAW_JSON = HERE / "discovered_raw.json"


def load_discovery_cfg(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    niche = cfg.get("niche") or "Niche"
    d = cfg.get("discovery") or {}
    return niche, {
        "enabled": bool(d.get("enabled", False)),
        "keywords": [k for k in (d.get("keywords") or []) if k.strip()],
        "seeds": [s for s in (d.get("seeds") or []) if s.strip()],
        "search_terms": int(d.get("search_terms") or 6),
        "per_query": int(d.get("per_query") or 50),
        "max_candidates": int(d.get("max_candidates") or 0),
        "min_followers": int(d.get("min_followers") or 0),
        "expand_related": bool(d.get("expand_related", True)),
    }


def niche_score(profile, keywords):
    """Generic relevance: how many niche keywords appear in name/bio/category."""
    hay = " ".join(str(profile.get(k) or "")
                   for k in ("username", "full_name", "biography", "category")).lower()
    hits = sorted({kw for kw in keywords if kw.lower() in hay})
    n = len(hits)
    rel = "strong" if n >= 2 else ("weak" if n == 1 else "none")
    return n, rel, ", ".join(hits)


def build_and_save(niche, cfg, raw_all, cand):
    keywords = cfg["keywords"]
    min_f = cfg["min_followers"]
    rows = []
    for u, p in raw_all.items():
        n, rel, matched = niche_score(p, keywords)
        src = sorted(cand.get(u, {}).get("source") or set(p.get("source") or []))
        via = sorted(cand.get(u, {}).get("via") or set(p.get("via") or []))
        rows.append({
            **p,
            "profile_url": f"https://www.instagram.com/{u}/",
            "niche_hits": n, "relevance": rel, "matched_keywords": matched,
            "source": "+".join(src), "discovered_via": " | ".join(via),
        })
    rows.sort(key=lambda r: ({"strong": 0, "weak": 1, "none": 2}.get(r["relevance"], 3),
                             -(r.get("followers") or 0)))

    out_xlsx = HERE / f"{re.sub(r'[^A-Za-z0-9]+', '_', niche).strip('_')}_Pages.xlsx"
    cols = ["username", "full_name", "profile_url", "followers", "following", "posts",
            "category", "is_verified", "is_private", "is_business", "external_url",
            "biography", "niche_hits", "relevance", "matched_keywords",
            "source", "discovered_via", "user_id"]
    wb = openpyxl.Workbook()
    sh = wb.active; sh.title = "Profiles"
    sh.append(cols)
    for r in rows:
        sh.append([r.get(c) for c in cols])
    s2 = wb.create_sheet("Summary")
    s2.append(["relevance", "count"])
    for rel in ("strong", "weak", "none"):
        s2.append([rel, sum(1 for r in rows if r["relevance"] == rel)])
    s2.append(["TOTAL", len(rows)])
    wb.save(out_xlsx)

    # handles file for scrape_reels: pass follower + relevance filters
    keep = [r for r in rows
            if r["relevance"] in ("strong", "weak") and (r.get("followers") or 0) >= min_f]
    handles_file = HERE / "discovered_pages.txt"
    handles_file.write_text(
        f"# {niche} pages discovered by discover_pages.py "
        f"(relevance>=weak, followers>={min_f})\n"
        + "\n".join(r["username"] for r in keep) + "\n", encoding="utf-8")

    print(f"\nDONE: {len(rows)} candidates -> {out_xlsx.name}", flush=True)
    print(f"  {len(keep)} passed filters -> {handles_file.name} "
          f"(feed it: python scrape_reels.py --file {handles_file.name})", flush=True)
    return out_xlsx, handles_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="niche_config.json")
    ap.add_argument("--session", default="session.txt")
    ap.add_argument("--max", type=int, default=None, help="override discovery.max_candidates")
    ap.add_argument("--score-only", action="store_true",
                    help="rebuild outputs from discovered_raw.json without any network")
    args = ap.parse_args()

    niche, cfg = load_discovery_cfg(HERE / args.config)
    if args.max is not None:
        cfg["max_candidates"] = args.max

    raw_all = {}
    if RAW_JSON.exists():
        try: raw_all = json.loads(RAW_JSON.read_text(encoding="utf-8"))
        except Exception: raw_all = {}

    if args.score_only:
        build_and_save(niche, cfg, raw_all, {})
        return

    if not cfg["enabled"]:
        print("Discovery is disabled (niche_config.discovery.enabled = false).", flush=True)
        print("Set it to true (and add keywords/seeds) to auto-discover pages.", flush=True)
        sys.exit(0)

    if not new_guest_session():
        print("Could not establish guest session", flush=True); sys.exit(1)
    print("Guest session OK (hydration is guest-safe).", flush=True)

    session = load_session(HERE / args.session)
    if not session:
        print(f"\n!! Discovery needs a BURNER sessionid in {args.session} — Instagram gates "
              "search + related-profiles behind login.", flush=True)
        print("   Put a throwaway account's cookie there (never your real account), or skip "
              "discovery and just analyze handpicked pages via pages.txt.", flush=True)
        sys.exit(1)

    cand = {}
    def add(username, src, via):
        c = cand.setdefault(username, {"source": set(), "via": set()})
        c["source"].add(src); c["via"].add(via)

    # Engine B — seed-expand
    seeds = [norm_handle(s) for s in cfg["seeds"]]
    seeds = [s for s in seeds if s]
    if cfg["expand_related"] and seeds:
        print(f"\n== Engine B: seed-expand ({len(seeds)} seeds) ==", flush=True)
        try:
            for s in seeds:
                users = expand_seed(s, session)
                for u in users:
                    add(u["username"], "seed-expand", f"seed:{s}")
                print(f"  @{s}: +{len(users)} (unique {len(cand)})", flush=True)
                time.sleep(random.uniform(*EXPAND_DELAY))
        except RateLimited as e:
            print(f"!! CIRCUIT BREAKER: {e}\nProceeding with what we have.", flush=True)
    else:
        print("\n== Engine B skipped (no seeds / expand_related off) ==", flush=True)

    # Engine A — keyword search
    terms = cfg["keywords"] if cfg["search_terms"] == 0 else cfg["keywords"][:cfg["search_terms"]]
    if terms:
        print(f"\n== Engine A: keyword search ({len(terms)} terms) ==", flush=True)
        try:
            for q in terms:
                users = keyword_search(q, cfg["per_query"], session)
                for u in users:
                    add(u["username"], "keyword", q)
                print(f"  '{q}': +{len(users)} (unique {len(cand)})", flush=True)
                time.sleep(random.uniform(*QUERY_DELAY))
        except RateLimited as e:
            print(f"!! CIRCUIT BREAKER: {e}\nProceeding with what we have.", flush=True)
    else:
        print("\n== Engine A skipped (no keywords) ==", flush=True)

    # hydrate new candidates
    todo = [u for u in cand if u not in raw_all]
    if cfg["max_candidates"] and len(todo) > cfg["max_candidates"]:
        print(f"\n(capping hydration to {cfg['max_candidates']} of {len(todo)})", flush=True)
        todo = todo[:cfg["max_candidates"]]
    print(f"\n== Hydrating {len(todo)} new profiles ({len(raw_all)} cached) ==", flush=True)
    try:
        for i, u in enumerate(todo, 1):
            p = hydrate(u, session)
            if p:
                raw_all[u] = {**p, "source": sorted(cand[u]["source"]),
                              "via": sorted(cand[u]["via"])}
                print(f"  [{i}/{len(todo)}] @{u} ({p.get('followers')} followers)", flush=True)
            time.sleep(random.uniform(*HYDRATE_DELAY))
    except RateLimited as e:
        print(f"!! CIRCUIT BREAKER: {e}\nSaving what we have.", flush=True)

    RAW_JSON.write_text(json.dumps(raw_all, ensure_ascii=False, indent=1), encoding="utf-8")
    build_and_save(niche, cfg, raw_all, cand)


if __name__ == "__main__":
    main()
