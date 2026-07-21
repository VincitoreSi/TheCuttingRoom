#!/usr/bin/env python3
"""Backfill the hub's render store from renders that already exist on disk.

The SimilarContent agent produced five slideshow reels in an ad-hoc run before the render
store existed, leaving them in `SimilarContent/assets/<slug>/reel.mp4` — outside the hub's
ROOT, and therefore unreachable over HTTP. This walks those assets, matches each to its
studio proposal by slug, derives a poster with ffmpeg, and uploads them through the same
`POST /api/renders/{platform}` endpoint an agent would use.

One-shot backfill, safe to re-run (the endpoint upserts). Once SimilarContent renders
through its own CLI this script has no further purpose.

    python3 scripts/seed_renders.py [--platform instagram] [--dry-run]
"""
import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "SimilarContent" / "assets"
BACKEND = "http://127.0.0.1:8787"


def _post(backend, path, payload):
    req = urllib.request.Request(
        backend + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}


def _studio_files(backend, platform):
    with urllib.request.urlopen(f"{backend}/api/studio/{platform}", timeout=30) as r:
        return [it["file"] for it in json.loads(r.read().decode())]


def _match(slug, files):
    """Studio filenames embed the slug: 2026-07-19-similar-<slug>-<cid12>.md."""
    return next((f for f in files if slug in f), None)


def _poster(mp4, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
                    "-frames:v", "1", "-q:v", "3", str(out)], check=True)


def _probe(mp4):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", str(mp4)],
                       capture_output=True, text=True, check=True)
    return round(float(r.stdout.strip()), 3)


def _b64(p):
    return base64.b64encode(p.read_bytes()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="instagram")
    ap.add_argument("--backend", default=BACKEND)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    render_manifest = {r["slug"]: r for r in
                       json.loads((ASSETS / "render_manifest.json").read_text())}
    try:
        files = _studio_files(args.backend, args.platform)
    except urllib.error.URLError as e:
        print(f"hub unreachable at {args.backend}: {e}", file=sys.stderr)
        print("start it with:  cd ReelScraper && uv run cli.py start", file=sys.stderr)
        return 2

    seeded = skipped = 0
    for d in sorted(ASSETS.iterdir()):
        mp4 = d / "reel.mp4"
        if not d.is_dir() or not mp4.exists():
            continue
        studio_file = _match(d.name, files)
        if not studio_file:
            print(f"  skip {d.name}: no studio item matches this slug")
            skipped += 1
            continue

        man = render_manifest.get(d.name, {})
        frames = sorted(p for p in d.glob("frame-*.png"))
        poster = d / "poster.jpg"
        if not args.dry_run and not poster.exists():
            _poster(mp4, poster)

        rec = {
            "file": studio_file, "agent": "similar-content", "kind": "slideshow",
            "content_id": man.get("content_id"), "slug": d.name,
            "duration_s": _probe(mp4), "width": 1080, "height": 1920, "fps": 30,
            "has_audio": False, "provider": "nano_banana",
            "frames": [{"frame": f["frame"], "kb": f.get("kb"),
                        "provider": f.get("provider"),
                        "on_screen_text": f.get("on_screen_text")}
                       for f in man.get("frames", [])],
            "note": "backfilled by scripts/seed_renders.py from a pre-render-store run",
        }
        print(f"  {d.name}  ->  {studio_file}  ({rec['duration_s']}s, {len(frames)} frames)")
        if args.dry_run:
            seeded += 1
            continue

        rec["assets"] = [{"name": "reel.mp4", "content_b64": _b64(mp4)},
                         {"name": "poster.jpg", "content_b64": _b64(poster)}]
        code, body = _post(args.backend, f"/api/renders/{args.platform}", rec)
        if code != 200:
            print(f"    FAILED {code}: {body}", file=sys.stderr)
            skipped += 1
        else:
            print(f"    -> {body['video_url']}")
            seeded += 1

    print(f"\n{seeded} seeded, {skipped} skipped")
    return 0 if seeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
