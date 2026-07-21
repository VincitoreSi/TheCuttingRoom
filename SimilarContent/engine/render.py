#!/usr/bin/env python3
"""engine/render.py — turn one approved clone recipe into a posted-ready reel.

The pipeline for a single item:

    recipe markdown -> RenderPlan -> N generated frames -> ffmpeg -> mp4 + poster
                    -> caption -> POST /api/renders/{platform}

Rendering costs real money (roughly $0.04 per frame) and a running job cannot be
cancelled, so the ordering here is deliberate: everything that can fail for free — parsing,
duration allocation, prompt composition, the frame budget — happens before the first API
call, and `--dry-run` stops exactly there.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from engine import AGENT_NAME
from engine.caption import CaptionError, GeminiTextClient, generate_caption
from engine.circuit import CircuitBreaker, CircuitTripped
from engine.nanobanana import ImageError, NanoBananaClient
from engine.recipe import (
    RenderPlan, allocate_durations, cap_frames, compose_frame_prompt, parse_recipe,
)
from engine.stitch import (
    REELS_ASPECT, canvas_for, poster, probe_duration, probe_streams, stitch,
    write_frame_manifest,
)

log = logging.getLogger("sc.render")

ASSETS = Path(__file__).resolve().parents[1] / "assets"


@dataclass
class RenderResult:
    plan: RenderPlan
    mp4: Path | None = None
    poster: Path | None = None
    frames: list[Path] = field(default_factory=list)
    durations: list[float] = field(default_factory=list)
    caption: dict | None = None
    dropped_shots: list[int] = field(default_factory=list)
    duration_s: float | None = None


def resolve_source(hub, plan: RenderPlan) -> tuple[str | None, str | None, dict | None]:
    """Find the exemplar this clone reproduces: (content_id, caption, virality_formula).

    Joins on the source reel URL first. The `content_id` embedded in the studio filename is
    truncated to 12 characters and cannot round-trip, so it is only a fallback prefix match.
    """
    try:
        rows = hub.content(plan.platform)
    except Exception as e:                              # noqa: BLE001 — never fatal
        log.warning("content fetch failed; rendering without source context",
                    extra={"err": str(e)})
        return None, None, None

    row = None
    if plan.source_url:
        row = next((r for r in rows if r.get("url") == plan.source_url), None)
    if row is None and plan.content_id_prefix:
        row = next((r for r in rows
                    if str(r.get("content_id", "")).startswith(plan.content_id_prefix)), None)
    if row is None:
        log.warning("no corpus row matched this clone", extra={"file": plan.file})
        return None, None, None

    cid = row.get("content_id")
    bp = hub.blueprint(plan.platform, cid) if cid else None
    return cid, row.get("caption"), (bp or {}).get("virality_formula")


def plan_frames(plan: RenderPlan, cfg: dict) -> tuple[list, list[float], list[int]]:
    """Decide which shots render and how long each holds. Free, and fully deterministic."""
    max_frames = int(cfg.get("max_frames_per_clone") or 12)
    kept = cap_frames(plan.shots, max_frames)
    dropped = [s.index for s in plan.shots if s not in kept]
    if dropped:
        # Never silent: a shortened clone still claims to reproduce the original.
        log.warning("frame budget exceeded — dropped shots %s (budget %d of %d)",
                    dropped, max_frames, len(plan.shots))
    durations = allocate_durations(kept, plan.target_duration_s,
                                   float(cfg.get("frame_min_hold_s") or 0.6))
    return kept, durations, dropped


def render_frames(client: NanoBananaClient, plan: RenderPlan, shots: list, cfg: dict,
                  work: Path, breaker: CircuitBreaker,
                  on_progress=None) -> list[Path]:
    """Generate one image per shot, anchoring every later frame to the first.

    Frame 0 establishes the subject; frames 1..N are generated with it attached so the
    clone reads as one person in one place. See engine/nanobanana for why this replaces
    the seed-pinning the config knobs imply.
    """
    aspect = str(cfg.get("aspect_ratio") or REELS_ASPECT)
    anchors: list[tuple[bytes, str]] = []
    out: list[Path] = []
    degraded = 0

    for i, shot in enumerate(shots):
        breaker.pace()
        prompt = compose_frame_prompt(plan, shot, aspect)
        try:
            raw, mime = client.generate_image(prompt, ref_images=anchors, aspect_ratio=aspect)
        except ImageError as e:
            # The model intermittently refuses to generate FROM a photorealistic reference
            # of a person (finishReason IMAGE_OTHER), and the refusal is a property of the
            # particular anchor image rather than a transient error — retrying with the
            # same anchor fails every time. Consistency is worth a lot, but not the frame
            # itself, so fall back to an unanchored generation and say so.
            if anchors:
                log.warning("frame %d refused with an anchor; retrying unanchored "
                            "(subject consistency may drift): %s", i, e)
                try:
                    breaker.pace()
                    raw, mime = client.generate_image(prompt, ref_images=None,
                                                      aspect_ratio=aspect)
                    degraded += 1
                except ImageError as e2:
                    log.error("frame %d failed unanchored too: %s", i, e2)
                    breaker.record_failure(f"frame {i}: {e2}")
                    continue
            else:
                log.error("frame %d failed: %s", i, e)
                breaker.record_failure(f"frame {i}: {e}")  # CircuitTripped at 3 strikes
                continue
        breaker.record_success()

        ext = ".jpg" if "jpeg" in (mime or "") else ".png"
        path = work / f"frame-{i:02d}{ext}"
        path.write_bytes(raw)
        out.append(path)
        if not anchors:
            anchors = [(raw, mime or "image/png")]     # frame 0 is the identity anchor
        if on_progress:
            on_progress(i + 1, len(shots), shot)

    if not out:
        raise ImageError("no frames were generated")
    if degraded:
        log.warning("%d of %d frame(s) were generated without the consistency anchor — "
                    "check the subject still reads as the same person before posting",
                    degraded, len(shots))
    return out


def existing_frames(work: Path) -> list[Path]:
    """Frames already generated for this clone, in shot order."""
    return sorted(p for p in work.glob("frame-*.*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))


def render_item(hub, cfg: dict, md: str, file: str, platform: str, *,
                run_id: str, dry_run: bool = False, reuse_frames: bool = False,
                work_dir: Path | None = None) -> RenderResult:
    """Render one approved studio item end to end.

    `reuse_frames` re-stitches the frames already on disk instead of generating new ones.
    Image generation is the entire cost of a render and is non-deterministic, so after a
    change to the stitcher (canvas, fit, frame rate) this re-encodes the SAME pictures for
    free rather than paying to produce different ones.
    """
    plan = parse_recipe(md, file, platform)
    shots, durations, dropped = plan_frames(plan, cfg)
    result = RenderResult(plan=plan, durations=durations, dropped_shots=dropped)

    if dry_run:
        print(f"\n=== {plan.title}\n    {file}")
        print(f"    target {plan.target_duration_s}s over {len(shots)} frame(s)"
              f"{f' (dropped shots {dropped})' if dropped else ''}")
        for shot, dur in zip(shots, durations):
            print(f"\n--- shot {shot.index}  hold {dur:.3f}s "
                  f"{'· text: ' + shot.on_screen_text.replace(chr(10), ' / ') if shot.on_screen_text else ''}")
            print(compose_frame_prompt(plan, shot, str(cfg.get("aspect_ratio") or REELS_ASPECT)))
        print(f"\n    total {sum(durations):.3f}s  (no API calls made)")
        return result

    work = work_dir or (ASSETS / plan.slug)
    work.mkdir(parents=True, exist_ok=True)

    hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform,
                 msg=f"rendering {plan.slug}", data={"file": file, "stage": "Rendering"})

    if reuse_frames:
        frames = existing_frames(work)
        if not frames:
            raise ImageError(
                f"--restitch needs frames on disk, but {work} has none. Run a normal "
                "render first.")
        log.info("re-stitching %d existing frame(s) — no image API calls", len(frames))
        shots = shots[:len(frames)]
    else:
        breaker = CircuitBreaker(max_strikes=3,
                                 pace_seconds=float(cfg.get("pace_seconds") or 2.0))
        client = NanoBananaClient(cfg["_api_key"],
                                  model=str(cfg.get("image_model") or "gemini-2.5-flash-image"),
                                  endpoint=cfg.get("image_endpoint"),
                                  retries=int(cfg.get("image_retries") or 3))

        def progress(done, total, shot):
            log.info("frame %d/%d rendered", done, total, extra={"shot": shot.index})
            hub.post_log(AGENT_NAME, "item.progress", run_id=run_id, platform=platform,
                         msg=f"frame {done}/{total}",
                         data={"file": file, "frame": done, "of": total,
                               "stage": "Rendering"})

        frames = render_frames(client, plan, shots, cfg, work, breaker, progress)
    result.frames = frames

    # A frame may have failed and been skipped; re-allocate so the holds still sum to the
    # source duration rather than leaving a gap where the missing shot was.
    if len(frames) != len(shots):
        durations = allocate_durations(shots[:len(frames)], plan.target_duration_s,
                                       float(cfg.get("frame_min_hold_s") or 0.6))
        result.durations = durations

    write_frame_manifest([(p.name, d) for p, d in zip(frames, durations)],
                         work / "frames.txt")
    # The canvas comes from the aspect ratio, not from two loose width/height knobs — that
    # way the video can never be rendered to a size that isn't the aspect it claims.
    aspect = str(cfg.get("aspect_ratio") or REELS_ASPECT)
    cw, ch = canvas_for(aspect)
    mp4 = stitch(list(zip(frames, durations)), work / "reel.mp4",
                 width=cw, height=ch,
                 fps=int(cfg.get("video_fps") or 30),
                 fit=str(cfg.get("video_fit") or "auto"))
    result.mp4 = mp4
    result.poster = poster(mp4, work / "poster.jpg")
    result.duration_s = probe_duration(mp4)

    content_id, source_caption, virality_formula = resolve_source(hub, plan)

    caption = None
    if reuse_frames:
        # A re-stitch is meant to change the container, nothing else. Regenerating would
        # silently hand back different copy for a reel the operator may already have
        # approved and scheduled.
        prev = next(iter(hub.renders(platform, file=file)), None) or {}
        if prev.get("caption"):
            caption = {"caption": prev["caption"], "hashtags": prev.get("hashtags") or [],
                       "alt_captions": prev.get("alt_captions") or []}
            result.caption = caption
            log.info("re-stitch: keeping the existing caption")
    if caption is None:
        try:
            caption = generate_caption(
                GeminiTextClient(cfg["_api_key"],
                                 model=str(cfg.get("caption_model") or "gemini-2.5-flash")),
                plan, source_caption, virality_formula,
                temperature=float(cfg.get("caption_temperature") or 0.8))
            result.caption = caption
        except CaptionError as e:
            # The video is the expensive part and it already exists — ship it captionless
            # rather than throwing the render away over a text call.
            log.warning("caption generation failed; publishing without one",
                        extra={"err": str(e)})

    info = probe_streams(mp4)
    hub.post_render(platform, {
        "file": file, "agent": AGENT_NAME, "kind": "slideshow",
        "content_id": content_id, "slug": plan.slug,
        "caption": (caption or {}).get("caption"),
        "caption_model": str(cfg.get("caption_model") or "gemini-2.5-flash") if caption else None,
        "hashtags": (caption or {}).get("hashtags", []),
        "duration_s": result.duration_s,
        # Probed off the finished container, not copied from config — the record should
        # describe the file that exists, so the Dashboard can size its player from fact.
        "width": info["width"], "height": info["height"], "fps": info["fps"],
        "aspect_ratio": aspect, "video_fit": str(cfg.get("video_fit") or "auto"),
        "has_audio": info["has_audio"],
        "provider": str(cfg.get("image_provider") or "nano_banana"),
        "run_id": run_id,
        "frames": [{"frame": p.name, "kb": p.stat().st_size // 1024,
                    "provider": str(cfg.get("image_provider") or "nano_banana"),
                    "duration_s": d, "on_screen_text": s.on_screen_text}
                   for p, d, s in zip(frames, durations, shots)],
        "dropped_shots": dropped,
        "alt_captions": (caption or {}).get("alt_captions", []),
    }, assets=[mp4, result.poster])

    hub.post_log(AGENT_NAME, "item.done", run_id=run_id, platform=platform,
                 content_id=content_id, msg=f"rendered {plan.slug}",
                 data={"file": file, "stage": "Rendered",
                       "frames": len(frames), "duration_s": result.duration_s})
    return result


def already_rendered(hub, platform: str, file: str, item_updated_at: float | None) -> bool:
    """True when a render exists that is newer than the studio item — so a re-run after an
    interrupted batch skips finished work instead of paying to redo it."""
    try:
        existing = hub.renders(platform, file=file)
    except Exception:                                   # noqa: BLE001
        return False
    if not existing:
        return False
    return (existing[0].get("updated_at") or 0) >= (item_updated_at or 0)


__all__ = ["RenderResult", "render_item", "plan_frames", "already_rendered",
           "resolve_source", "CircuitTripped", "time"]
