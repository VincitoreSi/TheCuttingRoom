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


def test_the_building_page_links_to_the_published_documentation(hub):
    """It used to link to /docs — the hub's own Swagger UI, which answers "what endpoints
    exist", not the "how do I start this thing" question of someone waiting on a first build.
    The address is read from mkdocs.yml so there is one declaration of it, not two."""
    r = hub.get("/")

    assert hub.mod.DOCS_URL, "documentation/mkdocs.yml should declare a resolved site_url"
    assert f'href="{hub.mod.DOCS_URL}"' in r.text
    assert 'href="/docs"' not in r.text


def test_the_docs_url_comes_from_mkdocs(hub, tmp_path):
    cfg = tmp_path / "mkdocs.yml"
    cfg.write_text("site_name: X\nsite_url: https://example.github.io/Repo/\n")

    assert hub.mod._published_docs_url(cfg) == "https://example.github.io/Repo/"


def test_an_unpersonalised_fork_gets_no_docs_link(hub, tmp_path):
    """mkdocs.yml ships the literal token GITHUB_USER until scripts/apply-identity.sh
    rewrites it, and https://GITHUB_USER.github.io/ resolves nowhere. A missing link is a
    smaller failure than one that looks live and 404s."""
    cfg = tmp_path / "mkdocs.yml"
    cfg.write_text("site_url: https://GITHUB_USER.github.io/TheCuttingRoom/\n")

    assert hub.mod._published_docs_url(cfg) is None
    assert hub.mod._published_docs_url(tmp_path / "absent.yml") is None


def test_the_api_keeps_working_while_the_frontend_is_missing(hub):
    """The build being absent says nothing about the API's health."""
    assert hub.get("/").status_code == 503

    assert hub.get("/api/platforms").status_code == 200
