"""The cascading heartbeat — work flowing down the pipeline on its own, in ratios.

The scheduler asks "when should a whole run happen?" and answers with a clock. This asks
"how much new work has landed?" and answers with a counter: every N new scraped reels an
`analyze` fires, every N new scored rows a `media` fires, every N new clips a blueprint
run fires (paid, opt-in), every N new blueprints three proposals are published — and then
it stops, at the human gate.

The N per boundary is not typed in. The operator types a batch size (`scrape_count`) and a
pass-through percentage per boundary, and the step chain is DERIVED from those — which is
what makes "downstream never fires more often than upstream" structural rather than a rule
the API has to police. The tests below mostly use a 1:1 funnel (a batch of 40, 100% through
every boundary) so the number in the assertion is the number in the fixture.

Everything below is about the four ways an unattended counter goes wrong: spending money
nobody asked for, firing a burst instead of once, firing while something else owns the
files, and silently skipping work because a watermark outlived the data it described.
"""
import json
import pathlib
import re

import pytest


PLATFORM = "instagram"


def _funnel(**fields):
    """A stored row whose funnel derives a step of 40 at every boundary — one batch of 40,
    nothing tapered. Most tests here care about ONE boundary's arithmetic."""
    row = {"enabled": True, "include_blueprints": False,
           "scrape_count": 40, "analyze_pct": 100, "media_pct": 100,
           "blueprint_pct": 100, "propose_pct": 100,
           "propose_count": 3,
           "marks": {"analyze": 0, "media": 0, "analysis-engine": 0, "propose": 0}}
    row.update(fields)
    return row


def _cfg(hub, platform=PLATFORM, **fields):
    """Write the cascade config by hand, the way an operator or an older build would."""
    row = _funnel()
    for k, v in fields.items():
        if isinstance(v, dict) and isinstance(row.get(k), dict):
            row[k].update(v)
        else:
            row[k] = v
    d = hub.root / "config"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pipeline_cascade.json").write_text(json.dumps({platform: row}), encoding="utf-8")
    return row


def _on_disk(hub):
    return json.loads((hub.root / "config" / "pipeline_cascade.json").read_text())


def _reels(hub, n, platform=PLATFORM):
    """`n` raw scraped items — what the `analyze` boundary counts."""
    (hub.root / "platforms" / platform).mkdir(parents=True, exist_ok=True)
    (hub.root / "platforms" / platform / "reels_raw.json").write_text(
        json.dumps({"someone": [{"id": i} for i in range(n)]}), encoding="utf-8")


def _corpus(hub, n, platform=PLATFORM):
    """`n` scored rows — what the `media` boundary counts (and what readiness needs)."""
    (hub.root / "platforms" / platform).mkdir(parents=True, exist_ok=True)
    (hub.root / "platforms" / platform / "content.json").write_text(
        json.dumps([{"content_id": f"c{i}"} for i in range(n)]), encoding="utf-8")


def _clips(hub, n, platform=PLATFORM):
    """`n` persisted mp4s — what the `analysis-engine` boundary counts."""
    d = hub.root / "media" / platform
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"c{i}.mp4").write_bytes(b"x")


def _blueprints(hub, n, platform=PLATFORM):
    """`n` blueprints — what the `propose` boundary counts."""
    d = hub.root / "analysis" / platform
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"c{i}.json").write_text("{}", encoding="utf-8")


@pytest.fixture
def launched(hub, monkeypatch):
    """Record what the tick launches instead of spawning anything."""
    calls = []

    def fake(platform, stage, cmd_kwargs=None, extra_args=None, job_key=None, meta=None):
        calls.append((platform, stage, list(extra_args or [])))
        return f"{platform}:{stage}:1"
    monkeypatch.setattr(hub.mod, "_launch_stage_job", fake)
    return calls


@pytest.fixture
def gemini_key(monkeypatch):
    """The blueprint stage's readiness needs a key present. Set by NAME only — the hub
    never reads, stores or logs the value."""
    monkeypatch.setenv("GEMINI_API_KEY", "present-for-this-test")


@pytest.fixture
def proposer(hub):
    """A registered producer that declares it proposes, so the propose boundary resolves."""
    (hub.root / "producers").mkdir(parents=True, exist_ok=True)
    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "dir": "SimilarContent",
                            "proposes": True,
                            "propose_cmd": ["uv", "run", "cli.py"]}}), encoding="utf-8")


# ---------------------------------------------------------------- off by default

def test_a_fresh_clone_fires_nothing(hub, launched):
    """No config file at all. A feature that spends a paid API must be byte-identical to
    the previous release until somebody switches it on."""
    _reels(hub, 5_000)
    _corpus(hub, 5_000)
    _clips(hub, 5_000)

    assert hub.mod._cascade_tick() == []
    assert launched == []
    assert not (hub.root / "config" / "pipeline_cascade.json").exists()


def test_a_disabled_cascade_opens_no_corpus_file(hub, monkeypatch, launched):
    """The cost of being off, asserted rather than assumed: a tick runs every 60 seconds
    forever on every install, and counting a corpus means parsing tens of megabytes."""
    _cfg(hub, enabled=False)
    counted = []
    monkeypatch.setattr(hub.mod, "_cascade_counts",
                        lambda p: counted.append(p) or {s: 0 for s in hub.mod.CASCADE_STAGES})

    hub.mod._cascade_tick()

    assert counted == []


@pytest.mark.parametrize("garbage", ["{not json", "[1, 2, 3]", "null", '"a string"',
                                     '{"instagram": ["not", "a", "row"]}'])
