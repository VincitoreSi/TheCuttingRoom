"""Favicon serving.

Browsers request /favicon.ico on their own regardless of what the document declares, and a
404 for it is cached per-origin — so one bad load can leave a tab iconless afterwards.
Safari is the reason a raster icon exists at all: it does not reliably render an SVG
favicon, so shipping only favicon.svg meant "works in Chrome, blank in Safari".
"""
import struct


def _dist(hub):
    d = hub.root / "frontend" / "dist"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_serves_a_real_ico_when_the_build_has_one(hub):
    ico = _dist(hub) / "favicon.ico"
    ico.write_bytes(struct.pack("<HHH", 0, 1, 1) + b"\x00" * 40)

    r = hub.get("/favicon.ico")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    assert r.content.startswith(struct.pack("<HHH", 0, 1, 1))


def test_falls_back_to_the_svg_for_an_older_build(hub):
    """A dist built before the raster icons existed should still get an icon."""
    (_dist(hub) / "favicon.svg").write_text("<svg/>")

    r = hub.get("/favicon.ico", follow_redirects=False)

    assert r.status_code == 308
    assert r.headers["location"] == "/favicon.svg"


def test_404s_cleanly_when_the_frontend_is_not_built(hub):
    r = hub.get("/favicon.ico")
    assert r.status_code == 404


def test_the_ico_route_wins_over_the_spa_mount(hub):
    """Registration order matters: the '/' StaticFiles mount swallows everything under it,
    so the route has to be declared first or it never runs."""
    paths = [getattr(r, "path", None) for r in hub.mod.app.routes]
    assert "/favicon.ico" in paths
    mounts = [i for i, r in enumerate(hub.mod.app.routes)
              if r.__class__.__name__ == "Mount" and getattr(r, "path", "") == ""]
    if mounts:
        assert paths.index("/favicon.ico") < min(mounts)
