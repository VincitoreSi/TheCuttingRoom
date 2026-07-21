#!/usr/bin/env python3
"""engine/stitch.py — assemble rendered frames into a vertical reel with system ffmpeg.

Shelling out to ffmpeg rather than adding moviepy/imageio keeps this agent's dependency
list empty, matching the house style (AnalysisEngine and AutoSearch are stdlib + one API
client each).

WHY THE CONCAT *FILTER*, NOT THE CONCAT DEMUXER
-----------------------------------------------
The obvious approach — a `_concat.txt` list fed to `-f concat` — does not hold the
durations you give it, and the earlier manual renders in `assets/` are the evidence. With
the widely-copied "repeat the last file" trick, the trailing `duration` directive is
ignored and the repeated frame inherits the previous hold, adding one full frame duration
to every clip: a 6.22s target rendered as 12.47s, a 32.2s target as 42.6s. Without the
repeat, the demuxer instead truncates most of the final segment (a 9.75s list came out at
6.37s). Neither is usable when the whole point is matching the source duration.

Feeding each frame as its own `-loop 1 -t <duration>` input and joining them with the
concat *filter* gives per-segment durations that are exact to within one frame period
(33ms at 30fps — an unavoidable consequence of quantising to a fixed frame rate, and well
below the threshold where a beat lands late).

Output is SILENT by design. Instagram's licensed audio cannot be attached
programmatically, so the recipe's `## Audio` block is the manual-attach handoff for the
operator; muxing some approximation here would produce a reel that looks finished but
carries the wrong sound.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("sc.stitch")

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# The canvases we render to, as (width, height). Reels/Shorts/TikTok are all 9:16, and
# that is the default and the only one that fills a phone screen full-bleed — the others
# exist so a future feed-first or square variant has somewhere to go, not because a reel
# should ever use them.
ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),     # reels / shorts / tiktok — full-screen vertical
    "4:5":  (1080, 1350),     # instagram feed portrait
    "1:1":  (1080, 1080),     # square
}
REELS_ASPECT = "9:16"


def canvas_for(aspect_ratio: str | None) -> tuple[int, int]:
    """Resolve an aspect-ratio label to a pixel canvas, defaulting to reels."""
    return ASPECT_PRESETS.get((aspect_ratio or REELS_ASPECT).strip(),
                              ASPECT_PRESETS[REELS_ASPECT])


class StitchError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))


def require_ffmpeg() -> None:
    if not ffmpeg_available():
        raise StitchError(
            f"{FFMPEG}/{FFPROBE} not found on PATH. Install with `brew install ffmpeg` "
            "(or set FFMPEG_BIN/FFPROBE_BIN).")


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    log.debug("ffmpeg", extra={"argc": len(cmd)})
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if r.returncode != 0:
        raise StitchError(f"{cmd[0]} failed ({r.returncode}): "
                          f"{(r.stderr or r.stdout or '').strip()[-600:]}")
    return r


# How far a frame's own aspect may sit from the canvas before cropping starts eating
# composition. Nano Banana's "9:16" is 768x1344 (4:7) — 1.6% off, safely croppable. It also
# sometimes ignores the request and returns a square 1024x1024, which is 78% off; cropping
# that to 9:16 discarded 44% of the width and cut a headline down to "Y BEGINS WITH THE
# RIGHT B". Hence a tolerance rather than a global choice.
CROP_TOLERANCE = 0.10


def probe_image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of a still, or None if it cannot be read."""
    try:
        r = _run([FFPROBE, "-v", "error", "-select_streams", "v",
                  "-show_entries", "stream=width,height",
                  "-of", "csv=p=0:s=x", str(path)])
        w, _, h = r.stdout.strip().partition("x")
        return int(w), int(h)
    except (StitchError, ValueError):
        return None