def test_an_unreadable_cascade_config_fails_closed(hub, launched, garbage):
    """Any problem resolves to disabled, for every platform. A trigger that turns ITSELF on
    because a file was unreadable is the one failure mode an unattended loop must not have.
    """
    _reels(hub, 5_000)
    (hub.root / "config").mkdir(parents=True, exist_ok=True)
    (hub.root / "config" / "pipeline_cascade.json").write_text(garbage, encoding="utf-8")

    assert hub.mod._cascade_tick() == []
    assert all(row["enabled"] is False for row in hub.mod._read_cascade().values())


# ---------------------------------------------------------------- firing rules

def test_a_stage_fires_once_when_its_input_grows_by_its_step(hub, launched):
    _cfg(hub)
    _reels(hub, 40)

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]
    assert hub.mod._cascade_tick() == []


def test_a_stage_below_its_step_does_not_fire(hub, launched):
    _cfg(hub)
    _reels(hub, 39)

    assert hub.mod._cascade_tick() == []


def test_a_stage_fires_once_when_its_input_grows_by_five_steps(hub, launched):
    """The mark jumps to the OBSERVED count, never forward by one step. These stages are
    batch — analyze re-scores the whole corpus every run — so a 200-item backlog is one
    fire's worth of work, and firing five identical analyzes back to back would be five
    times the work for the same result."""
    _cfg(hub)
    _reels(hub, 200)

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 200
    assert hub.mod._cascade_tick() == []


def test_only_the_most_upstream_due_stage_fires_in_one_tick(
        hub, launched, proposer, gemini_key):
    """The stages are strictly serial: each consumes output the next tick will only just
    have seen. Firing all four at once would run `media` against a corpus `analyze` is
    still rewriting — the exact hazard the run-all claim exists to prevent."""
    _cfg(hub, include_blueprints=True)
    _reels(hub, 200)
    _corpus(hub, 200)
    _clips(hub, 200)
    _blueprints(hub, 200)

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]
    assert hub.mod._cascade_tick() == [(PLATFORM, "media")]
    assert hub.mod._cascade_tick() == [(PLATFORM, "analysis-engine")]
    assert hub.mod._cascade_tick() == [(PLATFORM, "propose")]
    assert hub.mod._cascade_tick() == []


def test_the_watermark_is_stamped_before_the_launch(hub, monkeypatch):
    """A stage can run for an hour. If the mark were written when it FINISHED, every tick
    in between would still see the work as new and start another one."""
    seen = {}

    def fake(platform, stage, **k):
        seen["mark_during_launch"] = _on_disk(hub)[PLATFORM]["marks"]["analyze"]
        return "job-1"
    monkeypatch.setattr(hub.mod, "_launch_stage_job", fake)
    _cfg(hub)
    _reels(hub, 40)

    hub.mod._cascade_tick()

    assert seen["mark_during_launch"] == 40


def test_a_failed_launch_rolls_its_watermark_back(hub, monkeypatch):
    """Otherwise an exception in the spawner silently costs a full window of work: the mark
    says the input was consumed, and nothing ever consumed it."""
    def boom(*a, **k):
        raise RuntimeError("uv not found")
    monkeypatch.setattr(hub.mod, "_launch_stage_job", boom)
    _cfg(hub)
    _reels(hub, 40)

    assert hub.mod._cascade_tick() == []
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 0


def test_a_shrinking_corpus_pulls_the_mark_down_and_fires_nothing(hub, launched):
    """./clean, a re-scrape, a deleted analysis dir. A mark above the live count describes
    data that is gone; left there it keeps the stage silent until the count climbs back
    past a number that no longer means anything — a SILENT skip, which is precisely what a
    watermark exists to prevent. Lowering it fires nothing on its own: it then takes a full
    fresh step to come due."""
    _cfg(hub, marks={"analyze": 500})
    _reels(hub, 10)

    assert hub.mod._cascade_tick() == []
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 10

    _reels(hub, 49)
    assert hub.mod._cascade_tick() == []          # 39 new — still short of the step
    _reels(hub, 50)
    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]


def test_two_unchanged_ticks_fire_nothing_and_a_hub_restart_changes_nothing(hub, launched):
    """Marks and counts are both on disk and nothing here is time-based, so the decision is
    re-derived identically. Restarting the hub five times must not mean five analyzes."""
    _cfg(hub)
    _reels(hub, 40)
    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]

    # A restart, without reloading the module out from under the isolated ROOT: throw away
    # every scrap of in-memory state the tick could have leaned on and re-derive.
    hub.mod._RAW_COUNT_CACHE.clear()
    hub.mod.JOBS.clear()

    assert hub.mod._cascade_tick() == []
    assert hub.mod._cascade_tick() == []
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 40


# ---------------------------------------------------------------- backpressure

def test_the_cascade_stands_down_while_any_job_owns_the_platform(hub, launched):
    """Not just the same stage: firing `analyze` while a manual scrape is mid-write is the
    exact hazard the run-all claim was introduced to prevent. And no mark advances, so the
    work fires the instant the platform is free."""
    _cfg(hub)
    _reels(hub, 40)
    hub.mod.JOBS["instagram:scrape:1"] = {"platform": PLATFORM, "stage": "scrape",
                                          "status": "running", "started": 0, "ended": None,
                                          "rc": None, "tail": ""}
    try:
        assert hub.mod._cascade_tick() == []
        assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 0
        hub.mod.JOBS["instagram:scrape:1"]["status"] = "done"
        assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]
    finally:
        hub.mod.JOBS.pop("instagram:scrape:1", None)


def test_the_cascade_stands_down_while_a_run_all_owns_the_platform_and_keeps_its_marks(
        hub, launched):
    """The timer decides when whole runs happen; the cascade decides when the single next
    stage happens; the cascade always yields."""
    _cfg(hub)
    _reels(hub, 40)
    hub.mod._RUNNING_ALL.add(PLATFORM)
    try:
        assert hub.mod._cascade_tick() == []
        assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 0
    finally:
        hub.mod._RUNNING_ALL.discard(PLATFORM)

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]


