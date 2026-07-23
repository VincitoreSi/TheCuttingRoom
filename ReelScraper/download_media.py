#!/usr/bin/env python3
"""
download_media.py — persist reel/short videos + thumbnails locally so the frontend can
play them inline (Instagram/YouTube CDN links expire within hours; metrics are permanent).

Reads platforms/<platform>/content.json (written by `run.py analyze`) and downloads the
selected clips into media/<platform>/<content_id>.mp4 (+ .jpg thumbnail). Selection is a
tier/score GATE applied BEFORE the cap, so only content a user actually cares about is
downloaded (and, downstream, sent to PAID analysis) — never the top-N by raw score.
Skips files already present. Polite, sequential, with a short delay.

  python download_media.py instagram                    # gate from niche_config, else top 60
  python download_media.py instagram --min-tier Viral   # only Viral, then cap
  python download_media.py instagram --min-score 70      # explicit score floor
  python download_media.py instagram --top 150
"""
import sys, json, time, argparse, logging, urllib.request
from pathlib import Path

from core.atomicio import atomic_path
from core.hubevents import HubEvents
from core.logsetup import setup_logging
from core.virality import load_config, tier_threshold

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0 Safari/537.36"
log = logging.getLogger("media")


def _get(url, dest):
    """Download to `<dest>.part`, rename only once the whole body has landed.

    The widest corruption window in the repo used to be right here: `open(dest, "wb")` sat
    in the same `with` as `urlopen`, so the file at its FINAL name was truncated for the
    entire download — up to sixty seconds per clip. And the damage was permanent, because
    the loop below skips any clip whose `.mp4` already exists: a truncated file is never
    retried, `_media_count` in the hub counts it toward analysis readiness, and
    AnalysisEngine then uploads the ruin to a PAID API and pays to analyse nothing.

    `.part` is invisible to `glob("*.mp4")` and to the exact `f"{cid}.mp4"` lookups the hub
    does, so an interrupted download leaves the clip simply absent — which is the state the
    `not mp4.exists()` retry was written for."""
    with atomic_path(dest) as tmp:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            f.write(r.read())


def select_rows(rows, min_score=None, top=60):
    """The gate: keep scored rows meeting `min_score`, sort by score, THEN slice to `top`.

    Filtering BEFORE the cap is the whole point — the old code sliced the top-N by score with
    no floor, so excluding a tier would still be topped back up to the cap with the very
    clips it meant to exclude. `min_score` None = no floor (the pre-gate behaviour)."""
    rows = [r for r in rows if r.get("virality_score") is not None]
    if min_score is not None:
        rows = [r for r in rows if r["virality_score"] >= min_score]
    rows = sorted(rows, key=lambda r: -r["virality_score"])
    return rows[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platform")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--min-score", type=float, default=None,
                    help="only download clips scoring >= this (overrides --min-tier)")
    ap.add_argument("--min-tier", default=None,
                    help="only download clips at or above this tier label (from niche_config)")
    args = ap.parse_args()
    run_id = setup_logging("media", platform=args.platform)
    # Media was the one pipeline stage the Activity feed never showed: the hub spawns it blind
    # and its `core/logsetup` output lands in a file the hub never reads, so a clean run was
    # invisible on the Floor Log. Speak the SAME lifecycle vocabulary the scraper does —
    # run.start / item.* / run.end via BACKEND_API — so media gets a live per-reel thread too.
    events = HubEvents("media", run_id=run_id, platform=args.platform)

    cj = ROOT / "platforms" / args.platform / "content.json"
    if not cj.exists():
        log.error("no content.json — run analyze first", extra={"platform": args.platform})
        sys.exit(1)

    # Resolve the gate: an explicit --min-score wins; otherwise a --min-tier label is mapped
    # to a score through THIS platform's tiers, so labels/thresholds match the scoring engine.
    min_score = args.min_score
    if min_score is None and args.min_tier:
        _, tiers, _ = load_config(ROOT / "platforms" / args.platform / "niche_config.json")
        min_score = tier_threshold(tiers, args.min_tier)
        if min_score is None:
            log.warning("unknown --min-tier, ignoring", extra={"tier": args.min_tier})

    rows = json.loads(cj.read_text(encoding="utf-8"))
    rows = select_rows(rows, min_score=min_score, top=args.top)

    out = ROOT / "media" / args.platform
    out.mkdir(parents=True, exist_ok=True)
    # Plan up front so the count is known before the first fetch. A reel is "planned" only if
    # it has a live media_url and no .mp4 already on disk — the exact set the loop attempts and
    # the exact set that gets an item.* thread. `total` rides on every item.start as well as on
    # run.start: the Dashboard's 300-event log ring evicts run.start first on a long run, so a
    # reducer reading the total only from there would go blank exactly when it matters.
    planned = sum(1 for r in rows if r.get("content_id") and r.get("media_url")
                  and not (out / f"{r['content_id']}.mp4").exists())
    # NOT "media" as the stage label — liveStageIndex ORs `data.stage` against each board
    # node's pipeline key, so a literal "media" would light the media node from the wrong axis.
    events.emit("run.start", msg=f"downloading {planned} reels",
                data={"stage": "Downloading", "total": planned})
    got = skip = fail = 0
    n = 0
    for r in rows:
        cid = r.get("content_id")
        if not cid:
            continue
        mp4, jpg = out / f"{cid}.mp4", out / f"{cid}.jpg"
        if r.get("media_url") and not mp4.exists():
            n += 1
            events.emit("item.start", content_id=cid, msg=f"downloading {cid}",
                        data={"stage": "Downloading", "i": n, "of": planned})
            try:
                _get(r["media_url"], mp4); got += 1
                log.info("downloaded", extra={"file": f"{cid}.mp4"})
                events.emit("item.done", content_id=cid, msg=f"{cid}: downloaded",
                            data={"stage": "Done", "ok": True})
            except Exception as e:
                fail += 1
                log.warning("download failed", extra={"file": f"{cid}.mp4", "err": str(e)})
                # A failed clip is a WARNING that still exits 0 — expired CDN links are the
                # normal case — never item.error, which would falsely snap the whole media
                # thread red the way the scraper refuses to for an unresolved creator.
                events.emit("item.done", level="warning", content_id=cid,
                            msg=f"{cid}: download failed ({e})",
                            data={"stage": "Done", "ok": False})
            time.sleep(0.6)
        else:
            skip += 1
        if r.get("thumbnail_url") and not jpg.exists():
            try: _get(r["thumbnail_url"], jpg)
            except Exception: pass
    log.info("media done", extra={"platform": args.platform, "downloaded": got, "present": skip,
                                  "failed": fail, "dir": str(out)})
    # level="info" ALWAYS: a failed clip already carried its own warning, and the run itself
    # exits 0 whether or not a CDN link expired — painting run.end red would lie about that.
    events.emit("run.end", msg=f"{got} downloaded, {skip} present, {fail} failed",
                data={"stage": "Done", "downloaded": got, "present": skip, "failed": fail})
    if fail:
        log.info("failures are usually expired CDN links — re-scrape then re-run soon after")
    print(f"DONE [{args.platform}]: {got} downloaded, {skip} present, {fail} failed -> {out}", flush=True)


if __name__ == "__main__":
    main()