def _scale_chain(width: int, height: int, fit: str = "auto",
                 src: tuple[int, int] | None = None) -> str:
    """Fit a generated frame to the canvas without ever distorting it.

    Providers do not honour the aspect you ask for, and the two shapes Nano Banana returns
    need opposite treatment — so the default `fit="auto"` decides per frame:

      * within CROP_TOLERANCE of the canvas -> `cover`: scale to fill, centre-crop the
        sliver of overflow. No bars; a reel with bars reads as an amateur repost.
      * further out -> `contain`: letterbox. Thin bars cost far less than cropping the hook
        text or the subject out of the frame.

    `cover` / `contain` force one or the other. Neither ever stretches — a distorted face
    would sink the clone instantly.
    """
    contain = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
               f"setsar=1,format=yuv420p")
    cover = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
             f"crop={width}:{height},setsar=1,format=yuv420p")
    if fit == "contain":
        return contain
    if fit == "cover":
        return cover
    if not src or not src[0] or not src[1]:
        return contain                      # unknown source: never crop blind
    target = width / height
    drift = abs((src[0] / src[1]) - target) / target
    return cover if drift <= CROP_TOLERANCE else contain


def write_frame_manifest(frames: list[tuple[str, float]], out: Path) -> Path:
    """Write the frame/duration plan next to the render.

    Provenance only — the render itself is driven by the filter graph below, not by this
    file. It exists so a human can see what hold each frame was given without re-reading
    the render record.
    """
    lines = [f"{name}\t{dur:.3f}s" for name, dur in frames]
    total = sum(d for _, d in frames)
    out.write_text("\n".join(lines) + f"\n# total\t{total:.3f}s\n", encoding="utf-8")
    return out


def stitch(frames: list[tuple[Path, float]], out: Path, width: int = 1080,
           height: int = 1920, fps: int = 30, crf: int = 20, fit: str = "auto") -> Path:
    """Join `[(frame_path, hold_seconds), …]` into an H.264 mp4 sized for vertical short-form."""
    require_ffmpeg()
    if not frames:
        raise StitchError("no frames to stitch")
    missing = [str(p) for p, _ in frames if not Path(p).exists()]
    if missing:
        raise StitchError(f"missing frame file(s): {missing}")

    # Per-frame, because a provider can hand back different shapes within one clone and
    # the crop-vs-letterbox call depends on each frame's own aspect.
    chains = [_scale_chain(width, height, fit,
                           probe_image_size(Path(p)) if fit == "auto" else None)
              for p, _ in frames]
    if fit == "auto":
        padded = sum(1 for c in chains if "pad=" in c)
        if padded:
            log.info("letterboxing %d of %d frame(s) whose aspect is too far from the "
                     "canvas to crop safely", padded, len(frames))

    cmd = [FFMPEG, "-y", "-v", "error"]
    for path, dur in frames:
        # `-framerate fps` must match the output rate. A looped image input defaults to
        # 25fps, so with a 30fps output every segment is resampled and gains a frame or
        # two — an error that compounds per shot rather than staying within one frame.
        cmd += ["-loop", "1", "-framerate", str(fps),
                "-t", f"{max(dur, 0.001):.3f}", "-i", Path(path).name]
    graph = ";".join(f"[{i}:v]{chains[i]}[v{i}]" for i in range(len(frames)))
    labels = "".join(f"[v{i}]" for i in range(len(frames)))
    cmd += [
        "-filter_complex", f"{graph};{labels}concat=n={len(frames)}:v=1:a=0[out]",
        "-map", "[out]",
        "-r", str(fps),               # constant frame rate — IG re-encodes VFR badly
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",        # required for Safari/QuickTime playback
        "-movflags", "+faststart",    # metadata first, so the Dashboard can stream it
        "-an",                        # silent by design — see the module docstring
        out.name,
    ]
    _run(cmd, cwd=Path(frames[0][0]).parent)
    if not out.exists() or out.stat().st_size == 0:
        raise StitchError(f"ffmpeg produced no output at {out}")
    return out


def poster(mp4: Path, out: Path) -> Path:
    """Grab the first frame as the card's poster image."""
    require_ffmpeg()
    _run([FFMPEG, "-y", "-v", "error", "-i", mp4.name,
          "-frames:v", "1", "-q:v", "3", out.name], cwd=mp4.parent)
    return out


def probe_duration(mp4: Path) -> float:
    require_ffmpeg()
    r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(mp4)])
    return round(float(r.stdout.strip()), 3)


def probe_streams(mp4: Path) -> dict:
    """{'has_audio': bool, 'width': int, 'height': int, 'fps': float}."""
    require_ffmpeg()
    r = _run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(mp4)])
    streams = json.loads(r.stdout).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": video.get("width"), "height": video.get("height"), "fps": fps,
    }
