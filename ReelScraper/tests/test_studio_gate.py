"""The human gate must survive a producer re-POSTing its own markdown.

This is a regression suite for a bug that silently destroyed five real approvals:
`save_proposal` did `status = body.status or "proposed"`, so any re-POST of an existing
filename reset that item's gate state. `scripts/capture-demo.py` re-POSTs the same
filenames on every run, and the render step re-POSTs to stamp rendered-media info — so
an approval had a short and unpredictable life.
"""


def test_reposting_an_approved_item_keeps_it_approved(hub):
    name = "2026-07-19-similar-clip-1.md"
    hub.post("/api/studio/instagram",
             json={"filename": name, "text": "v1", "agent": "similar-content", "kind": "clone"})
    hub.post(f"/api/studio/instagram/{name}/status", json={"status": "approved"})

    # a producer re-POSTs the same item with no explicit status (the capture-demo.py case)
    r = hub.post("/api/studio/instagram",
                 json={"filename": name, "text": "v2 — with rendered media",
                       "agent": "similar-content", "kind": "clone"})

    assert r.json()["status"] == "approved"
    assert hub.get(f"/api/studio/instagram/{name}").json()["status"] == "approved"
    assert hub.get(f"/api/studio/instagram/{name}").json()["text"] == "v2 — with rendered media"


def test_explicit_status_still_wins(hub):
    name = "2026-07-19-similar-clip-2.md"
    hub.post("/api/studio/instagram", json={"filename": name, "text": "v1"})
    hub.post(f"/api/studio/instagram/{name}/status", json={"status": "approved"})

    r = hub.post("/api/studio/instagram",
                 json={"filename": name, "text": "v2", "status": "rejected"})
    assert r.json()["status"] == "rejected"


def test_first_insert_defaults_to_proposed(hub):
    r = hub.post("/api/studio/instagram", json={"filename": "fresh.md", "text": "x"})
    assert r.json()["status"] == "proposed"


def test_repost_preserves_agent_and_created_at(hub):
    name = "keeps-provenance.md"
    first = hub.post("/api/studio/instagram",
                     json={"filename": name, "text": "v1",
                           "agent": "similar-content", "kind": "clone"}).json()
    created = hub.get(f"/api/studio/instagram/{name}").json()["created_at"]

    hub.post("/api/studio/instagram", json={"filename": name, "text": "v2"})
    after = hub.get(f"/api/studio/instagram/{name}").json()

    assert first["file"] == name
    assert after["agent"] == "similar-content"       # not clobbered to null
    assert after["kind"] == "clone"
    assert after["created_at"] == created


def test_filename_traversal_is_sanitized(hub):
    r = hub.post("/api/studio/instagram",
                 json={"filename": "../../../etc/passwd", "text": "x"})
    assert r.status_code == 200
    name = r.json()["file"]
    assert "/" not in name and ".." not in name
    assert (hub.root / "studio" / "instagram" / name).exists()
    assert not (hub.root.parent / "etc" / "passwd").exists()
