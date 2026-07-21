#!/usr/bin/env python3
"""capture-demo.py — snapshot the live working install into `demo-data/`.

`./demo` restores this snapshot and launches, so the whole dashboard is populated the
moment someone clones the repo: a scored corpus, blueprints, studio proposals at the human
gate, rendered reels that actually play, evals, and activity logs.

Run it after a real pipeline run, whenever the demo should reflect new work:

    python3 scripts/capture-demo.py            # capture into demo-data/
    python3 scripts/capture-demo.py --dry-run  # show what would be captured, and how big

WHAT IS DELIBERATELY LEFT OUT
  * `platforms/*/reels_raw.json` (60 MB) and the xlsx/csv exports — regenerable, huge, and
    nothing reads them at runtime.
  * Most of `media/` (160 MB of scraped video). Only the posters plus the handful of clips
    that have blueprints are kept, which is what makes the Corpus grid look real without
    committing a media library.
  * Every `.env`, `session.txt`, and `content.db` — secrets and agent memory.

WHAT IS SANITISED
  Instagram's `thumbnail_url` / `media_url` are SIGNED CDN links: they carry `_nc_ohc`,
  `oh=` and `oe=` auth parameters, and they expire within hours. They are stripped, both
  because committing signed URLs is wrong and because they would render as broken images.
  The hub already prefers the local `/media/<content_id>.jpg` when the file exists, which
  is exactly what this snapshot ships.

PRIVACY — READ BEFORE MAKING THIS REPO PUBLIC
  This snapshot contains REAL scraped Instagram data: real creator handles, real captions,
  real engagement metrics, and video frames derived from other people's reels. That is a
  deliberate choice for a PRIVATE repo, to keep the demo realistic. Publishing it would
  republish third-party content and personal data. See `demo-data/README.md`.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "ReelScraper"
DEMO = ROOT / "demo-data"
DATA = DEMO / "data"

PLATFORM = "instagram"

# Signed-CDN fields to blank. They expire, and they carry auth parameters.
SIGNED_URL_FIELDS = ("thumbnail_url", "media_url", "video_url_best", "video_urls_all")
SIGNED_MARKERS = ("_nc_ohc", "oh=", "oe=", "_nc_gid", "efg=")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# ---------------------------------------------------------------------------------------
def analyzed_content_ids() -> list[str]:
    """The reels that have a schema-2 blueprint."""
    d = HUB / "analysis" / PLATFORM
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def cloned_content_ids() -> set[str]:
    """Reels we have actually cloned — i.e. that have a render record.

    These are the ones worth shipping the SOURCE video for, because the demo's story is
    "here is the original, here is our clone". Shipping source video for every analyzed
    clip triples the dataset for reels nobody can compare against anything.
    """
    out = set()
    for rj in (HUB / "renders").glob("*/*/render.json"):
        rec = json.loads(rj.read_text(encoding="utf-8")) if rj.exists() else {}
        if rec.get("content_id"):
            out.add(rec["content_id"])
    return out


def top_content_ids(content: Path, limit: int) -> list[str]:
    """The highest-virality reels — the ones a user actually sees before scrolling."""
    rows = json.loads(content.read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("items", [])
    rows = [r for r in rows if r.get("virality_score") is not None and r.get("content_id")]
    rows.sort(key=lambda r: -float(r["virality_score"]))
    return [r["content_id"] for r in rows[:limit]]


def sanitize_content(src: Path, dst: Path, keep_ids: set[str]) -> dict:
    """Copy content.json, blanking signed CDN URLs. Returns a small report."""
    rows = json.loads(src.read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("items", [])
    stripped = 0
    for r in rows:
        for f in SIGNED_URL_FIELDS:
            v = r.get(f)
            if isinstance(v, str) and v and any(m in v for m in SIGNED_MARKERS):
                r[f] = ""
                stripped += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return {"reels": len(rows), "signed_urls_stripped": stripped,
            "creators": len({r.get("creator") for r in rows if r.get("creator")})}


def copy_into(src: Path, dest_rel: str, report: list) -> None:
    """Copy a file or directory into demo/data/<dest_rel>, preserving structure."""
    if not src.exists():
        return
    dst = DATA / dest_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    report.append((dest_rel, size_of(src)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--platform", default=PLATFORM)
    ap.add_argument("--max-posters", type=int, default=80, metavar="N",
                    help="ship posters for the top N reels by virality (default 80). The "
                         "rest fall back to the on-brand placeholder, which is what the "
                         "real install looks like anyway.")
    args = ap.parse_args()

    content = HUB / "platforms" / args.platform / "content.json"
    if not content.exists():
        print(f"ERROR: no corpus at {rel(content)} — run the pipeline first.", file=sys.stderr)
        return 2

    # Source video only for reels we cloned; posters only for the visible top slice.
    keep = cloned_content_ids()
    poster_ids = set(top_content_ids(content, args.max_posters))
    print(f"cloned reels (source video kept for these): {len(keep)}")
    print(f"posters: top {args.max_posters} by virality")

    if args.dry_run:
        media_dir = HUB / "media" / args.platform
        posters = [media_dir / f"{c}.jpg" for c in poster_ids]
        posters = [p for p in posters if p.exists()]
        vids = [media_dir / f"{c}.mp4" for c in keep]
        vids = [v for v in vids if v.exists()]
        est = (size_of(content) + sum(size_of(p) for p in posters)
               + sum(size_of(v) for v in vids)
               + size_of(HUB / "renders") + size_of(HUB / "analysis")
               + size_of(HUB / "studio") + size_of(HUB / "evals"))
        print(f"  corpus json      {human(size_of(content))}")
        print(f"  posters ({len(posters):>3})     {human(sum(size_of(p) for p in posters))}")
        print(f"  corpus video ({len(vids)}) {human(sum(size_of(v) for v in vids))}")
        print(f"  renders          {human(size_of(HUB / 'renders'))}")
        print(f"  analysis         {human(size_of(HUB / 'analysis'))}")
        print(f"  studio           {human(size_of(HUB / 'studio'))}")
        print(f"\n  estimated demo/ size: {human(est)}")
        return 0

    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    report: list[tuple[str, int]] = []

    # 1. corpus (sanitised) + the per-platform config a fresh hub needs
    stats = sanitize_content(content, DATA / f"ReelScraper/platforms/{args.platform}/content.json",
                             keep)
    report.append((f"ReelScraper/platforms/{args.platform}/content.json",
                   size_of(DATA / f"ReelScraper/platforms/{args.platform}/content.json")))
    for name in ("pages.txt", "niche_config.json", "profiles_meta.json"):
        copy_into(HUB / "platforms" / args.platform / name,
                  f"ReelScraper/platforms/{args.platform}/{name}", report)

    # 2. media — posters for the whole grid, video only for the analyzed clips
    media_src = HUB / "media" / args.platform
    if media_src.exists():
        for cid in sorted(poster_ids):
            copy_into(media_src / f"{cid}.jpg",
                      f"ReelScraper/media/{args.platform}/{cid}.jpg", report)
        for cid in sorted(keep):
            copy_into(media_src / f"{cid}.mp4",
                      f"ReelScraper/media/{args.platform}/{cid}.mp4", report)

    # 3. everything downstream of the corpus: blueprints, the gate, the renders, the receipts
    for src, dest in (
        (HUB / "analysis" / args.platform, f"ReelScraper/analysis/{args.platform}"),
        (HUB / "studio" / args.platform, f"ReelScraper/studio/{args.platform}"),
        (HUB / "renders", "ReelScraper/renders"),
        (HUB / "evals", "ReelScraper/evals"),
        (HUB / "config", "ReelScraper/config"),
        (HUB / "producers", "ReelScraper/producers"),
        (HUB / "discovery", "ReelScraper/discovery"),
        (HUB / "logs" / "agents.jsonl", "ReelScraper/logs/agents.jsonl"),
    ):
        copy_into(src, dest, report)

    total = sum(sz for _, sz in report)
    manifest = {
        "captured_from": "live working install",
        "platform": args.platform,
        "corpus": stats,
        "cloned_reels_with_source_video": len(keep),
        "posters": args.max_posters,
        "total_bytes": total,
        "excludes": ["platforms/*/reels_raw.json", "platforms/*/*.xlsx", "platforms/*/*.csv",
                     "media/*.mp4 except cloned clips", "*/.env", "*/session.txt",
                     "memory/*/content.db"],
        "sanitised": {"signed_cdn_urls_blanked": stats["signed_urls_stripped"],
                      "fields": list(SIGNED_URL_FIELDS)},
        "entries": sorted({d.split("/")[1] if "/" in d else d for d, _ in report}),
    }
    (DEMO / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\ncaptured {len(report)} paths, {human(total)} into {rel(DATA)}")
    print(f"  corpus: {stats['reels']} reels / {stats['creators']} creators, "
          f"{stats['signed_urls_stripped']} signed URLs blanked")
    print(f"  manifest: {rel(DEMO / 'manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
