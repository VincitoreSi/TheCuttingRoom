"""GET /api/hub — the endpoint that makes two clones on one machine safe.

Two failures it exists to catch, both silent without it:

  1. A hub process that outlived a `git pull`. Python imports a module once and serves it
     from memory, so the old API keeps answering while the Dashboard on disk is new. The
     Board rendered the literal string "undefined pages" — a symptom that points nowhere
     near the cause.
  2. An agent aimed at the WRONG CLONE's hub. `BACKEND_API` is the only thing joining them,
     and a copied .env sends this niche's proposals into that niche's studio. Every call
     returns 200.
"""
import json
import os
import time

from fastapi.testclient import TestClient

from api import app as appmod


client = TestClient(appmod.app)


def test_reports_its_own_root():
    """The field agents compare against to know whose hub they are talking to."""
    body = client.get("/api/hub").json()
    assert body["root"] == str(appmod.ROOT)
    assert body["root"].endswith("ReelScraper")


def test_fresh_process_is_not_stale():
    body = client.get("/api/hub").json()
    assert body["stale"] is False
    assert body["source_mtime"] == body["source_mtime_now"]


def test_goes_stale_when_a_source_file_changes_under_the_running_process(monkeypatch):
    """The whole point: the tree moved, this process did not."""
    later = appmod.SOURCE_MTIME_AT_START + 3600
    monkeypatch.setattr(appmod, "_source_mtime", lambda: later)
    body = client.get("/api/hub").json()
    assert body["stale"] is True
    assert body["source_mtime_now"] == later
    # The value captured at import must NOT move — it is the record of what was loaded.
    assert body["source_mtime"] == appmod.SOURCE_MTIME_AT_START


def test_one_second_of_tolerance(monkeypatch):
    """A checkout can land files in the same second the hub imports them. Restarting on
    that would be a loop, and a one-second blind spot is the cheaper mistake."""
    for delta, expected in ((0.0, False), (0.5, False), (1.0, False), (2.0, True)):
        monkeypatch.setattr(appmod, "_source_mtime",
                            lambda d=delta: appmod.SOURCE_MTIME_AT_START + d)
        assert client.get("/api/hub").json()["stale"] is expected, delta


def test_source_mtime_tracks_the_files_it_claims_to(tmp_path, monkeypatch):
    """Touching a hub source moves the stamp; touching something else must not.

    Platform scrapers are deliberately excluded: they run as subprocesses and are re-read
    every time, so a change there needs no restart and must not read as skew.
    """
    before = appmod._source_mtime()
    scraper = appmod.pdir("instagram") / "scrape.py"
    if scraper.exists():
        scraper.touch()
        assert appmod._source_mtime() == before, "a scraper edit is not hub skew"

    target = appmod.ROOT / "api" / "app.py"
    stat_before = target.stat()
    try:
        os.utime(target, (stat_before.st_atime, time.time() + 60))
        assert appmod._source_mtime() > before
    finally:
        os.utime(target, (stat_before.st_atime, stat_before.st_mtime))


def test_reports_the_niche(tmp_path, monkeypatch):
    """What the Dashboard puts in the chrome so two identical boards are tellable apart."""
    body = client.get("/api/hub").json()
    # The real tree ships Fashion; assert on the contract, not the value.
    assert body["niche"] is None or isinstance(body["niche"], str)

    fake = tmp_path / "platforms"
    (fake / "instagram").mkdir(parents=True)
    (fake / "instagram" / "niche_config.json").write_text(json.dumps({"niche": "Fitness"}))
    monkeypatch.setattr(appmod, "pdir", lambda p: fake / p)
    monkeypatch.setattr(appmod, "PLATFORMS", ["instagram"])
    assert appmod._checkout_niche() == "Fitness"


def test_niche_survives_an_unreadable_config(tmp_path, monkeypatch):
    """A half-written or missing config must not 500 an endpoint agents call on startup."""
    fake = tmp_path / "platforms"
    (fake / "instagram").mkdir(parents=True)
    (fake / "instagram" / "niche_config.json").write_text("{ not json")
    monkeypatch.setattr(appmod, "pdir", lambda p: fake / p)
    monkeypatch.setattr(appmod, "PLATFORMS", ["instagram", "x"])
    assert appmod._checkout_niche() is None
    assert client.get("/api/hub").status_code == 200


def test_distinct_niches_are_all_reported(tmp_path, monkeypatch):
    """A half-converted clone is worth seeing, not smoothing over."""
    fake = tmp_path / "platforms"
    for name, niche in (("instagram", "Fashion"), ("x", "Fitness"), ("youtube", "Fashion")):
        (fake / name).mkdir(parents=True)
        (fake / name / "niche_config.json").write_text(json.dumps({"niche": niche}))
    monkeypatch.setattr(appmod, "pdir", lambda p: fake / p)
    monkeypatch.setattr(appmod, "PLATFORMS", ["instagram", "x", "youtube"])
    assert appmod._checkout_niche() == "Fashion · Fitness"
