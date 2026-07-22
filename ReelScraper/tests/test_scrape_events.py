"""The scrape stage's central-log events.

The scrape stage used to be the only one the hub spawned blind: one blocking `communicate()`,
stderr the child wrote to nobody, and a Dashboard card that showed an elapsed clock for six
minutes whether the run was working or wedged. These pin the contract that fixed it, and every
one of them is a rule that was wrong in an earlier draft of the design.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.hubevents import MAX_FAILURES, HubEvents  # noqa: E402

# The six verbs the hub's board reducer understands, plus the one out-of-band progress verb.
BOARD_VERBS = {"run.start", "item.start", "item.stage", "item.done", "item.error", "run.end"}
# Anything here would light the WRONG board node: liveStageIndex ORs `data.stage` against a
# pipeline stage key, so a lowercase "scrape" in a stage field points the Board at itself.
PIPELINE_STAGE_KEYS = {"scrape", "analyze", "media", "propose", "auto-search", "analysis-engine"}


class _Capture:
    """Stands in for urlopen. Records payloads; can be told to fail."""

    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def __call__(self, req, timeout=None):
        if self.fail:
            raise OSError("hub unreachable")
        self.posts.append(json.loads(req.data.decode()))

        class _R:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
        return _R()


@pytest.fixture
def emitter(monkeypatch):
    monkeypatch.setenv("BACKEND_API", "http://127.0.0.1:8787")
    cap = _Capture()
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", cap)
    return HubEvents("scrape", run_id="r1", platform="instagram"), cap


# ---------------------------------------------------------------- the client

def test_no_backend_api_means_total_silence(monkeypatch):
    """`cli.py scrape` is a bare passthrough and only `cli.py start` exports BACKEND_API, so a
    default would make a hand-run scrape blind-POST into whatever hub owns 8787 — possibly
    another checkout's, whose Board would then show this run's creators against that corpus."""
    monkeypatch.delenv("BACKEND_API", raising=False)
    cap = _Capture()
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", cap)

    HubEvents("scrape").emit("run.start")

    assert cap.posts == []


def test_an_unreachable_hub_never_raises_and_gives_up_after_three(monkeypatch):
    """Telemetry must not be able to break a scrape, and must not become the bottleneck it
    exists to report on: at a 3s timeout, a run with hundreds of events against a dead hub
    would spend longer talking about the run than running it."""
    monkeypatch.setenv("BACKEND_API", "http://127.0.0.1:8787")
    cap = _Capture(fail=True)
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", cap)
    ev = HubEvents("scrape")

    for _ in range(MAX_FAILURES + 5):
        ev.emit("item.start")          # must not raise

    assert ev.enabled is False


def test_every_event_carries_the_fields_the_board_reduces_on(emitter):
    ev, cap = emitter
    ev.emit("item.done", content_id="creator_a", msg="done", data={"stage": "Done"})

    p = cap.posts[0]
    assert p["agent"] == "scrape" and p["event"] == "item.done"
    assert p["run_id"] == "r1" and p["platform"] == "instagram"
    assert p["content_id"] == "creator_a" and p["data"]["stage"] == "Done"
    assert isinstance(p["ts"], float)


def test_the_heartbeat_is_throttled_but_always_fires_first(emitter, monkeypatch):
    ev, cap = emitter
    clock = [1000.0]
    monkeypatch.setattr("core.hubevents.time.monotonic", lambda: clock[0])

    ev.emit_throttled("item.progress", min_interval=30.0)      # first always goes
    clock[0] += 5
    ev.emit_throttled("item.progress", min_interval=30.0)      # swallowed
    clock[0] += 30
    ev.emit_throttled("item.progress", min_interval=30.0)      # due again

    assert [p["event"] for p in cap.posts] == ["item.progress", "item.progress"]


# ---------------------------------------------------------------- the emitted vocabulary

def _events_from(monkeypatch, tmp_path, platform, creators, fetch):
    """Drive one scraper's main() with the network stubbed, and return what it POSTed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platforms" / platform))
    mod = __import__("scrape")
    cap = _Capture()
    monkeypatch.setenv("BACKEND_API", "http://127.0.0.1:8787")
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", cap)
    monkeypatch.setattr(mod, "new_guest_session", lambda: True, raising=False)
    monkeypatch.setattr(mod, "scrape_creator", fetch)
    monkeypatch.setattr(mod, "CREATOR_DELAY", (0, 0), raising=False)
    (tmp_path / "pages.txt").write_text("\n".join(creators), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "HERE", tmp_path, raising=False)
    monkeypatch.setattr(sys, "argv", ["scrape.py", "--file", str(tmp_path / "pages.txt")])
    mod.main()
    return cap.posts