def test_a_job_that_lands_during_the_stamp_cancels_the_fire_and_gives_the_mark_back(
        hub, launched, monkeypatch):
    """The stand-down check runs under _CASCADE_LOCK, which does not guard JOBS — and the
    lock is then released across a config file write. A manual run landing in that window
    gave two AnalysisEngine runs draining the same top-15 pending clips: the same clips
    analysed, and billed, twice."""
    _cfg(hub)
    _reels(hub, 40)
    calls = {"n": 0}

    def arriving(platform):
        calls["n"] += 1
        return None if calls["n"] == 1 else "instagram:scrape:9 is still running"
    monkeypatch.setattr(hub.mod, "_active_job_on", arriving)

    assert hub.mod._cascade_tick() == []
    assert launched == []
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 0     # given back, not burnt


def test_a_readiness_refusal_does_not_burn_the_watermark(hub, launched, monkeypatch):
    """The timer advances its clock on a skip so it cannot spin. This clock IS the input
    count, which does not move on its own — so not advancing costs nothing, cannot spin (it
    launched nothing), and means the work fires the moment the block clears."""
    _cfg(hub)
    _reels(hub, 40)
    blocked = {"now": True}
    monkeypatch.setattr(hub.mod, "stage_readiness", lambda p: {
        "analyze": {"ready": not blocked["now"], "blocked_by": "scrape",
                    "reason": "Nothing scraped."}})

    assert hub.mod._cascade_tick() == []
    assert _on_disk(hub)[PLATFORM]["marks"]["analyze"] == 0

    blocked["now"] = False
    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]


# ---------------------------------------------------------------- money

def test_blueprints_never_fire_unless_explicitly_opted_into(hub, launched, gemini_key):
    """analysis-engine calls a paid API per clip. Unattended and every minute, that is a
    bill nobody agreed to — the same reasoning that keeps it out of the free scheduled
    stages and keeps `render` out of everything."""
    _cfg(hub)                                 # include_blueprints defaults false
    _corpus(hub, 200)
    _clips(hub, 200)
    _reels(hub, 0)

    assert hub.mod._cascade_tick() == [(PLATFORM, "media")]
    assert hub.mod._cascade_tick() == []      # analysis-engine is due, and stays silent

    _cfg(hub, include_blueprints=True, marks={"media": 200})
    assert hub.mod._cascade_tick() == [(PLATFORM, "analysis-engine")]


@pytest.mark.parametrize("hostile", [
    {"steps": {"render": 1}},
    {"steps": {"render": 1, "analyze": 1, "media": 1, "analysis-engine": 1, "propose": 1},
     "propose_count": 1},
    {"marks": {"render": -5}},
    {"stage": "render"},
    {"stages": ["render"]},
    {"render": True, "render_count": 99},
    {"render_pct": 100, "propose_count": 1},
    {"scrape_count": 1, "analyze_pct": 100, "media_pct": 100, "blueprint_pct": 100,
     "propose_pct": 100, "propose_count": 1},
])
def test_no_configuration_can_make_the_cascade_fire_render(
        hub, launched, proposer, gemini_key, hostile):
    """THE test. `render` spends image-API credits per frame, and the entire promise of this
    feature is that it costs nothing. Three independent things have to hold: `render` is not
    in CASCADE_STAGES, no config field names a stage, and `marks` keys outside CASCADE_STAGES
    are dropped on read rather than honoured. Delete this test and any one of the three can
    quietly regress into a bill.

    The funnel model removed the one field that came closest to naming a stage — `steps` is
    derived now — so a stored `steps.render` is doubly inert: dropped on read AND not an
    input at all.
    """
    hostile = {"include_blueprints": True, **hostile}
    _cfg(hub, **hostile)
    _reels(hub, 5_000)
    _corpus(hub, 5_000)
    _clips(hub, 5_000)
    _blueprints(hub, 5_000)

    for _ in range(8):
        hub.mod._cascade_tick()

    assert "render" not in hub.mod.CASCADE_STAGES
    assert [s for _, s, _ in launched if s == "render"] == []
    assert "render" not in hub.mod._read_cascade()[PLATFORM]["steps"]
    assert "render" not in _on_disk(hub)[PLATFORM]["marks"]
    assert list(hub.mod.CASCADE_PCTS) == hub.mod.CASCADE_STAGES


def test_render_is_not_reachable_through_the_cascade_api_either(hub):
    """The PUT model has no field that names a stage at all now — `steps` is not an input,
    so a body carrying one is ignored rather than filtered. The route cannot be talked into
    storing a stage name either way."""
    r = hub.put(f"/api/cascade/{PLATFORM}",
                json={"scrape_count": 40,
                      "steps": {"render": 1, "analyze": 40, "media": 40,
                                "analysis-engine": 40, "propose": 40}})

    assert r.status_code == 200, r.text
    assert "render" not in r.json()["steps"]
    assert list(r.json()["steps"]) == hub.mod.CASCADE_STAGES
    assert "render" not in _on_disk(hub)[PLATFORM]


# ---------------------------------------------------------------- the funnel

