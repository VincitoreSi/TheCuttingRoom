#!/usr/bin/env python3
"""tests/test_discover_events.py — the discovery run must EXPLAIN itself.

The symptom this locks down: a Discover run in the default posture (guest-only, no burner
session) skips Instagram's login-gated search surface for every term, finds nobody, and
exits "proposed 0" in seconds. Before this feature the only trace of *why* was a line in a
local logfile the hub never reads — the board showed an empty state with no reason.

These pin the contract that fixed it:

  * `discover_via_terms` emits per-query lifecycle events through an INJECTED callback
    (never importing the hub — same discipline as `on_candidate`): `query.start` /
    `query.skip` (guest-only) / `query.result` for every term, so a 0-candidate run is a
    trail of "searched X, found 0", not silence.
  * `cmd_run` stamps `run.start` with a PLAN (mode, surface, term-expansion, term count,
    caps) and, when running guest-only, posts ONE first-class reason event that names the
    fix (add IG_SESSIONID + guest_only=false).
  * `run.end` carries the COUNTS the Dashboard's "last run" strip reads, and a terminal
    `reason` — `guest_only_no_search` for the default no-op posture.
"""
from __future__ import annotations

import cli
from engine import search as searchlib
from engine.circuit import CircuitBreaker


# --------------------------------------------------------------- discover_via_terms (unit)

def test_guest_only_discovery_emits_per_query_events_with_zero_found(monkeypatch):
    """The core no-op path: guest-only, so topsearch is skipped for every term and nobody is
    hydrated. Every term must still leave a `query.start` -> `query.skip` -> `query.result
    found=0` trail so the run is observable rather than silent."""
    events: list[tuple[str, dict]] = []

    def on_event(event, *, level="info", content_id=None, msg=None, data=None, throttle=None):
        events.append((event, data or {}))

    terms = ["alpha", "beta"]
    cfg = {"guest_only": True, "per_term_limit": 5, "min_followers": 2000}
    breaker = CircuitBreaker(max_strikes=3, pace_seconds=0.0)
    budget = searchlib.Budget(cfg)

    # No burner, guest-only: the branch that hits the network is never taken, so the fake
    # guest session is only a placeholder and no ig.* call happens.
    cands = searchlib.discover_via_terms(
        terms, cfg, "instagram", guest=object(), burner=None,
        breaker=breaker, budget=budget, on_event=on_event,
    )

    assert cands == []
    kinds = [e for e, _ in events]
    assert kinds.count("query.start") == 2
    assert kinds.count("query.skip") == 2
    assert kinds.count("query.result") == 2
    # each result reports found=0 and hydrated=0 for the guest-only term
    for ev, data in events:
        if ev == "query.result":
            assert data.get("found") == 0, data
            assert data.get("hydrated") == 0, data


def test_query_skip_names_the_guest_only_reason(monkeypatch):
    events: list[tuple[str, dict, str]] = []

    def on_event(event, *, level="info", content_id=None, msg=None, data=None, throttle=None):
        events.append((event, data or {}, msg or ""))

    cfg = {"guest_only": True, "per_term_limit": 5}
    budget = searchlib.Budget(cfg)
    searchlib.discover_via_terms(
        ["term"], cfg, "instagram", guest=object(), burner=None,
        breaker=CircuitBreaker(max_strikes=3, pace_seconds=0.0), budget=budget, on_event=on_event,
    )
    skip = next(e for e in events if e[0] == "query.skip")
    assert skip[1].get("reason") == "guest_only"
    assert "guest-only" in skip[2].lower() and "burner" in skip[2].lower()


def test_discover_via_terms_without_on_event_is_a_noop(monkeypatch):
    """The callback is optional (default None) exactly like on_candidate — omitting it must
    not raise."""
    cfg = {"guest_only": True}
    searchlib.discover_via_terms(
        ["t"], cfg, "instagram", guest=object(), burner=None,
        breaker=CircuitBreaker(max_strikes=3, pace_seconds=0.0), budget=searchlib.Budget(cfg),
    )


# --------------------------------------------------------------- cmd_run (integration)

