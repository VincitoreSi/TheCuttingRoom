"""The media gate — download AND analyze only the selected tier.

Media used to download the top 60 clips by raw virality_score with a hardcoded cap and no
tier awareness at all, so a run that only cared about Viral clips still spent bandwidth (and,
downstream, PAID analysis credits) on 45 also-rans. The gate makes the selection a
user-configurable filter that keys on the SAME tiers the scoring engine uses, applied to
BOTH the media download and the AnalysisEngine pending queue.

These tests pin three halves of that: the pure selection filters before the cap, the media
stage forwards the configured gate to download_media, and the pending queue floors on the
configured tier so paid analysis never sees an unselected clip.
"""
import json

import pytest

import download_media
from core.virality import resolve_media_filter, tier_threshold


def _rows(n_viral, n_normal):
    """n_viral clips at score 90 (Viral), n_normal at score 40 (Normal). Each downloadable."""
    rows = []
    for i in range(n_viral):
        rows.append({"content_id": f"v{i}", "virality_score": 90.0, "tier": "Viral",
                     "media_url": f"http://x/v{i}.mp4"})
    for i in range(n_normal):
        rows.append({"content_id": f"n{i}", "virality_score": 40.0, "tier": "Normal",
                     "media_url": f"http://x/n{i}.mp4"})
    return rows


# ---------------------------------------------------------------- selection filters first

def test_selection_filters_by_min_score_before_the_cap():
    """15 Viral among 100 clips, gate at the Viral threshold -> exactly the 15 Viral, and the
    cap does NOT top the list back up to 60 with the 85 lower-tier clips it just excluded."""
    rows = _rows(15, 85)
    picked = download_media.select_rows(rows, min_score=85, top=60)
    assert [r["content_id"] for r in picked] == [f"v{i}" for i in range(15)]


def test_the_cap_still_applies_after_the_filter():
    """The gate never removes the cap. 100 Viral, gate at Viral, cap 60 -> 60."""
    rows = _rows(100, 0)
    assert len(download_media.select_rows(rows, min_score=85, top=60)) == 60


def test_no_gate_keeps_the_old_top_n_by_score_behaviour():
    """min_score absent = the pre-gate default: sort by score, slice to the cap."""
    rows = _rows(3, 3)
    picked = download_media.select_rows(rows, min_score=None, top=4)
    assert [r["content_id"] for r in picked] == ["v0", "v1", "v2", "n0"]


def test_rows_without_a_score_are_dropped():
    rows = _rows(2, 0) + [{"content_id": "x", "virality_score": None}]
    picked = download_media.select_rows(rows, min_score=None, top=10)
    assert "x" not in [r["content_id"] for r in picked]


# ---------------------------------------------------------------- tier -> threshold

def test_tier_threshold_reads_the_configured_tiers(tmp_path):
    cfg = tmp_path / "niche_config.json"
    cfg.write_text(json.dumps({"virality": {"tiers": [
        {"label": "Viral", "min_score": 85}, {"label": "High", "min_score": 70},
        {"label": "Normal", "min_score": 0}]}}), encoding="utf-8")
    from core.virality import load_config
    _, tiers, _ = load_config(cfg)
    assert tier_threshold(tiers, "Viral") == 85
    assert tier_threshold(tiers, "High") == 70
    assert tier_threshold(tiers, "nope") is None


def test_resolve_media_filter_maps_min_tier_to_a_score(tmp_path):
    cfg = tmp_path / "niche_config.json"
    cfg.write_text(json.dumps({"virality": {
        "tiers": [{"label": "Viral", "min_score": 85}, {"label": "High", "min_score": 70},
                  {"label": "Normal", "min_score": 0}],
        "media_filter": {"min_tier": "High", "max_downloads": 40}}}), encoding="utf-8")
    min_score, max_downloads = resolve_media_filter(cfg)
    assert min_score == 70
    assert max_downloads == 40