@pytest.mark.parametrize("funnel,expected", [
    # The shipped defaults: 250 reels, all analyzed, 60% worth downloading, 20% of those
    # worth a paid blueprint, 20% of those worth proposing against.
    ({"scrape_count": 250, "analyze_pct": 100, "media_pct": 60,
      "blueprint_pct": 20, "propose_pct": 20},
     {"analyze": 250, "media": 417, "analysis-engine": 2085, "propose": 10425}),
    # Everything passes through: one batch, four times.
    ({"scrape_count": 40, "analyze_pct": 100, "media_pct": 100,
      "blueprint_pct": 100, "propose_pct": 100},
     {"analyze": 40, "media": 40, "analysis-engine": 40, "propose": 40}),
    # Rounding is UP at every hop — 1 * 100/3 is 34, not 33. A step that rounded down would
    # fire a boundary before a full batch's worth of input could possibly have reached it.
    # And the last hop is clamped: 1,260,000 is a threshold no counter here ever crosses.
    ({"scrape_count": 1, "analyze_pct": 3, "media_pct": 3,
      "blueprint_pct": 3, "propose_pct": 3},
     {"analyze": 34, "media": 1_134, "analysis-engine": 37_800, "propose": 1_000_000}),
])
def test_the_steps_are_the_ceil_chain_of_the_percentages(hub, funnel, expected):
    """step[stage] = ceil(step[previous] * 100 / pct[stage]), anchored on scrape_count. The
    operator says how much survives each boundary; the counter thresholds follow."""
    assert hub.mod._cascade_steps(funnel) == expected

    r = hub.put(f"/api/cascade/{PLATFORM}", json=funnel)
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == expected


@pytest.mark.parametrize("scrape_count", [1, 7, 250, 4_999, 5_000])
@pytest.mark.parametrize("pcts", [(1, 1, 1, 1), (100, 100, 100, 100), (100, 60, 20, 20),
                                  (3, 97, 50, 1), (99, 1, 100, 7), (50, 50, 50, 50),
                                  (17, 83, 2, 61)])
def test_the_steps_are_non_decreasing_for_every_funnel_the_api_accepts(
        hub, scrape_count, pcts):
    """THE structural property, and the reason the model changed. A downstream stage firing
    more often than the one feeding it is the one arithmetic the chain cannot express; under
    the old absolute steps it was reachable by typing two numbers and had to be REFUSED at
    the boundary. Every pct is coerced into 1..100, so every multiplier is >= 1 — the funnel
    can no longer be widened through the API at all, whatever is typed."""
    analyze_pct, media_pct, blueprint_pct, propose_pct = pcts
    r = hub.put(f"/api/cascade/{PLATFORM}", json={
        "scrape_count": scrape_count, "analyze_pct": analyze_pct, "media_pct": media_pct,
        "blueprint_pct": blueprint_pct, "propose_pct": propose_pct,
        # The one boundary the percentages do NOT make structural has its own test; keep it
        # out of the way so a 400 here can only ever mean the funnel itself was refused.
        "propose_count": 1})

    assert r.status_code == 200, r.text
    steps = [r.json()["steps"][s] for s in hub.mod.CASCADE_STAGES]
    assert steps == sorted(steps)
    assert steps[0] >= 1
    assert steps[-1] <= 1_000_000
    assert r.json()["problem"] is None


def test_an_absurd_funnel_is_clamped_rather_than_left_to_compound(hub):
    """A chain of 1% compounds to 5 * 10^10 — an int Python is happy to hold and no counter
    on this box will ever reach, which reads to an operator as silently broken rather than as
    a number they typed."""
    r = hub.put(f"/api/cascade/{PLATFORM}", json={
        "scrape_count": 5_000, "analyze_pct": 1, "media_pct": 1,
        "blueprint_pct": 1, "propose_pct": 1})

    assert r.status_code == 200, r.text
    assert r.json()["steps"] == {"analyze": 500_000, "media": 1_000_000,
                                 "analysis-engine": 1_000_000, "propose": 1_000_000}


def test_a_stored_steps_from_an_older_build_is_ignored_and_then_dropped(hub, launched):
    """`pipeline_cascade.json` is per-install state, so an upgrade finds a file written when
    `steps` was an INPUT. It is neither migrated nor honoured — the funnel derives the chain —
    and the stale key is dropped the next time the row is written, because a stored setting
    that does nothing is worse than one that is gone."""
    _cfg(hub, steps={"analyze": 1, "media": 1, "analysis-engine": 1, "propose": 1})
    _reels(hub, 39)

    assert hub.mod._read_cascade()[PLATFORM]["steps"] == {
        "analyze": 40, "media": 40, "analysis-engine": 40, "propose": 40}
    assert hub.mod._cascade_tick() == []           # 39 new: the stored 1 is not in play

    _reels(hub, 40)
    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]
    assert "steps" not in _on_disk(hub)[PLATFORM]


def test_a_propose_count_wider_than_its_boundary_is_refused_with_the_number(hub):
    """The one funnel violation the percentages cannot make structural: `propose_count` is
    its own field, so publishing 25 recipes off a boundary that fires every 2 new blueprints
    still widens the last hop. REFUSED, not clamped: this is a human typing into a form, and
    a silent clamp means the number they see afterwards is not the number they typed, so they
    type it again."""
    r = hub.put(f"/api/cascade/{PLATFORM}",
                json={"scrape_count": 2, "analyze_pct": 100, "media_pct": 100,
                      "blueprint_pct": 100, "propose_pct": 100, "propose_count": 25})

    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "propose_count" in detail and "2" in detail


def test_a_refused_configuration_is_not_persisted(hub):
    """A 400 that had already written half the row would leave the stored settings in a
    state the operator never asked for and cannot see."""
    hub.put(f"/api/cascade/{PLATFORM}",
            json={"scrape_count": 2, "analyze_pct": 100, "media_pct": 100,
                  "blueprint_pct": 100, "propose_pct": 100, "propose_count": 25})

    row = hub.get("/api/cascade").json()[PLATFORM]
    assert row["scrape_count"] == 250            # the default, untouched
    assert row["propose_count"] == 5


def test_a_hand_edited_config_that_widens_the_funnel_disables_that_platform_and_says_why(
        hub, launched):
    """The rule is "any ambiguity = the chain is OFF". But not silently: GET reports the
    sentence, because a daemon that quietly stopped working is its own bug."""
    _cfg(hub, scrape_count=2, propose_count=25)
    _reels(hub, 5_000)
    _corpus(hub, 5_000)

    assert hub.mod._cascade_tick() == []

    row = hub.get("/api/cascade").json()[PLATFORM]
    assert row["enabled"] is False
    assert row["problem"] and "propose_count" in row["problem"]


