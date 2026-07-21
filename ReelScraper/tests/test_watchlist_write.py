"""Editing the watchlist must not eat the file it lives in.

GET /api/config strips comments and blank lines before handing `pages` to the Dashboard.
PUT then wrote `"\\n".join(pages)` straight back — so the very first "add a handle" from
the UI silently deleted every comment in pages.txt. The file ships from pages.txt.example
as annotated prose explaining what belongs in it, which made this a new user's first
irreversible action.
"""


def _pages_file(hub):
    return hub.root / "platforms" / "instagram" / "pages.txt"


def _put(hub, pages):
    r = hub.put("/api/config/instagram", json={"pages": pages})
    assert r.status_code == 200, r.text
    return _pages_file(hub).read_text(encoding="utf-8")


ANNOTATED = """\
# One handle per line. Lines starting with # are ignored.
# Instagram scraping is guest-mode only — never add a login cookie.

example_one
example_two
"""


def test_comments_survive_a_watchlist_edit(hub):
    _pages_file(hub).write_text(ANNOTATED, encoding="utf-8")

    text = _put(hub, ["example_one", "example_two", "example_three"])

    assert "# One handle per line" in text
    assert "guest-mode only" in text


def test_a_removed_handle_goes_and_the_rest_keep_their_places(hub):
    _pages_file(hub).write_text(ANNOTATED, encoding="utf-8")

    text = _put(hub, ["example_two"])

    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    assert lines == ["example_two"]
    assert "# One handle per line" in text


def test_a_new_handle_is_appended_not_reordered(hub):
    _pages_file(hub).write_text(ANNOTATED, encoding="utf-8")

    text = _put(hub, ["example_three", "example_one", "example_two"])

    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    # the two that already existed hold their original positions; only the newcomer moves
    assert lines == ["example_one", "example_two", "example_three"]


def test_writing_to_a_platform_with_no_pages_file_yet_just_works(hub):
    text = _put(hub, ["example_one"])

    assert text == "example_one\n"


def test_duplicates_in_the_request_are_collapsed(hub):
    text = _put(hub, ["example_one", "example_one", "example_two"])

    assert [l for l in text.splitlines() if l.strip()] == ["example_one", "example_two"]


def test_the_watchlist_count_reflects_the_edit_immediately(hub):
    """The Board reads this number; it is the whole point of the round trip."""
    _put(hub, ["example_one", "example_two"])

    p = next(x for x in hub.get("/api/platforms").json() if x["platform"] == "instagram")
    assert p["watchlist"] == 2
    assert p["readiness"]["scrape"]["ready"] is True


# ---------------------------------------------------------------------------------------
# pages.txt accepts three spellings of one creator — `handle`, `@handle`, and the profile
# URL — and the scrapers have always collapsed them before fetching. The hub did not: it
# compared raw strings, so approving a discovery candidate for a creator a human had
# already typed in by hand appended them a SECOND time. The scrape deduped and pulled that
# creator once while the Board counted two pages.

import pytest

from api.app import _norm_page_handle


@pytest.mark.parametrize("line,expected", [
    ("uptown_jor", "uptown_jor"),
    ("@uptown_jor", "uptown_jor"),
    ("https://www.instagram.com/uptown_jor", "uptown_jor"),
    ("https://www.instagram.com/uptown_jor/", "uptown_jor"),
    ("http://instagram.com/uptown_jor/reels/", "uptown_jor"),
    ("https://www.instagram.com/uptown_jor?hl=en", "uptown_jor"),
    ("  https://www.instagram.com/uptown_jor  ", "uptown_jor"),
    ("https://x.com/jack", "jack"),
    ("https://www.youtube.com/@MrBeast", "MrBeast"),
    # channel/ and user/ introduce the id rather than being it
    ("https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA", "UCX6OQ3DkcsbYNE6H8uQQuVA"),
    ("UCX6OQ3DkcsbYNE6H8uQQuVA", "UCX6OQ3DkcsbYNE6H8uQQuVA"),
    ("", ""),
])
def test_every_spelling_of_a_creator_normalizes_to_one_identity(line, expected):
    assert _norm_page_handle(line) == expected


def test_case_is_not_folded():
    """Instagram handles are case-insensitive; YouTube channel ids are NOT. Folding case
    here would merge two genuinely different channels into one."""
    assert _norm_page_handle("UCabcDEF") != _norm_page_handle("ucABCdef")


def test_discovery_will_not_re_add_a_handle_typed_by_hand(hub):
    """The reproduction: a human types the bare handle, AutoSearch approves the URL form."""
    from api.app import _append_handle_to_pages, _watchlist

    _pages_file(hub).write_text("# my watchlist\nuptown_jor\n", encoding="utf-8")
    assert _append_handle_to_pages("instagram", "https://www.instagram.com/uptown_jor") is False
    assert len(_watchlist("instagram")) == 1

    assert _append_handle_to_pages("instagram", "https://www.instagram.com/someone_new") is True
    assert len(_watchlist("instagram")) == 2


def test_the_count_matches_what_a_scrape_will_fetch(hub):
    """A file that already carries both spellings must still count one creator — otherwise
    the Sources card describes the file rather than the run."""
    from api.app import _watchlist

    _pages_file(hub).write_text(
        "# comment\nuptown_jor\nhttps://www.instagram.com/uptown_jor/\n@uptown_jor\nother\n",
        encoding="utf-8",
    )
    assert len(_watchlist("instagram")) == 2
