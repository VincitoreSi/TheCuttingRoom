"""Refusing a run that cannot work, and saying why when one fails.

Two things used to go wrong together. The hub validated only the stage NAME, so every Run
was launchable and a doomed stage failed four seconds later in a subprocess. And when it
did fail, the tail kept `stdout or stderr` — an `or` — so any stage that had printed a
progress line to stdout had its stderr thrown away, taking the actual reason with it. A red
node whose tail was the last routine progress line was the entire diagnostic surface.
"""
import json

import pytest


def _pages(hub, *handles):
    (hub.root / "platforms" / "instagram" / "pages.txt").write_text(
        "\n".join(handles) + "\n", encoding="utf-8")


def _raw(hub):
    (hub.root / "platforms" / "instagram" / "reels_raw.json").write_text(
        '{"someone": []}', encoding="utf-8")


# ------------------------------------------------------------------ the failure tail

def test_the_tail_keeps_stderr_even_when_the_stage_also_printed_progress(hub):
    """The regression. Losing stderr here is losing the only explanation that exists."""
    tail = hub.mod._job_tail("scraping page 21\nscraping page 22\n",
                             "ERROR: no scraped data — scrape first\n")

    assert "no scraped data" in tail
    assert "scraping page 22" in tail


def test_stderr_survives_a_stage_that_floods_stdout(hub):
    """Truncation has to eat the routine progress, never the error — so stderr goes last."""
    tail = hub.mod._job_tail("x" * 50_000, "ERROR: the actual reason\n")

    assert tail.endswith("ERROR: the actual reason")
    assert len(tail) <= hub.mod.TAIL_CHARS


def test_a_stage_that_only_wrote_to_one_stream_still_reports(hub):
    assert "only stdout" in hub.mod._job_tail("only stdout", "")
    assert "only stderr" in hub.mod._job_tail("", "only stderr")
    assert hub.mod._job_tail("", "") == ""


def test_a_crashed_stage_reports_a_real_return_code(hub, monkeypatch):
    """`_run_stage_blocking` hands its rc to the run-all supervisor, which reads None as
    "unknown stage — skip cleanly". A stage that crashed outright therefore let the whole
    rest of the pipeline run on as if nothing had happened."""
    def boom(*a, **k):
        raise OSError("uv not found")
    monkeypatch.setattr(hub.mod.subprocess, "run", boom)
    hub.mod.JOBS["instagram:scrape:99"] = {"platform": "instagram", "stage": "scrape",
                                           "status": "queued", "started": 0, "ended": None,
                                           "rc": None, "tail": ""}

    hub.mod._run_job("instagram:scrape:99", ["x"], hub.root)

    job = hub.mod.JOBS["instagram:scrape:99"]
    assert job["status"] == "error"
    assert job["rc"] is not None and job["rc"] != 0
    assert "uv not found" in job["tail"]


# ------------------------------------------------------------------ guard rails

def test_a_stage_whose_input_is_missing_is_refused_with_the_reason(hub):
    r = hub.post("/api/pipeline/instagram/analyze")

    assert r.status_code == 409
    assert "scrape" in r.json()["detail"].lower()


def test_scrape_is_refused_while_the_watchlist_is_empty(hub):
    r = hub.post("/api/pipeline/instagram/scrape")

    assert r.status_code == 409
    assert "watchlist" in r.json()["detail"].lower()


def test_force_is_the_escape_hatch(hub, monkeypatch):
    """Readiness is a convenience, not a security boundary — an operator who knows better
    must be able to override it."""
    launched = []
    monkeypatch.setattr(hub.mod, "_launch_stage_job",
                        lambda p, s, **k: launched.append((p, s)) or "job-1")

    assert hub.post("/api/pipeline/instagram/analyze?force=true").status_code == 200
    assert launched == [("instagram", "analyze")]


def test_a_ready_stage_is_not_blocked(hub, monkeypatch):
    _pages(hub, "example_one")
    monkeypatch.setattr(hub.mod, "_launch_stage_job", lambda p, s, **k: "job-1")

    assert hub.post("/api/pipeline/instagram/scrape").status_code == 200


# ------------------------------------------------------------------ run-all

def test_run_all_resolves_to_the_run_all_route_at_all(hub):
    """`/api/pipeline/{platform}/{stage}` was registered ~100 lines ABOVE the literal
    /run-all route, and Starlette matches in registration order — so every "Run full
    pipeline" click was answered 400 "stage must be one of [...]" by the catch-all and no
    pipeline was ever started. The Dashboard defines no onError, so the click looked inert.

    Any status other than 400-with-that-detail means the route is being reached."""
    r = hub.post("/api/pipeline/instagram/run-all")

    assert not (r.status_code == 400 and "stage must be one of" in r.text)


def test_run_all_is_refused_when_its_first_stage_cannot_run(hub):
    """Better than spawning a supervisor that dies on stage one and leaves the board red."""
    r = hub.post("/api/pipeline/instagram/run-all")

    assert r.status_code == 409
    assert "watchlist" in r.json()["detail"].lower()


def test_a_second_run_all_is_refused_while_one_is_in_flight(hub, monkeypatch):
    """Two supervisors over one reels_raw.json is a corrupt corpus, not a faster run."""
    _pages(hub, "example_one")
    monkeypatch.setattr(hub.mod.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: None,
                                                       "daemon": True})())

    assert hub.post("/api/pipeline/instagram/run-all").status_code == 200
    second = hub.post("/api/pipeline/instagram/run-all")

    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]


def test_a_refused_run_all_does_not_wedge_the_in_flight_flag(hub):
    """The flag is claimed before the readiness check; a 409 there must release it, or the
    platform can never run again without a hub restart."""
    hub.post("/api/pipeline/instagram/run-all")          # 409, watchlist empty
    _pages(hub, "example_one")

    assert hub.mod._RUNNING_ALL == set()


def test_every_stage_of_one_run_carries_the_same_run_id(hub, monkeypatch):
    """`run_id` was generated, returned, and attached to nothing — so nothing could tell
    which stages belonged to one "Run full pipeline" click."""
    seen = []
    monkeypatch.setattr(hub.mod, "_run_job",
                        lambda job_id, cmd, cwd: seen.append(hub.mod.JOBS[job_id]["run_id"]))

    hub.mod._run_all_supervisor("instagram", "run-7", ["scrape", "analyze"])

    assert seen == ["run-7", "run-7"]


def test_a_halted_run_says_on_the_activity_log_where_it_stopped(hub, monkeypatch):
    """One red stage followed by silence reads identically to a run still in progress."""
    def fail(platform, stage, run_id=None):
        return 0 if stage == "scrape" else 1
    monkeypatch.setattr(hub.mod, "_run_stage_blocking", fail)

    hub.mod._run_all_supervisor("instagram", "run-8", ["scrape", "analyze", "media"])

    recs = [json.loads(l) for l in
            (hub.root / "logs" / "agents.jsonl").read_text(encoding="utf-8").splitlines()]
    halt = [r for r in recs if r.get("event") == "run_halted"]
    assert len(halt) == 1
    assert "analyze" in halt[0]["msg"]
    assert halt[0]["data"]["skipped"] == ["media"]      # and says what never ran


def test_the_in_flight_flag_is_released_even_if_a_stage_explodes(hub, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(hub.mod, "_run_stage_blocking", boom)
    hub.mod._RUNNING_ALL.add("instagram")

    with pytest.raises(RuntimeError):
        hub.mod._run_all_supervisor("instagram", "run-9", ["scrape"])

    assert "instagram" not in hub.mod._RUNNING_ALL