def test_a_healthy_config_reports_no_problem(hub):
    assert hub.get("/api/cascade").json()[PLATFORM]["problem"] is None


def test_fixing_a_widened_funnel_brings_the_platform_back_on(hub, launched):
    """The read-time refusal is a REPORT, not stored state. It used to be promoted into a
    persisted `enabled: false` by the very PUT that fixed the funnel: `problem` cleared,
    the toggle stayed off, and the sentence explaining why was gone — so the operator did
    the one thing the message told them to and the platform still would not run."""
    _cfg(hub, scrape_count=2, propose_count=25)   # stored enabled, funnel widened by hand
    _reels(hub, 5_000)
    assert hub.mod._cascade_tick() == []
    assert hub.get("/api/cascade").json()[PLATFORM]["enabled"] is False

    r = hub.put(f"/api/cascade/{PLATFORM}", json={"scrape_count": 40})

    assert r.status_code == 200, r.text
    assert r.json()["problem"] is None
    assert r.json()["enabled"] is True
    assert _on_disk(hub)[PLATFORM]["enabled"] is True
    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]


def test_a_problem_row_offers_nothing_as_due(hub):
    """`due` is what the UI shows as about to happen. A row that refuses to run must not
    advertise work it will never start."""
    _cfg(hub, scrape_count=2, propose_count=25)
    _reels(hub, 5_000)

    assert hub.get("/api/cascade").json()[PLATFORM]["due"] == []


def test_one_platforms_tick_does_not_rewrite_another_platforms_stored_settings(hub, launched):
    """`_write_cascade` serialises every platform, and the tick writes on any mark change —
    so one instagram down-clamp silently restated x's row and dropped every top-level key
    this build does not know about. Settings the operator typed are not this tick's to
    rewrite."""
    d = hub.root / "config"
    d.mkdir(parents=True, exist_ok=True)
    widened = _funnel(scrape_count=2, propose_count=25)
    (d / "pipeline_cascade.json").write_text(json.dumps(
        {PLATFORM: _funnel(), "x": widened,
         "a-platform-a-newer-build-added": {"enabled": True}}))
    _reels(hub, 40)

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]

    disk = _on_disk(hub)
    assert disk["x"]["enabled"] is True                    # the refusal stayed a report
    assert disk["x"]["propose_count"] == 25                # ...and so did their number
    assert disk["x"]["scrape_count"] == 2
    assert "a-platform-a-newer-build-added" in disk


# ---------------------------------------------------------------- hand-edited files

@pytest.mark.parametrize("route", ["get", "put"])
def test_an_infinite_funnel_number_fails_closed_instead_of_500ing_the_panel(
        hub, launched, route):
    """json.loads maps `1e400` to float('inf'), and int(inf) raises OverflowError — an
    ArithmeticError, which slipped past a (TypeError, ValueError) handler. The tick survived
    on its own catch-all, but GET and PUT both 500ed: the cascade was silently dead and the
    operator could neither read `problem` nor switch the platform off through the UI."""
    (hub.root / "config").mkdir(parents=True, exist_ok=True)
    (hub.root / "config" / "pipeline_cascade.json").write_text(
        '{"instagram": {"enabled": true, "scrape_count": 1e400, "media_pct": 1e400}}',
        encoding="utf-8")

    hub.mod._cascade_tick()                        # must not raise
    r = (hub.get("/api/cascade") if route == "get"
         else hub.put(f"/api/cascade/{PLATFORM}", json={"enabled": False}))

    assert r.status_code == 200, r.text
    row = r.json() if route == "put" else r.json()[PLATFORM]
    assert row["scrape_count"] == 250              # the default, not infinity
    assert row["media_pct"] == 60
    assert row["steps"]["analyze"] == 250
    assert launched == []                          # nothing due: no corpus at all


@pytest.mark.parametrize("value", ["false", "0", "no", 0, [], None])
def test_a_stringly_typed_flag_never_turns_the_paid_boundary_on(
        hub, launched, gemini_key, value):
    """`bool("false")` is True — and `"false"` is exactly what a jq edit, a YAML->JSON
    conversion or a hand edit produces. A file that literally reads `false` was spending
    Gemini credits. Only a real boolean is an answer; everything else falls back to off."""
    _cfg(hub, include_blueprints=value)
    _corpus(hub, 200)
    _clips(hub, 200)
    hub.mod._cascade_tick()                        # media fires; that one is free

    assert [s for _, s in hub.mod._cascade_tick()] == []
    assert hub.mod._read_cascade()[PLATFORM]["include_blueprints"] is False


@pytest.mark.parametrize("value", ["false", "0", 0, None])
def test_a_stringly_typed_enabled_flag_leaves_the_cascade_off(hub, launched, value):
    _cfg(hub, enabled=value)
    _reels(hub, 5_000)

    assert hub.mod._cascade_tick() == []


# ---------------------------------------------------------------- the API

def test_the_cascade_is_off_on_a_fresh_install(hub):
    row = hub.get("/api/cascade").json()[PLATFORM]

    assert row["enabled"] is False
    assert row["include_blueprints"] is False
    assert row["scrape_count"] == 250
    assert (row["analyze_pct"], row["media_pct"],
            row["blueprint_pct"], row["propose_pct"]) == (100, 60, 20, 20)
    assert row["steps"] == {"analyze": 250, "media": 417,
                            "analysis-engine": 2_085, "propose": 10_425}
    assert row["propose_count"] == 5


