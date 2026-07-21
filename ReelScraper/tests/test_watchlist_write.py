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
