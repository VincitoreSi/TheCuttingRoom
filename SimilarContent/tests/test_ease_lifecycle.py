"""cli.py::_ease_lifecycle — the ONLY code path in this agent that writes hub config.

engine/propose.py decides WHAT the gate did; this decides what a run is allowed to do about
it. Everything worth guarding is here rather than in the scoring:

  * a starved gate is reported and never acted on — the message says what to lower the
    threshold to, and a human does it;
  * the only automatic change is putting the threshold BACK to a value a human chose, and it
    goes through `automation_threshold`, which cannot return anything lower;
  * `--dry-run` writes nothing at all, including the bookkeeping.

The fake hub records every write, so "changed nothing" is asserted as an empty list rather
than inferred from the absence of a failure.
"""
from types import SimpleNamespace

import pytest

import cli
from engine.hub import ConfigConflict, HubError, _same_value
from engine.propose import Ease, Target


class FakeHub:
    """Records config writes and log posts; `fail` makes every write raise, the way a hub
    that went away mid-run does.

    `stored` models what is IN THE HUB right now, so a test can move a knob mid-run the way
    an operator with the Agent Desk open does — the write path's compare-and-set is only
    meaningful against a hub that can disagree with the run's snapshot.
    """

    def __init__(self, fail: bool = False, stored: dict | None = None):
        self.config_writes: list[dict] = []
        self.logs: list[dict] = []
        self.fail = fail
        self.stored = dict(stored or {})

    def update_agent_config(self, agent, updates, expect=None):
        if self.fail:
            raise HubError("PUT /api/config/agent/similar-content -> 500")
        for key, want in (expect or {}).items():
            if key in self.stored and not _same_value(self.stored[key], want):
                raise ConfigConflict(f"{key} is {self.stored[key]!r}, not {want!r}")
        self.config_writes.append(dict(updates))
        self.stored.update(updates)
        return {"ok": True}

    def post_log(self, agent, event, **kw):
        self.logs.append({"event": event, **kw})


def _args(dry_run=False):
    return SimpleNamespace(platform="instagram", dry_run=dry_run)


def _targets(scores, threshold):
    """Candidates that HAVE a blueprint: a pool of un-analyzed clips is a different
    diagnosis (ease is duration-only there and the run must not advise lowering the gate)."""
    return [Target(row={}, blueprint={"content_id": f"bp{i}", "shots": [{"shot_index": 1}]},
                   ease=Ease(score=float(s), easy=s >= threshold, threshold=threshold),
                   virality_score=float(i))
            for i, s in enumerate(scores)]


def _run(hub, *, scores, threshold, count=5, restore_to=None, auto_restore=False,
         dry_run=False, ad_hoc=False):
    targets = _targets(scores, threshold)
    effective = cli._ease_lifecycle(hub, _args(dry_run), "sc-test", targets, count=count,
                                    threshold=threshold, restore_to=restore_to,
                                    auto_restore=auto_restore, ad_hoc=ad_hoc)
    return effective, targets


# ---- starved: report, never act ---------------------------------------------------------
def test_a_starved_gate_reports_and_writes_no_threshold(capsys):
    hub = FakeHub()
    effective, _ = _run(hub, scores=[40] * 15, threshold=55)
    assert effective == 55
    assert not any("ease_threshold" in w for w in hub.config_writes)
    out = capsys.readouterr().out
    assert "0 of 15 candidates cleared ease >= 55" in out
    assert "Lower ease_threshold to 40" in out


def test_the_starved_report_goes_to_the_hub_with_structured_data():
    hub = FakeHub()
    _run(hub, scores=[40] * 15, threshold=55)
    [entry] = [line for line in hub.logs if line["event"] == "ease.starved"]
    assert entry["level"] == "warning"
    assert entry["data"]["considered"] == 15 and entry["data"]["cleared"] == 0
    assert entry["data"]["best_score"] == 40 and entry["data"]["suggest_threshold"] == 40
    assert entry["data"]["applied_threshold"] is None


def test_even_auto_restore_cannot_make_a_starved_run_lower_the_gate():
    """The one thing that must be impossible by construction (D3)."""
    hub = FakeHub()
    effective, _ = _run(hub, scores=[40] * 15, threshold=55, restore_to=55,
                        auto_restore=True)
    assert effective == 55
    for write in hub.config_writes:
        assert write.get("ease_threshold", 55) >= 55


# ---- D6: lowering records where it came from --------------------------------------------
def test_a_lowered_threshold_records_its_origin():
    hub = FakeHub()
    _run(hub, scores=[40] * 15, threshold=40)
    assert hub.config_writes == [{"ease_restore_to": 55}]