def test_enabling_the_cascade_stamps_the_marks_to_the_current_counts(hub, launched):
    """Worth more than the rest of the config validation combined. Without this line one
    toggle against an already-scraped corpus fires four boundaries in turn — one of them
    paid — before anyone sees a job appear on the Board. It looks like a nicety copied from
    put_schedule, which is exactly why it is the easiest thing in the diff to lose."""
    _reels(hub, 3_000)
    _corpus(hub, 3_000)
    _clips(hub, 3_000)

    r = hub.put(f"/api/cascade/{PLATFORM}", json={"enabled": True,
                                                 "include_blueprints": True})

    assert r.status_code == 200, r.text
    assert r.json()["marks"] == {"analyze": 3_000, "media": 3_000,
                                 "analysis-engine": 3_000, "propose": 0}
    assert hub.mod._cascade_tick() == []
    assert launched == []


def test_re_enabling_an_already_enabled_platform_does_not_restamp(hub, launched):
    """Only the OFF->ON transition starts the clock, exactly like put_schedule. Otherwise
    any unrelated PUT (a step change, a blueprint opt-in) would swallow the backlog."""
    _cfg(hub, marks={"analyze": 0})
    _reels(hub, 40)

    hub.put(f"/api/cascade/{PLATFORM}", json={"enabled": True, "include_blueprints": False})

    assert hub.mod._cascade_tick() == [(PLATFORM, "analyze")]


def test_opting_into_blueprints_stamps_the_paid_boundary_and_settles_nothing(
        hub, launched, gemini_key):
    """The sibling of the enable stamp, and the one that matters more.

    While include_blueprints is false the analysis-engine mark is only ever clamped DOWN —
    _cascade_plan skips the stage before the due check — so a month of free cascading leaves
    it at whatever it was when the platform was enabled while media/ fills up. Flipping the
    PAID boundary on without restamping settled that whole backlog on the next unattended
    tick: a Gemini run over clips that landed weeks ago and are not "new" by this field's own
    meaning. PUT is the only way to set it, so no operator could have avoided it."""
    _corpus(hub, 10)
    _clips(hub, 10)
    assert hub.put(f"/api/cascade/{PLATFORM}", json={"enabled": True}).status_code == 200

    _clips(hub, 3_000)                       # a month of the free half of the cascade
    for _ in range(6):
        hub.mod._cascade_tick()
    assert _on_disk(hub)[PLATFORM]["marks"]["analysis-engine"] == 10

    r = hub.put(f"/api/cascade/{PLATFORM}", json={"include_blueprints": True})

    assert r.status_code == 200, r.text
    assert r.json()["marks"]["analysis-engine"] == 3_000
    assert r.json()["due"] == []
    assert hub.mod._cascade_tick() == []
    assert [s for _, s, _ in launched if s == "analysis-engine"] == []


def test_opting_in_twice_does_not_restamp(hub, launched, gemini_key):
    """Only the OFF->ON transition starts the paid clock. Otherwise an unrelated PUT that
    happens to carry the flag again would swallow every clip that landed since."""
    _cfg(hub, include_blueprints=True, marks={"analysis-engine": 0})
    _clips(hub, 200)
    _corpus(hub, 200)

    hub.put(f"/api/cascade/{PLATFORM}", json={"include_blueprints": True,
                                              "propose_count": 3})

    assert _on_disk(hub)[PLATFORM]["marks"]["analysis-engine"] == 0


def test_an_unreadable_corpus_refuses_the_blueprint_opt_in_rather_than_stamping_zero(
        hub, monkeypatch):
    """Fail closed, like enabling does: a stamp we could not read is a backlog that would
    otherwise be settled in one unattended, paid burst."""
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(hub.mod, "_cascade_counts", boom)

    r = hub.put(f"/api/cascade/{PLATFORM}", json={"include_blueprints": True})

    assert r.status_code == 409
    assert "unreadable" in r.json()["detail"]
    assert hub.mod._read_cascade()[PLATFORM]["include_blueprints"] is False


def test_the_marks_are_machine_owned_and_ignored_on_put(hub):
    """A watermark is bookkeeping, not a setting. Accepting one over HTTP would let a stale
    UI round-trip an old mark and re-fire a paid stage over a corpus already consumed."""
    _cfg(hub, marks={"analyze": 7})

    r = hub.put(f"/api/cascade/{PLATFORM}", json={"scrape_count": 40,
                                                 "marks": {"analyze": 0}})

    assert r.status_code == 200, r.text
    assert r.json()["marks"]["analyze"] == 7


def test_the_ui_never_has_to_recompute_the_boundary_arithmetic(hub):
    """GET does the counting, the due-list and the next-at threshold, the same courtesy
    GET /api/schedule pays with next_run_at."""
    _cfg(hub, marks={"analyze": 10})
    _reels(hub, 60)

    row = hub.get("/api/cascade").json()[PLATFORM]

    assert row["counts"]["analyze"] == 60
    assert row["next_at"]["analyze"] == 50
    assert row["due"] == ["analyze"]
    assert row["stages"] == ["analyze", "media", "analysis-engine", "propose"]


def test_the_due_list_never_offers_the_paid_stage_unless_opted_in(hub):
    _cfg(hub, include_blueprints=False)
    _clips(hub, 500)

    assert "analysis-engine" not in hub.get("/api/cascade").json()[PLATFORM]["due"]


def test_an_unknown_platform_is_a_404(hub):
    assert hub.put("/api/cascade/myspace", json={"enabled": True}).status_code == 404


@pytest.mark.parametrize("body,expected", [
    # A batch of zero would mean "fire on every tick forever"; 0% would mean an infinite step.
    ({"scrape_count": 0, "media_pct": 0}, {"scrape_count": 1, "media_pct": 1}),
    ({"scrape_count": -5, "analyze_pct": -5}, {"scrape_count": 1, "analyze_pct": 1}),
    # Above 100% a boundary would produce more than it consumed, which is not a funnel.
    ({"scrape_count": 99_999, "propose_pct": 400},
     {"scrape_count": 5_000, "propose_pct": 100}),
])
def test_a_funnel_number_outside_its_range_is_clamped_rather_than_stored(
        hub, body, expected):
    """Field-level coercion, exactly like the scheduler's max(1.0, every_hours): scrape_count
    into 1..5000, every percentage into 1..100."""
    r = hub.put(f"/api/cascade/{PLATFORM}", json=body)

    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in expected} == expected
    assert {k: _on_disk(hub)[PLATFORM][k] for k in expected} == expected


