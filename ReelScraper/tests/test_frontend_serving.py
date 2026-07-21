"""Serving the SPA shell, and what happens before it exists.

The hub used to decide ONCE at import whether frontend/dist existed: if it did not, "/"
was bound to a static placeholder for the process's whole life. Anyone who started the hub
before building the Dashboard — or who rebuilt it underneath a running hub — got a page
that said nothing useful and never changed, no matter how long they waited or how many
times they reloaded. The only cure was a restart nobody knew to perform.

So the contract these tests pin down is: the decision is made per REQUEST, and the page
shown in the meantime tells you what is happening and recovers on its own.
"""


def _dist(hub):
    d = hub.root / "frontend" / "dist"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_serves_the_building_page_when_there_is_no_build(hub):
    r = hub.get("/")

    assert r.status_code == 503                      # honest: not ready, not "not found"
    assert "Building the dashboard" in r.text


def test_the_building_page_reloads_itself(hub):
    """Without this it is just a nicer-looking dead end."""
    r = hub.get("/")

    assert "location.reload()" in r.text


def test_the_building_page_needs_nothing_from_the_build(hub):
    """It is shown precisely when /assets does not exist, so it cannot reference it."""
    r = hub.get("/")

    assert "/assets/" not in r.text


def test_serves_index_once_the_build_lands_without_a_restart(hub):
    """The whole point: same process, no reload of the app module."""
    assert hub.get("/").status_code == 503

    (_dist(hub) / "index.html").write_text("<!doctype html><title>Cutting Room</title>")

    r = hub.get("/")
    assert r.status_code == 200
    assert "Cutting Room" in r.text


def test_the_shell_is_never_cached(hub):
    """A rebuild renames the hashed assets index.html points at; a cached shell would ask
    for the previous build's files and 404 on every one of them."""
    (_dist(hub) / "index.html").write_text("<!doctype html>")

    r = hub.get("/")

    assert r.headers["cache-control"] == "no-store"


def test_the_api_keeps_working_while_the_frontend_is_missing(hub):
    """The build being absent says nothing about the API's health."""
    assert hub.get("/").status_code == 503

    assert hub.get("/api/platforms").status_code == 200