def test_the_origin_is_recorded_once_not_re_written_every_run():
    hub = FakeHub()
    _run(hub, scores=[40] * 15, threshold=40, restore_to=55)
    assert hub.config_writes == []


def test_recording_an_origin_yields_to_a_target_a_human_set_mid_run(capsys):
    """The bookkeeping write is guarded too: an operator who types a restore target while the
    run is scoring keeps it, rather than having 55 written over it seconds later."""
    hub = FakeHub(stored={"ease_restore_to": 45})
    _run(hub, scores=[40] * 15, threshold=40, restore_to=None)
    assert hub.config_writes == [] and hub.stored["ease_restore_to"] == 45
    assert "could not record ease_restore_to" in capsys.readouterr().err


def test_bookkeeping_failure_is_not_fatal(capsys):
    hub = FakeHub(fail=True)
    effective, _ = _run(hub, scores=[40] * 15, threshold=40)
    assert effective == 40
    assert "could not record ease_restore_to" in capsys.readouterr().err


# ---- D4/D5: restore is prompted, then opt-in --------------------------------------------
def test_restore_ready_prompts_and_writes_nothing(capsys):
    hub = FakeHub()
    effective, targets = _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5,
                              restore_to=55)
    assert effective == 40                       # unchanged: prompt-first
    assert hub.config_writes == []
    assert all(t.ease.threshold == 40 for t in targets)
    assert "restore ease_threshold to 55" in capsys.readouterr().out


def test_auto_restore_raises_the_threshold_and_clears_the_record():
    hub = FakeHub()
    effective, targets = _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5,
                              restore_to=55, auto_restore=True)
    assert effective == 55
    assert hub.config_writes == [{"ease_threshold": 55, "ease_restore_to": None}]
    # the run's own candidates are re-gated: the scores never moved, the verdicts did
    assert [t.ease.score for t in targets] == [60.0] * 12 + [10.0] * 3
    assert sum(1 for t in targets if t.ease.easy) == 12


def test_a_failed_restore_keeps_running_at_the_old_threshold(capsys):
    hub = FakeHub(fail=True)
    effective, targets = _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5,
                              restore_to=55, auto_restore=True)
    assert effective == 40
    assert all(t.ease.threshold == 40 for t in targets)
    assert "restore could not be saved" in capsys.readouterr().err


# ---- the write may never contradict what actually happened -------------------------------
def test_a_failed_restore_is_never_reported_as_a_restore(capsys):
    """`msg` and `kind` used to be decided BEFORE the write was attempted, so a hub that 500s
    left the run printing "RESTORED ease_threshold 40 -> 55" and posting `ease.restored` at
    level info — while the gate was still 40 and every pick had been ranked at 40. The
    Dashboard would render a restore that never happened."""
    hub = FakeHub(fail=True)
    _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5, restore_to=55,
         auto_restore=True)
    out = capsys.readouterr().out
    assert "RESTORED" not in out
    assert "ease_threshold is still 40" in out and "restore it to 55" in out
    assert [line["event"] for line in hub.logs] == ["ease.restore_ready"]
    assert hub.logs[0]["data"]["applied_threshold"] is None


def test_a_dry_run_restore_is_reported_in_the_conditional(capsys):
    hub = FakeHub()
    _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5, restore_to=55,
         auto_restore=True, dry_run=True)
    out = capsys.readouterr().out
    assert "[dry-run] would restore ease_threshold 40 -> 55" in out
    assert "RESTORED" not in out
    assert [line["event"] for line in hub.logs] == ["ease.restore_ready"]


def test_a_restore_never_overwrites_a_threshold_a_human_changed_mid_run(capsys):
    """THE safety asymmetry, at the only place it can actually break (D3).

    `automation_threshold` is a max() against the value the run read AT START, and a propose
    run spends seconds fetching a corpus, an analysis listing and up to `pool` blueprints
    before it writes. Snapshot 40 with a recorded origin of 55; operator raises the gate to
    70 in the Agent Desk during that window; `max(40, 55)` is still 55 and the write would
    store 55 — automation LOWERING a gate a human just raised, silently, because the stored
    document is a diff against the defaults and 55 IS the default, so the 70 leaves no trace.
    """
    hub = FakeHub(stored={"ease_threshold": 70})
    effective, targets = _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5,
                              restore_to=55, auto_restore=True)
    assert hub.config_writes == []                  # nothing was written at all
    assert hub.stored["ease_threshold"] == 70       # the human's value still stands
    assert effective == 40                          # this run keeps the gate it scored at
    assert all(t.ease.threshold == 40 for t in targets)
    assert "restore abandoned" in capsys.readouterr().err
    assert [line["event"] for line in hub.logs] == ["ease.restore_ready"]