# ---------------------------------------------------------------- propose's count

def test_propose_never_asks_for_more_recipes_than_the_blueprints_that_triggered_it(hub):
    """`--count` is clamped to the NEW blueprints available. The funnel check above makes
    this unreachable through the API — which is the point: it is the last line of defence
    for a file somebody edited by hand, and the one place the invariant is enforced at the
    moment money-shaped work is actually handed out. Publishing 3 recipes off 2 new
    blueprints is the funnel running backwards, and no other layer would catch it."""
    row = {"propose_count": 3}

    assert hub.mod._cascade_extra_args("propose", row, 2) == ["--count", "2"]
    assert hub.mod._cascade_extra_args("propose", row, 99) == ["--count", "3"]
    # Never zero or negative: argparse would take --count 0 literally and publish nothing.
    assert hub.mod._cascade_extra_args("propose", row, 0) == ["--count", "1"]
    # And no other stage is handed arguments it never declared.
    assert hub.mod._cascade_extra_args("analyze", row, 99) is None


def test_the_paid_boundary_is_rationed_to_its_configured_share_of_the_new_clips(hub):
    """`--limit` is `blueprint_top_pct` of what TRIGGERED the firing, never of the corpus.
    Sized against the corpus it would re-ration work already blueprinted and grow without
    bound as the corpus does — on the one boundary that spends money per clip.

    The slice is meaningful because GET /api/analysis/{p}/pending sorts by -virality_score
    before it applies `limit`, so this is the TOP fifth by default, not an arbitrary fifth.
    The duration veto is deliberately NOT here: it lives in analysis-engine's own config as
    `max_duration_s` so a manual Run from the Board obeys it too."""
    row = {"blueprint_top_pct": 20}

    assert hub.mod._cascade_extra_args("analysis-engine", row, 60) == ["--limit", "12"]

    # Rounds UP and floors at 1. A boundary that fired is one that found new work, and a
    # quota that floored to 0 would hand out `--limit 0`, analyze nothing, and still let the
    # mark advance past those clips — a silent skip, which is what a watermark exists to
    # prevent.
    assert hub.mod._cascade_extra_args("analysis-engine", row, 4) == ["--limit", "1"]
    assert hub.mod._cascade_extra_args("analysis-engine", row, 1) == ["--limit", "1"]

    # Never more than what triggered the firing, even at 100%.
    assert hub.mod._cascade_extra_args(
        "analysis-engine", {"blueprint_top_pct": 100}, 7) == ["--limit", "7"]

    # media is free and processes what it finds; it is handed nothing.
    assert hub.mod._cascade_extra_args("media", row, 99) is None


def test_propose_asks_for_its_configured_count_when_there_is_room(
        hub, launched, proposer):
    _cfg(hub, marks={"media": 10})
    _blueprints(hub, 40)
    _corpus(hub, 10)

    hub.mod._cascade_tick()

    assert launched[-1][:2] == (PLATFORM, "propose")
    assert launched[-1][2] == ["--count", "3"]


def test_a_propose_boundary_with_no_producer_stands_down_instead_of_erroring_every_minute(
        hub, launched, caplog):
    """No registered producer declares `proposes`, so the launch raises a 409 — and because
    the mark is correctly rolled back, the identical failure repeated on every 60s tick:
    1,440 ERROR records a day per platform, drowning the real ones. It is a not-ready
    condition, so it is treated as one — resolved before the stamp, one INFO line, and it
    fires the moment a producer registers."""
    _cfg(hub, marks={"media": 10})
    _blueprints(hub, 40)
    _corpus(hub, 10)

    with caplog.at_level("INFO", logger="api.hub"):
        assert hub.mod._cascade_tick() == []
        assert hub.mod._cascade_tick() == []

    assert launched == []
    assert _on_disk(hub)[PLATFORM]["marks"]["propose"] == 0
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []


def test_reference_blueprints_do_not_count_as_pipeline_work(hub, launched, proposer):
    """`ref_*` are operator-supplied reference clips, not corpus output — the same
    exclusion _media_count already makes. Counting them would let dropping 40 references
    into the folder fire a propose run over a corpus that has not moved."""
    _cfg(hub)
    _corpus(hub, 10)
    d = hub.root / "analysis" / PLATFORM
    d.mkdir(parents=True, exist_ok=True)
    for i in range(40):
        (d / f"ref_{i}.json").write_text("{}", encoding="utf-8")

    assert hub.mod._cascade_tick() == []


# ---------------------------------------------------------------- the daemon

def test_the_cascade_tick_never_raises(hub, monkeypatch, launched):
    """A count helper that throws — an unreadable corpus, a permissions change, a corrupt
    analysis dir — must cost one log line, not the daemon. The loop below has no way to
    restart a thread that died."""
    _cfg(hub)

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(hub.mod, "_scraped_count", boom)
    monkeypatch.setattr(hub.mod, "_media_count", boom)

    assert hub.mod._cascade_tick() == []


def test_the_cascade_loop_never_raises_into_startup(hub, monkeypatch):
    """Fire-and-forget from the startup hook, inside its own try/except. A background
    trigger that is off by default must never be able to stop the hub from serving."""
    def boom(*a, **k):
        raise RuntimeError("thread refused")
    monkeypatch.setattr(hub.mod.threading, "Thread", boom)

    hub.mod._on_startup()          # must not raise


