"""The render store: producer-generated media, structurally separated from the corpus.

The separation is the point of most of these tests. Generated reels were once written
straight into `media/<platform>/<content_id>.mp4`, which made the corpus serve our own
output under real creators' ids with metrics that no longer described the video. Nothing
here may make that reachable again.
"""
import base64

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
MP4 = base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256).decode()


def _post(hub, name, **over):
    body = {"file": name, "agent": "similar-content", "kind": "slideshow",
            "caption": "a caption", "duration_s": 9.75, "width": 1080, "height": 1920,
            "assets": [{"name": "reel.mp4", "content_b64": MP4},
                       {"name": "poster.jpg", "content_b64": PNG}]}
    body.update(over)
    return hub.post("/api/renders/instagram", json=body)


def test_round_trip(hub, approved_item):
    r = _post(hub, approved_item)
    assert r.status_code == 200, r.text
    rec = r.json()

    assert rec["render_id"] == approved_item[:-3]          # derived from the .md stem
    assert rec["video_url"].startswith(f"/renders/instagram/{rec['render_id']}/reel.mp4")
    assert rec["caption"] == "a caption"
    assert rec["local_path"].endswith("reel.mp4")
    assert rec["bytes"] > 0

    on_disk = hub.root / "renders" / "instagram" / rec["render_id"] / "reel.mp4"
    assert on_disk.read_bytes() == base64.b64decode(MP4)
    assert hub.get("/api/renders/instagram").json()[0]["render_id"] == rec["render_id"]


def test_render_id_is_server_derived_not_client_supplied(hub, approved_item):
    # a client-supplied render_id must be ignored entirely
    rec = _post(hub, approved_item, render_id="../../escape").json()
    assert rec["render_id"] == approved_item[:-3]
    assert (hub.root / "renders" / "instagram" / rec["render_id"]).is_dir()


def test_rerender_overwrites_in_place_and_busts_cache(hub, approved_item):
    first = _post(hub, approved_item).json()
    second = _post(hub, approved_item, caption="v2").json()

    assert second["render_id"] == first["render_id"]        # one item, one dir
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] > first["updated_at"]
    assert second["caption"] == "v2"
    assert second["video_url"] != first["video_url"]        # ?v= changed
    dirs = list((hub.root / "renders" / "instagram").iterdir())
    assert len(dirs) == 1


def test_unapproved_item_is_still_uploadable_but_missing_item_is_not(hub):
    # the store keys on an existing studio item; a bare filename is not enough
    r = _post(hub, "does-not-exist.md")
    assert r.status_code == 404


def test_unknown_platform_rejected(hub, approved_item):
    r = hub.post("/api/renders/atlantis",
                 json={"file": approved_item, "agent": "x",
                       "assets": [{"name": "reel.mp4", "content_b64": MP4}]})
    assert r.status_code == 404


def test_bad_kind_rejected(hub, approved_item):
    assert _post(hub, approved_item, kind="hologram").status_code == 400


def test_illegal_asset_names_rejected(hub, approved_item):
    for bad in ["../escape.mp4", "/etc/passwd.mp4", "reel.sh", "reel.mp4.exe", "a/b.mp4"]:
        r = _post(hub, approved_item,
                  assets=[{"name": bad, "content_b64": MP4}])
        assert r.status_code == 400, f"{bad!r} should have been refused"


def test_corpus_shaped_asset_name_refused(hub, approved_item):
    """The exact shape that corrupted the corpus: a scraped content_id filename.

    Synthetic digits — a real Instagram content_id is `<media_pk>_<user_pk>` and its trailing
    half permanently identifies an account, so one must never be committed. Only the SHAPE
    matters here; it is what CORPUS_NAME_RE matches on."""
    r = _post(hub, approved_item,
              assets=[{"name": "1234567890123456789_12345678901.mp4", "content_b64": MP4}])
    assert r.status_code == 400
    assert "corpus" in r.json()["detail"].lower()


def test_oversized_payload_rejected(hub, approved_item, monkeypatch):
    monkeypatch.setattr(hub.mod, "MAX_RENDER_BYTES", 32)
    assert _post(hub, approved_item).status_code == 413


def test_invalid_base64_rejected(hub, approved_item):
    r = _post(hub, approved_item,
              assets=[{"name": "reel.mp4", "content_b64": "not!valid!base64"}])
    assert r.status_code == 400


def test_render_never_lands_in_corpus_media(hub, approved_item):
    before = sorted(p.name for p in (hub.root / "media" / "instagram").iterdir())
    _post(hub, approved_item)
    after = sorted(p.name for p in (hub.root / "media" / "instagram").iterdir())
    assert before == after, "a render must never write into the corpus media namespace"


def test_delete_and_filters(hub, approved_item):
    rec = _post(hub, approved_item).json()
    assert len(hub.get("/api/renders/instagram", params={"file": approved_item}).json()) == 1
    assert hub.get("/api/renders/instagram", params={"agent": "nobody"}).json() == []

    assert hub.delete(f"/api/renders/instagram/{rec['render_id']}").status_code == 200
    assert hub.get("/api/renders/instagram").json() == []
    assert not (hub.root / "renders" / "instagram" / rec["render_id"]).exists()
    assert hub.delete(f"/api/renders/instagram/{rec['render_id']}").status_code == 404


def test_index_rebuilds_from_render_json(hub, approved_item):
    """index.json is a derived cache — render.json files are the source of truth."""
    rec = _post(hub, approved_item).json()
    (hub.root / "renders" / "index.json").write_text("{}")

    rebuilt = hub.mod._rebuild_render_index()
    assert f"instagram/{rec['render_id']}" in rebuilt