def test_an_explicit_min_score_overrides_the_tier(tmp_path):
    cfg = tmp_path / "niche_config.json"
    cfg.write_text(json.dumps({"virality": {
        "tiers": [{"label": "Viral", "min_score": 85}, {"label": "Normal", "min_score": 0}],
        "media_filter": {"min_tier": "Viral", "min_score": 60}}}), encoding="utf-8")
    min_score, _ = resolve_media_filter(cfg)
    assert min_score == 60


def test_no_media_filter_resolves_to_no_gate(tmp_path):
    cfg = tmp_path / "niche_config.json"
    cfg.write_text(json.dumps({"virality": {
        "tiers": [{"label": "Viral", "min_score": 85}]}}), encoding="utf-8")
    assert resolve_media_filter(cfg) == (None, None)


# ---------------------------------------------------------------- the media stage forwards it

def _write_cfg(hub, media_filter):
    (hub.root / "platforms" / "instagram" / "niche_config.json").write_text(json.dumps({
        "virality": {
            "tiers": [{"label": "Viral", "min_score": 85}, {"label": "High", "min_score": 70},
                      {"label": "Normal", "min_score": 0}],
            "media_filter": media_filter}}), encoding="utf-8")


def test_media_stage_forwards_the_resolved_gate(hub):
    """The manual media button and the cascade both go through STAGE_CMD['media']; it reads
    the platform's niche_config and forwards --min-score (tier-resolved) and --top."""
    _write_cfg(hub, {"min_tier": "Viral", "max_downloads": 30})
    cmd, cwd = hub.mod.STAGE_CMD["media"]("instagram")
    assert cmd[1:3] == ["download_media.py", "instagram"]
    assert "--min-score" in cmd
    assert cmd[cmd.index("--min-score") + 1] == "85"
    assert "--top" in cmd
    assert cmd[cmd.index("--top") + 1] == "30"


def test_media_stage_without_a_gate_forwards_no_flags(hub):
    """No media_filter configured = the pre-gate command, so nothing about existing runs
    changes until a user opts in."""
    (hub.root / "platforms" / "instagram" / "niche_config.json").write_text(
        json.dumps({"virality": {"tiers": [{"label": "Viral", "min_score": 85}]}}),
        encoding="utf-8")
    cmd, _ = hub.mod.STAGE_CMD["media"]("instagram")
    assert "--min-score" not in cmd
    assert "--top" not in cmd


# ---------------------------------------------------------------- the pending queue is gated

def _content(hub, rows):
    (hub.root / "platforms" / "instagram" / "content.json").write_text(
        json.dumps(rows), encoding="utf-8")


def test_pending_queue_floors_on_the_configured_tier(hub):
    """Paid analysis must only ever see selected-tier clips. A Normal clip that somehow has
    local media (a leftover from a looser earlier run) must not surface once the gate says
    Viral."""
    _write_cfg(hub, {"min_tier": "Viral"})
    _content(hub, [
        {"content_id": "v0", "virality_score": 90.0, "tier": "Viral"},
        {"content_id": "n0", "virality_score": 40.0, "tier": "Normal"},
    ])
    for cid in ("v0", "n0"):
        (hub.root / "media" / "instagram" / f"{cid}.mp4").write_bytes(b"x")

    got = hub.get("/api/analysis/instagram/pending").json()
    ids = [r["content_id"] for r in got]
    assert "v0" in ids
    assert "n0" not in ids


def test_an_explicit_pending_filter_still_overrides_the_gate(hub):
    """The gate is only the DEFAULT floor. A caller that passes its own min_score keeps the
    documented per-request control."""
    _write_cfg(hub, {"min_tier": "Viral"})
    _content(hub, [{"content_id": "n0", "virality_score": 40.0, "tier": "Normal"}])
    (hub.root / "media" / "instagram" / "n0.mp4").write_bytes(b"x")

    got = hub.get("/api/analysis/instagram/pending?min_score=10").json()
    assert [r["content_id"] for r in got] == ["n0"]
