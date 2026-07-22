"""The human gate must survive a producer re-POSTing its own markdown.

This is a regression suite for a bug that silently destroyed five real approvals:
`save_proposal` did `status = body.status or "proposed"`, so any re-POST of an existing
filename reset that item's gate state. `scripts/capture-demo.py` re-POSTs the same
filenames on every run, and the render step re-POSTs to stamp rendered-media info — so
an approval had a short and unpredictable life.
"""
import json

import pytest


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


# ---------------------------------------------------------------- removing a rejected card

def _rejected(hub, name):
    hub.post("/api/studio/instagram",
             json={"filename": name, "text": "x", "agent": "similar-content", "kind": "clone"})
    hub.post(f"/api/studio/instagram/{name}/status",
             json={"status": "rejected", "note": "off-brand"})
    return name


def test_a_rejected_item_can_be_removed(hub):
    name = _rejected(hub, "2026-07-22-similar-drop-me.md")

    assert hub.delete(f"/api/studio/instagram/{name}").json()["deleted"] is True

    assert not (hub.root / "studio" / "instagram" / name).exists()
    assert hub.get(f"/api/studio/instagram/{name}").status_code == 404
    assert name not in [r["file"] for r in hub.get("/api/studio/instagram").json()]


def test_removing_an_item_leaves_the_gate_log_intact(hub):
    """The card goes, the record of the decision does not. gate.jsonl is the audit trail of
    what was decided and why; a delete that erased its own history would leave holes exactly
    where someone later asks 'who rejected this, and when?'."""
    name = _rejected(hub, "2026-07-22-similar-audited.md")
    hub.delete(f"/api/studio/instagram/{name}")

    lines = (hub.root / "studio" / "instagram" / "gate.jsonl").read_text().splitlines()
    rows = [json.loads(x) for x in lines if x.strip() and json.loads(x)["file"] == name]
    assert [r["status"] for r in rows] == ["rejected", "deleted"]
    assert rows[0]["note"] == "off-brand"          # the original decision survives verbatim


@pytest.mark.parametrize("status", ["proposed", "approved", "draft"])
def test_only_a_rejected_item_can_be_removed(hub, status):
    """The one state where 'remove this card' unambiguously means 'I am done with it'. A
    proposed item is one click from approval and an approved one is a real generation
    somebody kept — so this route must never become the fast path to losing either."""
    name = f"2026-07-22-similar-keep-{status}.md"
    hub.post("/api/studio/instagram", json={"filename": name, "text": "x"})
    hub.post(f"/api/studio/instagram/{name}/status", json={"status": status})

    r = hub.delete(f"/api/studio/instagram/{name}")

    assert r.status_code == 409
    assert status in r.json()["detail"]
    assert (hub.root / "studio" / "instagram" / name).exists()


def test_an_item_with_rendered_media_is_refused_and_names_the_route(hub):
    """A render is paid output under its own id. Clearing a card must not be a way to spend
    money and then silently destroy the result, so this refuses and points at the route that
    deletes media on purpose."""
    name = _rejected(hub, "2026-07-22-similar-has-media.md")
    rid = hub.mod._render_id(name)
    (hub.root / "renders" / "instagram" / rid).mkdir(parents=True, exist_ok=True)

    r = hub.delete(f"/api/studio/instagram/{name}")

    assert r.status_code == 409
    assert f"/api/renders/instagram/{rid}" in r.json()["detail"]
    assert (hub.root / "studio" / "instagram" / name).exists()


def test_removing_something_that_is_not_there_is_a_404(hub):
    assert hub.delete("/api/studio/instagram/never-existed.md").status_code == 404