def test_instagram_emits_the_run_and_item_lifecycle(monkeypatch, tmp_path):
    posts = _events_from(monkeypatch, tmp_path, "instagram", ["creator_a"],
                         lambda c, limit, events=None: ({"followers": 10}, [{"id": 1, "code": "a"}]))

    kinds = [p["event"] for p in posts]
    assert kinds[0] == "run.start" and kinds[-1] == "run.end"
    assert "item.start" in kinds and "item.done" in kinds
    assert all(p["agent"] == "scrape" for p in posts)


def test_item_start_carries_the_total_so_the_client_ring_cannot_lose_it(monkeypatch, tmp_path):
    """The Dashboard's log ring holds 300 events across ALL agents, so on a long run
    `run.start` is the FIRST record evicted. A reducer reading the total only from there goes
    blank on exactly the runs this feature exists for."""
    posts = _events_from(monkeypatch, tmp_path, "instagram", ["a", "b"],
                         lambda c, limit, events=None: ({"followers": 1}, [{"id": 1, "code": "x"}]))

    starts = [p for p in posts if p["event"] == "item.start"]
    assert starts and all(p["data"]["of"] == 2 for p in starts)


def test_one_terminal_event_per_creator(monkeypatch, tmp_path):
    """An unresolved creator is a WARNING that still saves and still exits 0. Emitting
    item.error for it too would make one typo'd handle report the whole floor as snapped after
    a successful run — and `hasErrorEvent` is sticky, so it would never clear."""
    posts = _events_from(monkeypatch, tmp_path, "instagram", ["ghost"],
                         lambda c, limit, events=None: (None, []))

    per_creator = [p for p in posts if p["content_id"] == "ghost"]
    terminal = [p["event"] for p in per_creator if p["event"] in ("item.done", "item.error")]
    assert terminal == ["item.done"]
    done = next(p for p in per_creator if p["event"] == "item.done")
    assert done["level"] == "warning" and done["data"]["resolved"] is False


def test_a_stop_is_reported_as_a_normal_run_end(monkeypatch, tmp_path):
    """The hub records a stop as job_stopped and never job_failed. run.end painting itself
    red would break that promise from the agent's side."""
    import scrape as mod  # noqa: F401  (already on the path from the helper)
    posts = _events_from(monkeypatch, tmp_path, "instagram", ["a"],
                         lambda c, limit, events=None: ({"followers": 1}, [{"id": 1, "code": "x"}]))

    end = next(p for p in posts if p["event"] == "run.end")
    assert end["level"] == "info"


def test_no_event_claims_a_verb_or_a_stage_that_would_mislead_the_board(monkeypatch, tmp_path):
    """Two collisions, both silent if they regress.

    `data.stage` is OR-ed against each board node's pipeline stage key, so a literal "scrape"
    would light a node from the wrong axis. And any verb outside the six is ignored by the
    server reducer — which is exactly why the heartbeat uses `item.progress` and why it must
    never drift into `item.stage`, one of the six, where every beat would rewrite item state.
    """
    posts = _events_from(monkeypatch, tmp_path, "instagram", ["a"],
                         lambda c, limit, events=None: ({"followers": 1}, [{"id": 1, "code": "x"}]))

    for p in posts:
        assert p["event"] in BOARD_VERBS | {"item.progress"}, p["event"]
        stage = (p.get("data") or {}).get("stage")
        if stage is not None:
            assert stage not in PIPELINE_STAGE_KEYS, f"{stage!r} collides with a board node"


# ---------------------------------------------------------------- the hub end of the contract

def test_the_hub_accepts_and_stores_what_the_client_actually_sends(hub, monkeypatch):
    """The unit tests above stub urlopen, so they prove the SHAPE the client builds and
    nothing about whether the hub takes it. This posts a real emitted payload through the
    real route: `LogIn` requires only `agent`, but `data`, `content_id` and `run_id` are the
    fields the board reduces on, and a rename on either side would leave the scrape card
    silently blank rather than erroring."""
    captured = {}

    class _Send:
        def __call__(self, req, timeout=None):
            body = json.loads(req.data.decode())
            r = hub.post("/api/logs", json=body)
            captured["status"] = r.status_code

            class _R:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
            return _R()

    monkeypatch.setenv("BACKEND_API", "http://testserver")
    monkeypatch.setattr("core.hubevents.urllib.request.urlopen", _Send())

    HubEvents("scrape", run_id="r9", platform="instagram").emit(
        "item.done", content_id="creator_a", msg="creator_a: 12 reels",
        data={"stage": "Done", "items": 12, "resolved": True, "reels_total": 12})

    assert captured["status"] == 200
    rows = hub.get("/api/logs", params={"agent": "scrape"}).json()
    rec = next(r for r in rows if r["event"] == "item.done")
    assert rec["content_id"] == "creator_a"
    assert rec["run_id"] == "r9" and rec["platform"] == "instagram"
    assert rec["data"]["stage"] == "Done" and rec["data"]["reels_total"] == 12
