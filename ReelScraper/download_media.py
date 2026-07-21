#!/usr/bin/env python3
"""
download_media.py — persist reel/short videos + thumbnails locally so the frontend can
play them inline (Instagram/YouTube CDN links expire within hours; metrics are permanent).

Reads platforms/<platform>/content.json (written by `run.py analyze`) and downloads the
top-N by virality_score into media/<platform>/<content_id>.mp4 (+ .jpg thumbnail).
Skips files already present. Polite, sequential, with a short delay.

  python download_media.py instagram            # top 60 (default)
  python download_media.py instagram --top 150
"""
import sys, json, time, argparse, logging, urllib.request
from pathlib import Path

from core.atomicio import atomic_path
from core.logsetup import setup_logging

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platform")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()
    setup_logging("media", platform=args.platform)

    cj = ROOT / "platforms" / args.platform / "content.json"
    if not cj.exists():
        log.error("no content.json — run analyze first", extra={"platform": args.platform})
        sys.exit(1)
    rows = json.loads(cj.read_text(encoding="utf-8"))
    rows = [r for r in rows if r.get("virality_score") is not None]
    rows.sort(key=lambda r: -r["virality_score"])
    rows = rows[:args.top]

    out = ROOT / "media" / args.platform
    out.mkdir(parents=True, exist_ok=True)
    got = skip = fail = 0
    for r in rows:
        cid = r.get("content_id")
        if not cid:
            continue
        mp4, jpg = out / f"{cid}.mp4", out / f"{cid}.jpg"
        if r.get("media_url") and not mp4.exists():
            try:
                _get(r["media_url"], mp4); got += 1
                log.info("downloaded", extra={"file": f"{cid}.mp4"})
            except Exception as e:
                fail += 1
                log.warning("download failed", extra={"file": f"{cid}.mp4", "err": str(e)})
            time.sleep(0.6)
        else:
            skip += 1
        if r.get("thumbnail_url") and not jpg.exists():
            try: _get(r["thumbnail_url"], jpg)
            except Exception: pass
    log.info("media done", extra={"platform": args.platform, "downloaded": got, "present": skip,
                                  "failed": fail, "dir": str(out)})
    if fail:
        log.info("failures are usually expired CDN links — re-scrape then re-run soon after")
    print(f"DONE [{args.platform}]: {got} downloaded, {skip} present, {fail} failed -> {out}", flush=True)


if __name__ == "__main__":
    main()
