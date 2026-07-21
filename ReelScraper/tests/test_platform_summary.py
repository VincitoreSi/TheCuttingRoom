"""What /api/platforms says about a platform mid-pipeline.

`scrape` and the board do not share a file. scrape writes `<content>_raw*.json`; only
`analyze` turns that into `content.json`, and `content.json` is the only thing the board
reads. So there is a real state — "250 reels are on disk, nothing has scored them yet" —
that used to be reported as `has_data: false, items: 0`, i.e. identical to a platform
nobody has ever scraped. A user who ran Scrape from the Dashboard, watched it finish, and
then found an empty board was told by every surface in the app that the scrape had found
nothing. It had found 250 reels.

These tests pin the distinction: `scraped` is about raw output existing, `has_data` is
about a scored corpus existing, and the gap between them is the analyze stage.
"""
import json


def _plat(client, platform="instagram"):
    return next(p for p in client.get("/api/platforms").json() if p["platform"] == platform)


def _raw(hub, name="reels_raw.json"):
    """What a finished scrape leaves behind. The per-platform names differ
    (reels_raw / posts_raw / shorts_raw) but all match the `*_raw*.json` convention."""
    (hub.root / "platforms" / "instagram" / name).write_text('{"someone": []}', encoding="utf-8")


def _corpus(hub, rows):
    (hub.root / "platforms" / "instagram" / "content.json").write_text(
        json.dumps(rows), encoding="utf-8")


def test_a_platform_nobody_has_scraped_is_empty_on_every_axis(hub):
    p = _plat(hub)

    assert p["scraped"] is False
    assert p["has_data"] is False
    assert p["items"] == 0


def test_a_scrape_with_no_analyze_reports_scraped_but_no_corpus(hub):
    """The state that made this confusing. Raw output exists; nothing has scored it."""
    _raw(hub)

    p = _plat(hub)

    assert p["scraped"] is True        # the scrape did happen, and did produce something
    assert p["has_data"] is False      # ...but there is still no scored corpus to show
    assert p["items"] == 0


def test_scraped_survives_a_hub_restart(hub):
    """It is read off the filesystem, not off the in-memory job ledger — which is empty
    after a restart, and was the only other way to know a scrape had ever run. This hub
    has run no jobs at all, so the ledger cannot be the source."""
    _raw(hub)

    assert hub.get("/api/pipeline/status").json() in ({}, {"jobs": {}}, {"jobs": []})
    assert _plat(hub)["scraped"] is True


def test_every_platforms_raw_naming_convention_counts(hub):
    """x writes posts_raw*.json and youtube shorts_raw*.json; the scraper may also shard
    into reels_raw_2.json. Matching `*_raw*.json` covers all of them — a check keyed to
    one platform's filename would report x and youtube as never scraped."""
    for name in ("posts_raw.json", "shorts_raw.json", "reels_raw_2.json"):
        d = hub.root / "platforms" / "instagram"
        for old in d.glob("*_raw*.json"):
            old.unlink()
        _raw(hub, name)

        assert _plat(hub)["scraped"] is True, name


def _pages(hub, text):
    (hub.root / "platforms" / "instagram" / "pages.txt").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- counts the Board needs

def test_watchlist_counts_pages_not_corpus_creators(hub):
    """The Sources node used to show `creators`, which counts distinct creators in the
    SCORED corpus — so a handle added a second ago read as "0 pages" until scrape AND
    analyze had both run. `watchlist` answers the question actually being asked."""
    _pages(hub, "# a comment\n\nexample_one\nexample_two\n")

    p = _plat(hub)

    assert p["watchlist"] == 2      # comments and blank lines are not handles
    assert p["creators"] == 0       # ...and no corpus exists yet, which is fine


def test_scraped_items_counts_reels_on_disk_before_anything_scores_them(hub):
    _raw(hub)   # {"someone": []}
    assert _plat(hub)["scraped_items"] == 0

    (hub.root / "platforms" / "instagram" / "reels_raw.json").write_text(
        json.dumps({"a": [1, 2, 3], "b": [4, 5]}), encoding="utf-8")

    p = _plat(hub)
    assert p["scraped_items"] == 5
    assert p["items"] == 0          # still nothing scored — the two are different numbers


def test_a_half_written_raw_dump_does_not_break_the_endpoint(hub):
    """A live scrape rewrites the dump in place; the Dashboard polls this endpoint the
    whole time. Returning 0 for an unparseable file beats 500-ing the entire board."""
    (hub.root / "platforms" / "instagram" / "reels_raw.json").write_text(
        '{"a": [1, 2', encoding="utf-8")

    assert _plat(hub)["scraped_items"] == 0


