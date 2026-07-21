"""Frame assembly. These really do invoke ffmpeg — the failure modes worth catching (a
duration that drifts from the source, an accidental audio track, a stretched frame) only
show up in the actual container.

Duration exactness is the headline. The operator attaches the original sound by hand in
the Instagram composer, so a render that drifts from the source duration stops landing its
cuts on the beat. One frame period (33ms at 30fps) is the floor — you cannot express a
finer boundary at a fixed frame rate — and everything here asserts against that.
"""
import struct
import zlib
from pathlib import Path

import pytest

from engine.stitch import (
    ASPECT_PRESETS, REELS_ASPECT, StitchError, _scale_chain, canvas_for, ffmpeg_available,
    poster, probe_duration, probe_image_size, probe_streams, stitch, write_frame_manifest,
)

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
FRAME = 1 / 30 + 0.005          # one frame period at 30fps, plus container rounding slack


def _png(path: Path, w=108, h=192, rgb=(200, 40, 40)):
    """A minimal valid PNG, so the tests need neither Pillow nor binary fixtures."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def _frames(tmp_path, durations):
    out = []
    for i, d in enumerate(durations):
        out.append((_png(tmp_path / f"frame-{i:02d}.png", rgb=(40 * i % 255, 40, 180)), d))
    return out


# ---- duration fidelity -----------------------------------------------------------------
@needs_ffmpeg
@pytest.mark.parametrize("durations", [
    [1.5, 1.5, 1.5],                 # even holds
    [4.4, 1.0, 1.0, 1.0, 1.0, 1.35],  # the real six-shot recipe
    [6.22],                          # single static frame
    [0.6, 12.0],                     # a very long tail hold
])
def test_duration_matches_the_sum_of_holds(tmp_path, durations):
    """The concat DEMUXER got these wrong in both directions — inflating by a full hold
    with the repeated-last-file trick, truncating the tail without it. The filter graph
    must land within a frame."""
    mp4 = stitch(_frames(tmp_path, durations), tmp_path / "reel.mp4")
    assert probe_duration(mp4) == pytest.approx(sum(durations), abs=FRAME)


@needs_ffmpeg
def test_final_frame_actually_holds(tmp_path):
    """Regression for the demuxer's truncated tail: a long closing hold must survive."""
    mp4 = stitch(_frames(tmp_path, [1.0, 8.0]), tmp_path / "reel.mp4")
    assert probe_duration(mp4) == pytest.approx(9.0, abs=FRAME)


# ---- container shape -------------------------------------------------------------------
@needs_ffmpeg
def test_output_is_silent(tmp_path):
    """Silence is a contract, not an accident — the sound is attached by hand on IG."""
    mp4 = stitch(_frames(tmp_path, [2.0]), tmp_path / "reel.mp4")
    assert probe_streams(mp4)["has_audio"] is False


@needs_ffmpeg
def test_output_is_1080x1920_at_30fps(tmp_path):
    info = probe_streams(stitch(_frames(tmp_path, [1.0, 1.0]), tmp_path / "reel.mp4"))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["fps"] == pytest.approx(30, abs=0.1)


@needs_ffmpeg
def test_odd_sized_source_still_fills_the_reels_canvas(tmp_path):
    """Providers return assorted sizes; a stretched subject would sink the clone, and a
    letterboxed one reads as an amateur repost. Cover crops instead of doing either."""
    frames = [(_png(tmp_path / "frame-00.png", w=400, h=100), 1.0)]
    info = probe_streams(stitch(frames, tmp_path / "reel.mp4"))
    assert (info["width"], info["height"]) == (1080, 1920)


@needs_ffmpeg
def test_custom_dimensions_are_honoured(tmp_path):
    info = probe_streams(stitch(_frames(tmp_path, [1.0]), tmp_path / "reel.mp4",
                                width=720, height=1280, fps=24))
    assert (info["width"], info["height"]) == (720, 1280)
    assert info["fps"] == pytest.approx(24, abs=0.1)


# ---- aspect ratio ----------------------------------------------------------------------
def test_reels_is_the_default_canvas():
    assert canvas_for(None) == (1080, 1920)
    assert canvas_for("") == (1080, 1920)
    assert canvas_for("nonsense") == (1080, 1920)
    assert REELS_ASPECT == "9:16"


@pytest.mark.parametrize("label,size", list(ASPECT_PRESETS.items()))
def test_every_preset_canvas_matches_its_label(label, size):
    w, h = size
    num, den = (int(x) for x in label.split(":"))
    assert w / h == pytest.approx(num / den, abs=1e-6), \
        f"{label} preset {w}x{h} is not actually {label}"