# ---- a restore may only ever aim at a target that PREDATES the run -----------------------
def test_a_freshly_recorded_origin_is_not_acted_on_by_the_run_that_recorded_it(capsys):
    """D5, prompt-first. The origin was recorded and then immediately fed back into the
    restore decision, so with ease_auto_restore on, a human lowering the gate to 40 had it
    put back to 55 by the very next run — two writes whose net effect is "undo the operator",
    with nothing learned about the corpus in between. They could never hold the gate below
    the default while the pool supported it."""
    hub = FakeHub()
    effective, _ = _run(hub, scores=[60] * 12 + [10] * 3, threshold=40, count=5,
                        restore_to=None, auto_restore=True)
    assert effective == 40
    assert hub.config_writes == [{"ease_restore_to": 55}]     # recorded, not acted on
    assert not any("ease_threshold" in w for w in hub.config_writes)
    # ...and the run says the corpus now supports it, which is the prompt D5 asks for.
    assert "restore ease_threshold to 55" in capsys.readouterr().out


# ---- D4's margin is about the CONFIGURED pool, not a hand-typed one ----------------------
def test_a_hand_narrowed_run_reports_but_never_restores(capsys):
    """`--count 2` over a pool of 15 with three clips above the record satisfies "more than
    count" and used to restore the gate and clear `ease_restore_to` permanently — starving
    every scheduled run at `top_n: 5`, which had correctly declined to restore. A run whose
    pool and count were typed by hand cannot speak for the corpus."""
    hub = FakeHub()
    effective, _ = _run(hub, scores=[60] * 3 + [10] * 12, threshold=40, count=2,
                        restore_to=55, auto_restore=True, ad_hoc=True)
    assert effective == 40 and hub.config_writes == []
    assert [line["event"] for line in hub.logs] == ["ease.restore_ready"]
    assert "narrowed by hand" in capsys.readouterr().out
    # ...the same pool and count on a normal run is exactly what D4 means to act on.
    hub2 = FakeHub()
    effective2, _ = _run(hub2, scores=[60] * 3 + [10] * 12, threshold=40, count=2,
                         restore_to=55, auto_restore=True)
    assert effective2 == 55 and hub2.config_writes == [
        {"ease_threshold": 55, "ease_restore_to": None}]


# ---- the knobs are parsed strictly, because the hub does not validate them ---------------
def test_ease_auto_restore_is_parsed_strictly_so_a_string_false_stays_off():
    """`bool("false")` is True, and this is the knob that decides whether a run may write the
    threshold at all. The hub stores `body.config` verbatim, so a curl or a hand-edited
    similar-content.json really can put the string there."""
    assert cli._as_bool("false", False) is False
    assert cli._as_bool("0", False) is False and cli._as_bool("no", False) is False
    assert cli._as_bool("true", False) is True and cli._as_bool(True, False) is True
    assert cli._as_bool("banana", False) is False      # unparseable -> the default, not True
    assert cli._as_bool(None, False) is False


def test_integer_knobs_reject_booleans_and_stay_inside_the_schema_range(capsys):
    """`ease_threshold: true` silently became a gate of 1, where every clip is "easy" and the
    ease ranking is noise. The hub enforces neither `type` nor `minimum`/`maximum`."""
    assert cli._as_int(True, 55, name="ease_threshold") == 55
    assert cli._as_int(-20, 55, name="ease_threshold", lo=0, hi=100) == 0
    assert cli._as_int(500, 55, name="ease_threshold", lo=0, hi=100) == 100
    assert cli._as_int("70", 55, name="ease_threshold", lo=0, hi=100) == 70
    assert "ease_threshold" in capsys.readouterr().err        # corrections are never silent

    assert cli._as_int_or_none(True) is None
    assert cli._as_int_or_none(-1) is None and cli._as_int_or_none(101) is None
    assert cli._as_int_or_none("55") == 55 and cli._as_int_or_none("") is None


# ---- --dry-run writes nothing, ever ------------------------------------------------------
@pytest.mark.parametrize("kwargs", [
    {"scores": [40] * 15, "threshold": 40},                                   # would record
    {"scores": [60] * 12 + [10] * 3, "threshold": 40, "restore_to": 55,
     "auto_restore": True},                                                   # would restore
])
def test_dry_run_never_touches_the_config(kwargs, capsys):
    hub = FakeHub()
    effective, _ = _run(hub, dry_run=True, **kwargs)
    assert hub.config_writes == []
    assert effective == kwargs["threshold"]
    assert "[dry-run] would" in capsys.readouterr().out
    assert all(line["data"]["dry_run"] for line in hub.logs)