def test_the_cascade_thread_is_started_at_startup(hub, monkeypatch):
    started = []
    monkeypatch.setattr(hub.mod.threading, "Thread",
                        lambda target=None, **k: started.append(getattr(target, "__name__", ""))
                        or type("T", (), {"start": lambda s: None, "daemon": True})())

    hub.mod._on_startup()

    assert "_cascade_loop" in started


# ------------------------------------------------------- the Dashboard's half of it
#
# The funnel is edited in a browser, and the browser's copy of the rules is a separate
# language the hub's suite cannot import. That gap is where THE bug in this feature lived
# once already: the card PUT a field the model did not declare, pydantic dropped it, the
# route answered 200, and nothing was persisted — a save that looked like it worked and
# never did. TypeScript cannot see app.py and pytest cannot see the .ts, so neither side's
# green suite says anything at all about the seam between them. These read the Dashboard
# source as text and assert the two halves still describe the same funnel.
#
# They SKIP rather than fail when the Dashboard is not on disk: this package is meant to be
# usable on its own, and a missing sibling directory is a legitimate checkout, not a defect.
DASHBOARD_LIB = pathlib.Path(__file__).resolve().parents[2] / "Dashboard" / "src" / "lib"


def _dashboard(name):
    f = DASHBOARD_LIB / name
    if not f.is_file():
        pytest.skip(f"no Dashboard checkout at {DASHBOARD_LIB}")
    return f.read_text(encoding="utf-8")


def _ts_cascade_limits():
    """The `CASCADE_LIMITS` table out of cascadeFunnel.ts, as {field: (min, max, default)}.

    Read as text on purpose. A generated client would be the other answer, but it would
    have to be regenerated by the same hand that broke the contract, and this failure mode
    is precisely a change made on one side only."""
    src = _dashboard("cascadeFunnel.ts")
    body = re.search(r"CASCADE_LIMITS\s*=\s*\{(.*?)\n\}", src, re.S)
    assert body, "cascadeFunnel.ts no longer declares CASCADE_LIMITS"
    return {m["f"]: (int(m["lo"]), int(m["hi"]), int(m["def"]))
            for m in re.finditer(
                r"(?P<f>\w+):\s*\{\s*min:\s*(?P<lo>\d+),\s*max:\s*(?P<hi>\d+),"
                r"\s*fallback:\s*(?P<def>\d+)\s*\}", body.group(1))}


def test_the_card_and_the_hub_agree_on_every_bound_and_default(hub):
    """Same numbers, both sides. The card clamps locally so the operator reads back what
    they typed rather than a silent server-side correction — which only stays true while
    the two tables match. A hub that lowered SCRAPE_COUNT_MAX would otherwise leave the
    form happily accepting 5000 and the hub quietly storing something else."""
    want = {"scrape_count": (1, hub.mod.SCRAPE_COUNT_MAX,
                             hub.mod.CASCADE_DEFAULTS["scrape_count"]),
            "propose_count": (1, hub.mod.PROPOSE_COUNT_MAX,
                              hub.mod.CASCADE_DEFAULTS["propose_count"]),
            # A quota, not a cadence — so it is absent from CASCADE_PCTS below and has to be
            # named here, exactly like the two counts above.
            "blueprint_top_pct": (1, 100,
                                  hub.mod.CASCADE_DEFAULTS["blueprint_top_pct"])}
    for field in hub.mod.CASCADE_PCTS.values():
        want[field] = (1, 100, hub.mod.CASCADE_DEFAULTS[field])

    assert _ts_cascade_limits() == want


def test_every_number_the_card_can_put_is_a_field_the_model_declares(hub):
    """THE regression test for the original bug: a body key the model does not declare is
    dropped by pydantic, the route answers 200, and the setting is silently not saved.
    CASCADE_LIMITS is exactly the set of numbers the card PUTs, one field per commit."""
    accepted = set(hub.mod.CascadeIn.model_fields)

    assert set(_ts_cascade_limits()) <= accepted

    # ...and the other direction, so a new number on the hub cannot stay unreachable: the
    # two booleans are toggles, not numbers, and are not in the card's clamp table.
    assert accepted - set(_ts_cascade_limits()) == {"enabled", "include_blueprints"}


def test_every_field_the_card_reads_is_one_the_route_actually_returns(hub):
    """The mirror of the above, on the GET. A field the card reads and the row does not
    carry is `undefined` in a browser and NaN in the arithmetic below it — the card renders
    "≤NaN clips", which points nowhere near the cause."""
    src = _dashboard("types.ts")
    body = re.search(r"export interface CascadeRow \{(.*?)\n\}", src, re.S)
    assert body, "types.ts no longer declares CascadeRow"
    declared = set(re.findall(r"^  (\w+)\??:", body.group(1), re.M))

    assert declared == set(hub.get(f"/api/cascade").json()[PLATFORM])


def test_the_card_names_no_stage_the_cascade_may_not_launch(hub):
    """The render barrier, on the Dashboard's side of the wire. The card's own stage list
    has to be the hub's, or a future row could offer a percentage for a boundary that
    spends image credits — and the form would look like the place that was allowed to."""
    stages = re.search(r"CASCADE_STAGES = \[(.*?)\]", _dashboard("types.ts"), re.S)
    assert stages, "types.ts no longer declares CASCADE_STAGES"

    assert re.findall(r'"([^"]+)"', stages.group(1)) == hub.mod.CASCADE_STAGES

    # ...and the card's own row table names no boundary outside that list. Checked on the
    # quoted literals rather than the whole file, so a comment saying "the card renders X"
    # is not a failure — it is `key: "render"` in FUNNEL_ROWS that would be.
    rows = re.search(r"FUNNEL_ROWS = \[(.*?)\n\] as const", _dashboard("cascadeFunnel.ts"), re.S)
    assert rows, "cascadeFunnel.ts no longer declares FUNNEL_ROWS"
    assert "render" not in re.findall(r'"([^"]+)"', rows.group(1))
