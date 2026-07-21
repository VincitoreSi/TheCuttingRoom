"""Running the pipeline on a timer, without letting it run up a bill or a stampede.

There is no daemon outside the hub — by design, since the whole project is local-first and
depends on no cron. So a schedule is best-effort "while the hub is up", and everything here
is about the two ways an unattended loop goes wrong: spending money nobody asked it to, and
firing again while the previous run is still going (or every time the hub restarts).
"""
import json
import time

import pytest


def _sched(hub, platform="instagram"):
    return hub.get("/api/schedule").json()[platform]


def _put(hub, **body):
    r = hub.put("/api/schedule/instagram", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _pages(hub, *handles):
    (hub.root / "platforms" / "instagram" / "pages.txt").write_text(
        "\n".join(handles) + "\n", encoding="utf-8")


@pytest.fixture
def launched(hub, monkeypatch):
    """Record run-all launches instead of spawning supervisor threads."""
    calls = []
    monkeypatch.setattr(hub.mod, "_start_run_all",
                        lambda p, stages, trigger="manual": calls.append((p, list(stages), trigger))
                        or f"{p}:run-all:1")
    return calls


# ---------------------------------------------------------------- defaults & config

def test_scheduling_is_off_on_a_fresh_install(hub):
    row = _sched(hub)

    assert row["enabled"] is False
    assert row["include_blueprints"] is False


def test_an_unreadable_schedule_file_fails_closed(hub):
    (hub.root / "config").mkdir(parents=True, exist_ok=True)
    (hub.root / "config" / "pipeline_schedule.json").write_text("{not json", encoding="utf-8")

    assert _sched(hub)["enabled"] is False


def test_a_hand_edited_schedule_that_reads_false_is_false(hub, launched):
    """`bool("false")` is True. A file that literally says `false` — what a jq edit or a
    YAML->JSON conversion produces — used to switch the timer on AND opt it into the paid
    stage. Only a real boolean is an answer here."""
    (hub.root / "config").mkdir(parents=True, exist_ok=True)
    (hub.root / "config" / "pipeline_schedule.json").write_text(json.dumps({
        "instagram": {"enabled": "false", "include_blueprints": "false",
                      "every_hours": 24, "last_run_at": 0}}), encoding="utf-8")

    row = _sched(hub)

    assert row["enabled"] is False
    assert row["include_blueprints"] is False
    assert hub.mod._schedule_tick(now=time.time() + 25 * 3600) == []


def test_enabling_starts_the_clock_rather_than_firing_immediately(hub, launched):
    """last_run_at defaults to 0, so without this, switching the schedule on would look
    overdue by 55 years and start a scrape on the spot."""
    _put(hub, enabled=True, every_hours=24)

    assert hub.mod._schedule_tick() == []


def test_an_interval_below_an_hour_is_clamped(hub):
    assert _put(hub, every_hours=0)["every_hours"] == 1.0


# ---------------------------------------------------------------- the paid stage

def test_a_scheduled_run_does_not_spend_api_credits_by_default(hub, launched):
    """analysis-engine calls a paid API per clip. Unattended and daily, that is a bill
    nobody agreed to — the same reasoning that keeps `render` out of RUN_ALL_STAGES."""
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)

    hub.mod._schedule_tick(now=time.time() + 25 * 3600)

    assert launched[0][1] == ["scrape", "analyze", "media"]
    assert "analysis-engine" not in launched[0][1]


def test_blueprints_can_be_opted_into(hub, launched):
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24, include_blueprints=True)

    hub.mod._schedule_tick(now=time.time() + 25 * 3600)

    assert launched[0][1][-1] == "analysis-engine"


# ---------------------------------------------------------------- firing rules

def test_a_due_platform_runs_and_an_undue_one_does_not(hub, launched):
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)

    assert hub.mod._schedule_tick(now=time.time() + 23 * 3600) == []
    assert hub.mod._schedule_tick(now=time.time() + 25 * 3600) == ["instagram"]


def test_the_clock_is_stamped_before_launch_so_a_long_run_cannot_double_fire(hub, launched):
    """A scrape can outlast the tick interval. If last_run_at were written after the run
    finished, every tick in between would see it as still overdue and start another."""
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)
    t = time.time() + 25 * 3600

    hub.mod._schedule_tick(now=t)
    hub.mod._schedule_tick(now=t + 60)

    assert len(launched) == 1


def test_the_schedule_survives_a_hub_restart_without_re_firing(hub, launched):
    """last_run_at is persisted, so the interval is not measured from process start —
    otherwise restarting the hub five times would mean five scrapes."""
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)
    t = time.time() + 25 * 3600
    hub.mod._schedule_tick(now=t)
    launched.clear()

    on_disk = json.loads((hub.root / "config" / "pipeline_schedule.json").read_text())
    assert on_disk["instagram"]["last_run_at"] > 0
    assert hub.mod._schedule_tick(now=t + 3600) == []


def test_a_platform_with_no_watchlist_is_skipped_quietly(hub, launched):
    """Starting a run that exists only to fail would paint the board red on a timer."""
    _put(hub, enabled=True, every_hours=24)

    assert hub.mod._schedule_tick(now=time.time() + 25 * 3600) == []
    assert launched == []


def test_a_skipped_platform_does_not_spin(hub, launched):
    """The clock still moves, so an unwatched platform is reconsidered next interval —
    not on every single tick."""
    _put(hub, enabled=True, every_hours=24)
    t = time.time() + 25 * 3600
    hub.mod._schedule_tick(now=t)

    assert hub.mod._schedule_tick(now=t + 60) == []


def test_a_run_already_in_flight_is_not_disturbed(hub, monkeypatch):
    """Overlapping a manual run is expected, not an error."""
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)
    hub.mod._RUNNING_ALL.add("instagram")
    try:
        assert hub.mod._schedule_tick(now=time.time() + 25 * 3600) == []
    finally:
        hub.mod._RUNNING_ALL.discard("instagram")


def test_disabling_stops_it(hub, launched):
    _pages(hub, "example_one")
    _put(hub, enabled=True, every_hours=24)
    _put(hub, enabled=False)

    assert hub.mod._schedule_tick(now=time.time() + 400 * 3600) == []


def test_the_next_run_time_is_reported_for_the_ui(hub):
    _pages(hub, "example_one")
    row = _put(hub, enabled=True, every_hours=24)
    row = _sched(hub)

    assert row["next_run_at"] == pytest.approx(row["last_run_at"] + 24 * 3600)
    assert row["stages"] == ["scrape", "analyze", "media"]