class _FakeHub:
    """Records every POST /api/logs. Guest-only, discovery ON, so cmd_run runs the real
    no-op path with no network."""

    def __init__(self, base, *a, **k):
        self.base = base
        self.logs: list[dict] = []
        self.candidates: list[dict] = []
        self.insights: list[dict] = []

    def health_ok(self):
        return True

    def foreign_checkout(self):
        return None

    def register_producer(self, manifest):
        return {}

    def get_agent_config(self, agent):
        return {"config": {"discovery_enabled": True, "guest_only": True}}

    def platform_config(self, platform):
        return {"config": {"niche": "fashion", "discovery": {"keywords": ["ootd", "streetwear"]}}}

    def factors(self, platform):
        return None

    def list_insights(self):
        return []

    def post_candidate(self, platform, cand):
        self.candidates.append(cand)
        return {}

    def post_insight(self, *a, **k):
        return {}

    def post_log(self, agent, event, *, level="info", run_id=None, platform=None,
                 content_id=None, msg=None, data=None):
        self.logs.append({"agent": agent, "event": event, "level": level, "run_id": run_id,
                          "platform": platform, "content_id": content_id, "msg": msg,
                          "data": data or {}})


class _FakeGuest:
    def __init__(self, *a, **k):
        self.cookie = {}
        self.csrf = "x"

    def bootstrap(self):
        return True


def _run_guest_only(monkeypatch):
    monkeypatch.setattr(cli, "HubClient", _FakeHub)
    monkeypatch.setattr(cli.ig, "GuestSession", _FakeGuest)
    # guest_only=true means load_burner_session is never called, but guard anyway.
    monkeypatch.setattr(cli.ig, "load_burner_session", lambda *a, **k: None)

    class _Args:
        platform = "instagram"

    rc = cli.cmd_run(_Args())
    return rc


def _events_by(logs, event):
    return [row for row in logs if row["event"] == event]


def test_run_start_carries_the_plan(monkeypatch):
    hub = {}

    def _capture(base, *a, **k):
        hub["h"] = _FakeHub(base)
        return hub["h"]

    monkeypatch.setattr(cli, "HubClient", _capture)
    monkeypatch.setattr(cli.ig, "GuestSession", _FakeGuest)
    monkeypatch.setattr(cli.ig, "load_burner_session", lambda *a, **k: None)

    class _Args:
        platform = "instagram"

    cli.cmd_run(_Args())
    start = _events_by(hub["h"].logs, "run.start")
    assert start, "no run.start emitted"
    data = start[0]["data"]
    assert data.get("mode") == "run"
    assert data.get("surface") == "guest-only"
    assert data.get("term_expansion") is False
    assert isinstance(data.get("terms"), int) and data["terms"] >= 1
    assert data.get("per_term_limit") == 5
    assert "min_followers" in data


def test_guest_only_run_posts_a_first_class_reason_event(monkeypatch):
    hub = {}
    monkeypatch.setattr(cli, "HubClient", lambda base, *a, **k: hub.setdefault("h", _FakeHub(base)))
    monkeypatch.setattr(cli.ig, "GuestSession", _FakeGuest)
    monkeypatch.setattr(cli.ig, "load_burner_session", lambda *a, **k: None)

    class _Args:
        platform = "instagram"

    cli.cmd_run(_Args())
    reasons = [r for r in hub["h"].logs
               if (r["data"] or {}).get("reason") == "no_burner_session"]
    assert reasons, "expected one first-class no-burner reason event"
    msg = (reasons[0]["msg"] or "").lower()
    assert "ig_sessionid" in msg and "guest_only" in msg


def test_run_end_carries_counts_and_terminal_reason(monkeypatch):
    hub = {}
    monkeypatch.setattr(cli, "HubClient", lambda base, *a, **k: hub.setdefault("h", _FakeHub(base)))
    monkeypatch.setattr(cli.ig, "GuestSession", _FakeGuest)
    monkeypatch.setattr(cli.ig, "load_burner_session", lambda *a, **k: None)

    class _Args:
        platform = "instagram"

    cli.cmd_run(_Args())
    end = _events_by(hub["h"].logs, "run.end")
    assert end, "no run.end emitted"
    data = end[0]["data"]
    for key in ("terms_run", "raw_found", "hydrated", "passed_gates", "proposed"):
        assert key in data, f"run.end missing count {key}: {data}"
    assert data["proposed"] == 0
    assert data["passed_gates"] == 0
    assert data["raw_found"] == 0
    assert data["reason"] == "guest_only_no_search", data


def test_per_query_events_reach_the_hub_during_a_run(monkeypatch):
    hub = {}
    monkeypatch.setattr(cli, "HubClient", lambda base, *a, **k: hub.setdefault("h", _FakeHub(base)))
    monkeypatch.setattr(cli.ig, "GuestSession", _FakeGuest)
    monkeypatch.setattr(cli.ig, "load_burner_session", lambda *a, **k: None)

    class _Args:
        platform = "instagram"

    cli.cmd_run(_Args())
    kinds = [r["event"] for r in hub["h"].logs]
    assert "query.start" in kinds
    assert "query.result" in kinds
    # two seed keywords -> at least two query.result rows
    assert kinds.count("query.result") >= 2