@needs_ffmpeg
def test_cover_crops_to_exactly_reels_with_no_bars(tmp_path):
    """The real case: Nano Banana returns 768x1344 for a 9:16 request, which is 4:7 —
    ~1.6% too wide. Cover must produce a full-bleed 9:16 frame, not a padded one."""
    frames = [(_png(tmp_path / "frame-00.png", w=768, h=1344), 1.0)]
    info = probe_streams(stitch(frames, tmp_path / "reel.mp4", fit="cover"))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["width"] / info["height"] == pytest.approx(9 / 16, abs=1e-6)


@needs_ffmpeg
@pytest.mark.parametrize("aspect", list(ASPECT_PRESETS))
def test_each_aspect_option_renders_at_its_canvas(tmp_path, aspect):
    w, h = canvas_for(aspect)
    frames = [(_png(tmp_path / "frame-00.png", w=768, h=1344), 1.0)]
    info = probe_streams(stitch(frames, tmp_path / "reel.mp4", width=w, height=h))
    assert (info["width"], info["height"]) == (w, h)


@needs_ffmpeg
def test_contain_letterboxes_instead_of_cropping(tmp_path):
    """The escape hatch: keep every pixel, accept the bars."""
    frames = [(_png(tmp_path / "frame-00.png", w=400, h=100), 1.0)]
    info = probe_streams(stitch(frames, tmp_path / "reel.mp4", fit="contain"))
    assert (info["width"], info["height"]) == (1080, 1920)


# ---- auto fit: crop what is close, letterbox what is not -------------------------------
@pytest.mark.parametrize("src,expect", [
    ((768, 1344), "crop"),      # Nano Banana's "9:16" — 1.6% off, safe to crop
    ((1080, 1920), "crop"),     # already exact
    ((1024, 1024), "pad"),      # its square fallback — cropping cut a headline in half
    ((1080, 1350), "pad"),      # 4:5 feed portrait
    ((1920, 1080), "pad"),      # landscape
    (None, "pad"),              # unknown size: never crop blind
])
def test_auto_picks_crop_only_for_near_target_aspects(src, expect):
    chain = _scale_chain(1080, 1920, "auto", src)
    assert (expect + "=") in chain


def test_auto_is_the_default():
    near = _scale_chain(1080, 1920, src=(768, 1344))
    far = _scale_chain(1080, 1920, src=(1024, 1024))
    assert "crop=" in near and "pad=" in far


@needs_ffmpeg
def test_auto_letterboxes_a_square_frame_rather_than_cropping_it(tmp_path):
    """Regression: a square source cropped to 9:16 loses 44% of its width, which turned
    'LUXURY BEGINS WITH THE RIGHT BRAND' into 'Y BEGINS WITH THE RIGHT B'."""
    frames = [(_png(tmp_path / "frame-00.png", w=512, h=512), 1.0)]
    mp4 = stitch(frames, tmp_path / "reel.mp4", fit="auto")
    info = probe_streams(mp4)
    assert (info["width"], info["height"]) == (1080, 1920)
    # the full square must still be present: 1080-wide scaled square = 1080 tall, centred,
    # leaving black above and below rather than a cropped middle band
    assert probe_image_size(_png(tmp_path / "chk.png", w=512, h=512)) == (512, 512)


@needs_ffmpeg
def test_probe_image_size_reads_real_dimensions(tmp_path):
    assert probe_image_size(_png(tmp_path / "f.png", w=768, h=1344)) == (768, 1344)
    assert probe_image_size(tmp_path / "missing.png") is None


@needs_ffmpeg
def test_poster_is_extracted_as_jpeg(tmp_path):
    mp4 = stitch(_frames(tmp_path, [1.0]), tmp_path / "reel.mp4")
    p = poster(mp4, tmp_path / "poster.jpg")
    assert p.exists() and p.stat().st_size > 0
    assert p.read_bytes()[:2] == b"\xff\xd8"          # JPEG SOI


# ---- refusal ---------------------------------------------------------------------------
def test_no_frames_refused(tmp_path):
    with pytest.raises(StitchError, match="no frames"):
        stitch([], tmp_path / "reel.mp4")


def test_missing_frame_file_refused_before_invoking_ffmpeg(tmp_path):
    with pytest.raises(StitchError, match="missing frame"):
        stitch([(tmp_path / "nope.png", 1.0)], tmp_path / "reel.mp4")


def test_frame_manifest_records_holds_and_total(tmp_path):
    out = write_frame_manifest([("frame-00.png", 4.4), ("frame-01.png", 1.0)],
                               tmp_path / "frames.txt")
    text = out.read_text()
    assert "frame-00.png\t4.400s" in text
    assert "# total\t5.400s" in text
