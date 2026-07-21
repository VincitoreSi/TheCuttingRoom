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
