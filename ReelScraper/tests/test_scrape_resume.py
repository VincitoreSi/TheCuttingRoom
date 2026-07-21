"""A resumed scrape must not destroy what an earlier scrape saved.

`save_outputs` rewrites reels_raw.json wholesale, and the resume logic deliberately skips
creators that are already in it. Those two facts together used to mean the accumulator was
seeded empty and then written over the top of everything: adding one handle to a watchlist
of five deleted the other four, and running the pipeline a second time — when there was
nothing new to fetch — wrote `{}` over the entire corpus and still logged DONE with rc 0.
Analyze then failed with "no scraped data — scrape first", so the one-click pipeline broke
on every other invocation and each recovery cost a full re-scrape of every creator.

Nothing here touches the network: the guest session and the per-creator fetch are both
stubbed, which is exactly the condition (`todo` empty, or only new handles fetched) under
which the corpus was being lost.
"""
import json
import sys
from pathlib import Path

import pytest

IG = Path(__file__).resolve().parents[1] / "platforms" / "instagram"


@pytest.fixture
def scrape(tmp_path, monkeypatch):
    """The instagram scraper, pointed at an isolated platform directory."""
    sys.path.insert(0, str(IG))
    import scrape as mod

    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod, "new_guest_session", lambda: True)
    monkeypatch.setattr(mod, "setup_logging", lambda *a, **k: None)
    # Nothing may sleep between creators in a test.
    monkeypatch.setattr(mod, "CREATOR_DELAY", (0, 0))
    mod.tmp_path = tmp_path
    return mod


def _raw(tmp_path):
    return json.loads((tmp_path / "reels_raw.json").read_text(encoding="utf-8"))


def _seed(tmp_path, handles, raw):
    (tmp_path / "pages.txt").write_text("\n".join(handles) + "\n", encoding="utf-8")
    (tmp_path / "reels_raw.json").write_text(json.dumps(raw), encoding="utf-8")


def _run(scrape, tmp_path, monkeypatch, fetch=None):
    monkeypatch.setattr(scrape, "scrape_creator", fetch or (lambda c, limit: (None, [])))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["scrape.py", "--file", str(tmp_path / "pages.txt")])
    scrape.main()


def test_a_scrape_with_nothing_to_do_leaves_the_corpus_alone(scrape, tmp_path, monkeypatch):
    """The exact second click on Run. Every watchlisted creator is already saved, so the
    fetch loop never runs — and the file must survive that."""
    before = {"creator_a": [{"id": 1, "code": "a1"}, {"id": 2, "code": "a2"}],
              "creator_b": [{"id": 3, "code": "b1"}]}
    _seed(tmp_path, ["creator_a", "creator_b"], before)

    _run(scrape, tmp_path, monkeypatch)

    assert _raw(tmp_path) == before


def test_adding_one_handle_keeps_the_creators_already_scraped(scrape, tmp_path, monkeypatch):
    """The watchlist grows; the corpus must grow with it, not collapse to the newcomer."""
    _seed(tmp_path, ["creator_a", "creator_b", "creator_new"],
          {"creator_a": [{"id": 1, "code": "a1"}], "creator_b": [{"id": 2, "code": "b1"}]})

    def fetch(c, limit):
        assert c == "creator_new", f"already-saved creator {c} should not be re-fetched"
        return {"followers": 10}, [{"id": 9, "code": "n1"}]

    _run(scrape, tmp_path, monkeypatch, fetch)

    raw = _raw(tmp_path)
    assert sorted(raw) == ["creator_a", "creator_b", "creator_new"]
    assert len(raw["creator_a"]) == 1 and len(raw["creator_b"]) == 1


def test_the_xlsx_rows_describe_the_whole_corpus_not_just_this_run(scrape, tmp_path, monkeypatch):
    """save_outputs rewrites the workbook wholesale too, so the rows handed to it have to
    cover every creator on disk — otherwise the report drops the resumed ones."""
    _seed(tmp_path, ["creator_a", "creator_b"], {"creator_a": [{"id": 1, "code": "a1"}]})
    monkeypatch.setattr(scrape, "scrape_creator",
                        lambda c, limit: ({"followers": 5}, [{"id": 2, "code": "b1"}]))

    _run(scrape, tmp_path, monkeypatch,
         fetch=lambda c, limit: ({"followers": 5}, [{"id": 2, "code": "b1"}]))

    rows, summary = scrape.rows_and_summary(_raw(tmp_path), {})
    assert len(rows) == 2
    assert dict(summary) == {"creator_a": 1, "creator_b": 1}


def test_an_unreadable_raw_file_does_not_abort_the_scrape(scrape, tmp_path, monkeypatch):
    """A dump truncated by an earlier crash must not stop a fresh run — it starts over
    rather than raising, which is the same call the count endpoint makes."""
    (tmp_path / "pages.txt").write_text("creator_a\n", encoding="utf-8")
    (tmp_path / "reels_raw.json").write_text('{"creator_a": [', encoding="utf-8")

    _run(scrape, tmp_path, monkeypatch,
         fetch=lambda c, limit: ({"followers": 1}, [{"id": 1, "code": "a1"}]))

    assert list(_raw(tmp_path)) == ["creator_a"]


def test_rows_and_summary_reads_followers_off_the_profile_meta(scrape):
    rows, summary = scrape.rows_and_summary(
        {"creator_a": [{"id": 1, "code": "a1"}]}, {"creator_a": {"followers": 1234}})

    assert rows[0]["creator_followers"] == 1234
    assert summary == [("creator_a", 1)]
