"""The media stage's central-log events.

Media used to be the one pipeline stage the Activity feed never showed: the hub spawns it
blind, `download_media.py` logged only through `core/logsetup` (a file the hub never reads),
and `_record_job_outcome` pushes a log frame only for error/stopped — so a clean media run
was invisible on the Floor Log. These pin the contract that fixed it: `download_media` now
speaks the SAME lifecycle vocabulary the scraper does (run.start / item.* / run.end), so media
gets a live per-reel thread in the feed exactly like scrape. Mirrors test_scrape_events.py.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The six verbs the hub's board reducer understands, plus the one out-of-band progress verb.
BOARD_VERBS = {"run.start", "item.start", "item.stage", "item.done", "item.error", "run.end"}
# A literal stage key here would light the WRONG board node: liveStageIndex ORs `data.stage`
# against a pipeline stage key, so a lowercase "media" in a stage field points the Board at
# itself from the wrong axis.
PIPELINE_STAGE_KEYS = {"scrape", "analyze", "media", "propose", "auto-search", "analysis-engine"}


class _Capture:
    """Stands in for urlopen. Records payloads; can be told to fail."""

    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def __call__(self, req, timeout=None):
        if self.fail:
            raise OSError("hub unreachable")
        self.posts.append(json.loads(req.data.decode()))

        class _R:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
        return _R()


def _events_from(monkeypatch, tmp_path, platform, rows, fail_urls=frozenset()):
    """Drive download_media.main() with the network stubbed, and return what it POSTed."""
    mod = __import__("download_media")
    cap = _Capture()
    monkeypatch.setenv("BACKEND_API", "http://127.0.0.1:8787")
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", cap)

    def fake_get(url, dest):
        if url in fail_urls:
            raise OSError("expired CDN link")
        Path(dest).write_bytes(b"x")

    monkeypatch.setattr(mod, "_get", fake_get)
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    cj = tmp_path / "platforms" / platform / "content.json"
    cj.parent.mkdir(parents=True, exist_ok=True)
    cj.write_text(json.dumps(rows), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["download_media.py", platform])
    mod.main()
    return cap.posts, mod, tmp_path


def _row(cid, score=1.0, media=True, thumb=True):
    r = {"content_id": cid, "virality_score": score}
    if media:
        r["media_url"] = f"https://cdn.example/{cid}.mp4"
    if thumb:
        r["thumbnail_url"] = f"https://cdn.example/{cid}.jpg"
    return r


def test_media_emits_the_run_and_item_lifecycle(monkeypatch, tmp_path):
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", [_row("a"), _row("b")])

    kinds = [p["event"] for p in posts]
    assert kinds[0] == "run.start" and kinds[-1] == "run.end"
    assert "item.start" in kinds and "item.done" in kinds
    assert all(p["agent"] == "media" for p in posts)
    assert all(p["platform"] == "instagram" for p in posts)


def test_run_start_carries_the_planned_count(monkeypatch, tmp_path):
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram",
                                     [_row("a"), _row("b"), _row("c")])
    start = next(p for p in posts if p["event"] == "run.start")
    assert start["data"]["total"] == 3


def test_item_start_carries_the_total_so_the_client_ring_cannot_lose_it(monkeypatch, tmp_path):
    """run.start is the FIRST record evicted from the Dashboard's 300-event ring, so the total
    rides on every item.start too — mirrors the scraper's reason for the same field."""
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", [_row("a"), _row("b")])
    starts = [p for p in posts if p["event"] == "item.start"]
    assert starts and all(p["data"]["of"] == 2 for p in starts)


def test_one_item_start_and_one_terminal_event_per_downloaded_reel(monkeypatch, tmp_path):
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", [_row("a"), _row("b")])
    for cid in ("a", "b"):
        per = [p for p in posts if p.get("content_id") == cid]
        starts = [p for p in per if p["event"] == "item.start"]
        terminal = [p["event"] for p in per if p["event"] in ("item.done", "item.error")]
        assert len(starts) == 1
        assert terminal == ["item.done"]


def test_run_end_carries_downloaded_present_failed_totals(monkeypatch, tmp_path):
    # "a" downloads, "b" already present (mp4 on disk), "c" fails.
    out = tmp_path / "media" / "instagram"
    out.mkdir(parents=True)
    (out / "b.mp4").write_bytes(b"already")
    rows = [_row("a"), _row("b"), _row("c")]
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", rows,
                                     fail_urls={"https://cdn.example/c.mp4"})
    end = next(p for p in posts if p["event"] == "run.end")
    assert end["level"] == "info"
    assert end["data"]["downloaded"] == 1
    assert end["data"]["present"] == 1
    assert end["data"]["failed"] == 1


def test_a_failed_download_does_not_paint_the_thread_red(monkeypatch, tmp_path):
    """An expired CDN link is a WARNING that still exits 0, exactly like the scraper's
    unresolved creator — never item.error, which would falsely snap the whole media thread."""
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", [_row("a")],
                                     fail_urls={"https://cdn.example/a.mp4"})
    per = [p for p in posts if p.get("content_id") == "a"]
    done = next(p for p in per if p["event"] == "item.done")
    assert done["level"] == "warning" and done["data"]["ok"] is False
    assert not any(p["event"] == "item.error" for p in posts)


def test_no_event_claims_a_verb_or_a_stage_that_would_mislead_the_board(monkeypatch, tmp_path):
    posts, _mod, _out = _events_from(monkeypatch, tmp_path, "instagram", [_row("a"), _row("b")])
    for p in posts:
        assert p["event"] in BOARD_VERBS | {"item.progress"}, p["event"]
        stage = (p.get("data") or {}).get("stage")
        if stage is not None:
            assert stage not in PIPELINE_STAGE_KEYS, f"{stage!r} collides with a board node"


def test_media_still_downloads_the_files(monkeypatch, tmp_path):
    """The telemetry is bolted onto the existing download path, not a rewrite of it: the
    files must still land on disk."""
    _posts, _mod, out_root = _events_from(monkeypatch, tmp_path, "instagram", [_row("a")])
    assert (out_root / "media" / "instagram" / "a.mp4").exists()
    assert (out_root / "media" / "instagram" / "a.jpg").exists()