def test_the_reel_count_is_recomputed_when_the_dump_changes(hub):
    """It is cached on (mtime, size) to keep a multi-MB parse off every poll — so the
    cache key has to actually notice a rewrite."""
    f = hub.root / "platforms" / "instagram" / "reels_raw.json"
    f.write_text(json.dumps({"a": [1, 2]}), encoding="utf-8")
    assert _plat(hub)["scraped_items"] == 2

    f.write_text(json.dumps({"a": [1, 2, 3, 4]}), encoding="utf-8")

    assert _plat(hub)["scraped_items"] == 4


# ---------------------------------------------------------------- guard rails

def test_nothing_is_runnable_on_an_empty_install_except_discovery(hub):
    r = _plat(hub)["readiness"]

    assert r["scrape"]["ready"] is False
    assert "watchlist" in r["scrape"]["reason"].lower()
    assert r["scrape"]["blocked_by"] is None      # running a stage cannot add a handle
    assert r["analyze"]["ready"] is False
    assert r["media"]["ready"] is False
    assert r["auto-search"]["ready"] is True      # discovery has no corpus precondition


def test_each_blocked_stage_names_the_stage_that_unblocks_it(hub):
    """The one-click fix. Following blocked_by has to terminate, not cycle."""
    _pages(hub, "example_one\n")

    r = _plat(hub)["readiness"]

    assert r["scrape"]["ready"] is True           # a handle is all scrape needs
    assert r["analyze"]["blocked_by"] == "scrape"
    assert r["media"]["blocked_by"] == "analyze"
    assert r["analysis-engine"]["blocked_by"] == "media"


def test_readiness_advances_one_stage_at_a_time(hub):
    _pages(hub, "example_one\n")
    _raw(hub)
    assert _plat(hub)["readiness"]["analyze"]["ready"] is True
    assert _plat(hub)["readiness"]["media"]["ready"] is False

    _corpus(hub, [{"content_id": "1_2", "creator": "example_creator"}])

    assert _plat(hub)["readiness"]["media"]["ready"] is True


def test_a_reference_clip_does_not_make_the_blueprint_stage_look_ready(hub):
    """ref_*.mp4 are operator-supplied reference videos, not corpus media. The blueprint
    stage works off the corpus, so they must not satisfy its media precondition."""
    media = hub.root / "media" / "instagram"
    media.mkdir(parents=True, exist_ok=True)
    (media / "ref_deadbeef.mp4").write_bytes(b"")

    assert _plat(hub)["readiness"]["analysis-engine"]["blocked_by"] == "media"

    (media / "123_456.mp4").write_bytes(b"")

    # now media is satisfied, so the block moves on to the key (absent in tests)
    assert _plat(hub)["readiness"]["analysis-engine"]["blocked_by"] != "media"


def test_a_missing_api_key_blocks_the_blueprint_stage_and_names_the_variable(hub, monkeypatch):
    """No stage can fix this one, so blocked_by stays None and the reason has to tell a
    human exactly what to set and where."""
    for v in hub.mod.GEMINI_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(hub.mod, "ROOT", hub.root)   # sibling .env lookup lands in tmp
    media = hub.root / "media" / "instagram"
    media.mkdir(parents=True, exist_ok=True)
    (media / "123_456.mp4").write_bytes(b"")

    s = _plat(hub)["readiness"]["analysis-engine"]

    assert s["ready"] is False
    assert s["blocked_by"] is None
    assert "GEMINI_API_KEY" in s["reason"]


def test_key_presence_never_leaks_the_value(hub, monkeypatch):
    """Presence only — the same contract as /api/config/agent/{a}/secrets/status."""
    monkeypatch.setenv("GEMINI_API_KEY", "sk-not-a-real-key-9999")

    body = hub.get("/api/platforms").text

    assert "sk-not-a-real-key-9999" not in body


def test_an_analyzed_corpus_reports_both(hub):
    _raw(hub)
    _corpus(hub, [{"content_id": "1_2", "creator": "example_creator", "tier": "Viral"},
                  {"content_id": "3_4", "creator": "example_creator", "tier": "Strong"}])

    p = _plat(hub)

    assert p["scraped"] is True
    assert p["has_data"] is True
    assert p["items"] == 2
    assert p["creators"] == 1
    assert p["viral"] == 1
