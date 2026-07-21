"""`POST /api/reference/{platform}` fetches a URL the caller supplies — the only place in
the hub that dereferences arbitrary input.

`urllib.request.urlopen` honours `file://` and `ftp://`, and whatever comes back is written
to `media/<platform>/ref_<hash>.mp4`, which the `/media` static mount then serves. Unguarded,
that is an arbitrary-local-file read with a download link attached, plus SSRF onto anything
the host can reach. The hub binds loopback only, but its threat model already treats an open
browser tab as inside the perimeter (see the CORS note and `_validate_render_cmd`), so
"local-only" is not the mitigation.

These tests assert the refusal happens BEFORE any fetch and BEFORE anything is registered.

Everything resolves `assert_fetchable_url` / `UnsafeFetchURL` off the LIVE module object
(`hub.mod`) rather than importing them at module scope. `conftest.hub` calls
`importlib.reload(api.app)`, which rebuilds every class in the module; a module-level
`from api.app import UnsafeFetchURL` binds the pre-reload class, so `pytest.raises` stops
matching what the reloaded code actually raises — the tests then pass in isolation and fail
as part of the suite.
"""
import socket

import pytest


# ---- the validator ---------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///Users/someone/.ssh/id_rsa",
    "ftp://example.com/x.mp4",
    "gopher://example.com/",
    "data:video/mp4;base64,AAAA",
    "/etc/passwd",                       # no scheme at all
    "",
    "   ",
    None,
])
def test_non_http_schemes_are_refused(hub, url):
    with pytest.raises(hub.mod.UnsafeFetchURL):
        hub.mod.assert_fetchable_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8787/api/platforms",       # the hub itself
    "http://localhost/x.mp4",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata service
    "http://10.0.0.5/x.mp4",
    "http://192.168.1.1/x.mp4",
    "http://172.16.0.1/x.mp4",
    "http://[::1]/x.mp4",
    "http://0.0.0.0/x.mp4",
])
def test_private_loopback_and_link_local_hosts_are_refused(hub, url):
    with pytest.raises(hub.mod.UnsafeFetchURL):
        hub.mod.assert_fetchable_url(url)


def _resolves_to(monkeypatch, hub, ip, port=80):
    monkeypatch.setattr(hub.mod.socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                          (ip, port))])


def test_a_hostname_resolving_to_loopback_is_refused(hub, monkeypatch):
    """DNS rebinding: a public-looking name that answers 127.0.0.1 is the standard way
    around a validator that only inspects the literal host string."""
    _resolves_to(monkeypatch, hub, "127.0.0.1")
    with pytest.raises(hub.mod.UnsafeFetchURL, match="non-public"):
        hub.mod.assert_fetchable_url("http://totally-legit.example/x.mp4")


def test_a_hostname_resolving_to_the_metadata_service_is_refused(hub, monkeypatch):
    _resolves_to(monkeypatch, hub, "169.254.169.254")
    with pytest.raises(hub.mod.UnsafeFetchURL, match="non-public"):
        hub.mod.assert_fetchable_url("http://totally-legit.example/x.mp4")


def test_a_public_url_passes(hub, monkeypatch):
    _resolves_to(monkeypatch, hub, "93.184.216.34", 443)
    assert hub.mod.assert_fetchable_url(" https://example.com/clip.mp4 ") == \
        "https://example.com/clip.mp4"


def test_an_unresolvable_host_is_refused(hub, monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(hub.mod.socket, "getaddrinfo", _boom)
    with pytest.raises(hub.mod.UnsafeFetchURL, match="could not resolve"):
        hub.mod.assert_fetchable_url("http://no-such-host.invalid/x.mp4")


# ---- the endpoint ----------------------------------------------------------------------
def test_endpoint_rejects_a_file_url_with_400(hub):
    r = hub.post("/api/reference/instagram", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400
    assert "http" in r.json()["detail"]


def test_endpoint_rejects_the_metadata_service(hub):
    r = hub.post("/api/reference/instagram",
                 json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400


def test_a_refused_url_leaves_no_registry_entry_and_no_file(hub):
    """A rejection must not half-register the reference — no `no_media` row, no stub file."""
    hub.post("/api/reference/instagram", json={"url": "file:///etc/passwd"})
    reg = hub.root / "references" / "instagram" / "registry.json"
    assert not reg.exists() or reg.read_text().strip() in ("", "{}")
    assert not list((hub.root / "media" / "instagram").glob("ref_*"))


def test_downloader_refuses_rather_than_writing_the_file(hub, tmp_path):
    """Defence in depth: the fetcher re-validates, so it is safe even if a future caller
    forgets to check first."""
    dest = tmp_path / "out.mp4"
    assert hub.mod._download_reference("file:///etc/passwd", dest) is False
    assert not dest.exists()
