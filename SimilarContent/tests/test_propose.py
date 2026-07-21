"""Proposal generation — the half of the producer that decides WHAT gets cloned.

Grounded in the real corpus CSV and the real schema-2 blueprints on disk rather than invented
fixtures, because the ease heuristic's whole job is to survive the hub's actual output (every
corpus field arrives as a *string*, blueprints are frequently absent).

The round-trip test is the load-bearing one: build_recipe() and engine/recipe.py::parse_recipe()
are a matched pair, and a silent drift between them would produce recipes that parse into half
a video at $0.04 a frame.
"""
import pytest

from engine.propose import (
    EASE_LONG_S, EASE_THRESHOLD, ProposeError, automation_threshold, build_recipe,
    clip_duration, content_id_index, diagnose_ease, rank_targets, recipe_filename,
    restore_origin, score_ease, score_targets, select_targets, shortcode, Ease, Target,
)
from engine.recipe import RecipeError, parse_recipe

# The blueprints these tests run against are selected by SHAPE (fewest shots / most shots)
# by the fixtures in conftest.py, never by a hard-coded content_id. An Instagram content_id is
# `<media_pk>_<user_pk>` and its trailing half permanently identifies a real account, so those
# ids must not be committed. Selecting on shape also means these tests exercise whatever
# dataset the operator actually has, instead of skipping unless it is one specific corpus.
# Synthetic shortcodes below (AAA/BBB/ZZZ/…) are placeholders and join to nothing real.


# ---- ease scoring ----------------------------------------------------------------------
def _bp(n_shots: int, duration: float, static: int | None = None, cam: str = "Static",
        style: dict | None = None) -> dict:
    """A blueprint of a given SHAPE — n shots, a duration, and how many of those shots hold
    the camera still. Synthetic on purpose: these are the monotonicity tests, and they have
    to reach shapes (1 shot, 12 shots, 40s, half-static) that no real corpus need contain."""
    static = n_shots if static is None else static
    return {
        "content_id": f"shape_{n_shots}_{duration}_{static}",
        "video_metadata": {"estimated_duration_seconds": duration},
        "global_style": style or {},
        "shots": [{"shot_index": i + 1,
                   "camera_movement": cam if i < static else "Slow dolly in"}
                  for i in range(n_shots)],
    }


def test_single_short_static_shot_is_easy():
    """The shape the whole rule exists to find, and the one this corpus does not contain —
    so it is asserted synthetically rather than left to skip."""
    ease = score_ease({}, _bp(1, 6.0))
    assert ease.easy and ease.score >= EASE_THRESHOLD
    assert any(r.startswith("single-shot") for r in ease.reasons)


def test_a_real_single_shot_clip_is_easy(single_shot_blueprint):
    ease = score_ease({"duration_s": "6.22"}, single_shot_blueprint)
    assert ease.easy
    assert ease.score >= EASE_THRESHOLD
    assert any(r.startswith("single-shot") for r in ease.reasons)


def test_multi_shot_clip_is_not_easy(hardest_blueprint):
    n = len(hardest_blueprint["shots"])
    ease = score_ease({"duration_s": "9.75"}, hardest_blueprint)
    assert not ease.easy
    assert any(f"{n} shots" in r for r in ease.reasons)


def test_a_typical_reel_does_not_clear_the_default_gate_by_accident():
    """The intent the constants are calibrated to. A 6-7 shot, ~10s, fully static reel — the
    shape of essentially every Instagram winner — must sit BELOW the gate with room to see
    it, and a 1-2 shot short static clip must clear it comfortably. A first draft of this
    curve put the 6-shot case at 55.07 against a gate of 55: passing by seven hundredths of a
    point is the same failure as never passing at all."""
    typical = [score_ease({}, _bp(n, d)).score for n in (6, 7) for d in (9.4, 9.9)]
    assert max(typical) < EASE_THRESHOLD - 2
    assert score_ease({}, _bp(1, 7.0)).score > EASE_THRESHOLD + 20
    assert score_ease({}, _bp(2, 7.0)).score > EASE_THRESHOLD + 10


def test_every_extra_shot_costs_something():
    """The bug this rule had: shot bands stopped at 4, so 5 shots and 12 shots — and every
    real reel — scored identically on the strongest signal."""
    scores = [score_ease({}, _bp(n, 10.0)).score for n in range(1, 13)]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)
    assert scores[5] > scores[11]          # 6 shots still beats 12; no band flattens them


def test_every_extra_second_costs_something_up_to_the_long_cutoff():
    """Inside the ramp — that is the honest claim. The duration term is clamped at both ends,
    so a 35s and a 75s clip DO land on the same number; what separates them there is that
    neither can be easy at all (the >= EASE_LONG_S veto), and the ease backfill ranks both
    behind anything that can."""
    scores = [score_ease({}, _bp(4, d)).score for d in (6.0, 9.4, 9.9, 15.0, 22.0)]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)
    beyond = [score_ease({}, _bp(4, d)) for d in (35.0, 75.0)]
    assert beyond[0].score == beyond[1].score          # the ramp is flat out here...
    assert not any(e.eligible or e.easy for e in beyond)   # ...and nothing out here is easy


def test_every_non_static_shot_costs_something():
    """3-of-7 static and 7-of-7 static are different clips to remake, and the old
    all-or-nothing rule scored them the same."""
    scores = [score_ease({}, _bp(7, 9.8, static=s)).score for s in (7, 6, 4, 3, 0)]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)
    assert any("static camera 3/7" in r for r in score_ease({}, _bp(7, 9.8, static=3)).reasons)


def test_the_measured_duration_beats_the_blueprints_estimate():
    """`duration_s` is the platform's own `video_duration` — measured. `video_metadata.
    estimated_duration_seconds` is a vision model's guess, and on the real corpus one of six
    is 0.633s out (9.5 vs 10.133), worth 0.76 ease points where ~0.14 separates the pool.
    Scoring the guess put that clip 4th of 6 instead of last."""
    bp = _bp(7, 9.5)
    row = {"duration_s": "10.133000373840332"}
    assert clip_duration(row, bp) == (pytest.approx(10.133, abs=1e-3), "measured")
    assert score_ease(row, bp).score < score_ease({}, bp).score
    assert any(r.startswith("10.133s") for r in score_ease(row, bp).reasons)
    # ...and with no measured value the estimate still scores, flagged as an estimate.
    assert clip_duration({}, bp) == (9.5, "estimated")
    assert any(r.endswith("(est)") for r in score_ease({}, bp).reasons)


def test_the_score_and_the_printed_duration_are_the_same_number():
    """The CLI table prints `Target.duration_s` next to the score. They used to be computed by
    two different expressions, so a blueprint estimate of 0 scored `+30` as "0s" while the
    table beside it printed 12.4s off the corpus row."""
    bp = _bp(1, 0.0)                       # a truncated/failed analysis writes 0, not None
    row = {"duration_s": "12.4"}
    t = Target(row=row, blueprint=bp, ease=score_ease(row, bp))
    assert t.duration_s == 12.4
    assert any(r.startswith("12.4s") for r in t.ease.reasons)
    assert "**duration:** 12.4s" in build_recipe("instagram", row, bp, t.ease)


def test_a_zero_or_negative_duration_is_unknown_not_instant():
    """`duration_points(0)` pays full marks, and a model-written 0 is a failed measurement."""
    for bad in (0, -5, "0", float("nan"), float("inf")):
        got, src = clip_duration({"duration_s": bad}, None)
        assert got is None and src == "unknown", bad
    ease = score_ease({"duration_s": "0"}, _bp(1, 0.0, cam=""))
    assert not ease.easy and ease.score == 45          # the shots term, and nothing else
    assert "duration unknown — cannot be called easy" in ease.reasons


def test_a_long_or_unknown_duration_can_never_be_easy():
    """EASE_SHOTS_WEIGHT + EASE_STATIC_WEIGHT is 65, above any sane gate — so without a veto
    a 90s single-take talking head scored 65, was called easy, and OUTRANKED every genuine
    10s candidate at ~52. The render would then hold one frame for a minute and a half. A
    clip with no duration at all scored the same 65, i.e. missing data was rewarded."""
    long_one = score_ease({"duration_s": "90"}, _bp(1, 90.0))
    assert long_one.score >= EASE_THRESHOLD          # the other two terms still pay
    assert not long_one.easy and not long_one.eligible
    assert not long_one.at(10).easy                   # ...at ANY threshold, including a restore
    no_dur = score_ease({}, {"shots": [{"camera_movement": "Static"}]})
    assert not no_dur.easy and not no_dur.eligible
    assert score_ease({"duration_s": str(EASE_LONG_S - 0.01)}, _bp(1, 1)).easy


def test_a_graphic_card_shot_counts_as_static():
    """`camera_movement: "None"` is what the analyzer writes for a text/colour card — no
    camera at all. Those are the CHEAPEST shots in a blueprint, so they must not be scored
    as movement."""
    assert score_ease({}, _bp(4, 9.5, cam="None")).score == \
        score_ease({}, _bp(4, 9.5, cam="Static")).score


def test_learning_one_shot_is_static_never_lowers_the_score():
    """The static term was pro rata over the SHOT COUNT while switching on as soon as ONE
    shot carried camera data — so every dataless shot counted as moving. Filling in one of
    seven `camera_movement` fields cost the clip 9 points: the score fell as the information
    improved, and could push it under the gate."""
    blind = _bp(7, 9.8, cam="", style={"editing_style": "minimal editing"})
    one_known = _bp(7, 9.8, cam="", style={"editing_style": "minimal editing"})
    one_known["shots"][0]["camera_movement"] = "Static"
    assert score_ease({}, one_known).score >= score_ease({}, blind).score
    # ...and the reason discloses that the fraction is over KNOWN shots, not over all seven.
    assert any("1/1 known of 7" in r for r in score_ease({}, one_known).reasons)


@pytest.mark.parametrize("movement,static", [
    ("Static", True), ("None", True), ("Static (locked off)", True), ("Locked off", True),
    ("Drone shot", False),                       # contains "one shot"
    ("Slow zoom, minimal movement", False),      # contains "minimal"
    ("Handheld with still moments", False),      # contains "still"
    ("Slow dolly in", False), ("Whip pan", False),
])
def test_camera_movement_is_matched_as_a_token_not_as_a_substring(movement, static):
    """`camera_movement` is a short controlled vocabulary, and a substring scan read the
    hardest shot types in the language as static — a 4-shot all-aerial reel took the full
    +20 for holding a drone still."""
    ease = score_ease({}, _bp(4, 9.5, cam=movement))
    assert any(f"static camera {4 if static else 0}/4" in r for r in ease.reasons), \
        ease.reasons


def test_style_text_is_only_a_fallback_when_there_is_no_camera_data():
    no_cam = _bp(6, 9.5, cam="", style={"editing_style": "minimal editing, no cuts"})
    for s in no_cam["shots"]:
        s["camera_movement"] = ""
    ease = score_ease({}, no_cam)
    assert any(r.startswith("minimal editing") for r in ease.reasons)
    # ...and it must never overrule per-shot data, which can tell 3/7 from 7/7.
    with_cam = _bp(7, 9.5, static=3, style={"editing_style": "minimal editing"})
    assert not any(r.startswith("minimal editing") for r in score_ease({}, with_cam).reasons)


def test_reasons_carry_what_each_term_paid():
    """`51.03 — 7 shots +6.4, 9.5s +24.6, static camera 7/7 +20` answers "which term ate the
    points" without re-running the arithmetic by hand."""
    reasons = score_ease({}, _bp(7, 9.5)).reasons
    assert any(r.startswith("7 shots +") for r in reasons)
    assert any(r.startswith("9.5s +") for r in reasons)
    assert any(r.startswith("static camera 7/7 +") for r in reasons)


def test_ease_ranks_the_real_analyzed_clips_in_the_expected_order(
        simplest_blueprint, middle_blueprint, hardest_blueprint):
    """The rule must actually separate the corpus: fewer shots beats more shots."""
    one = score_ease({}, simplest_blueprint).score
    grid = score_ease({}, middle_blueprint).score
    hard = score_ease({}, hardest_blueprint).score
    assert one >= grid > hard


def _shape(bp):
    """What the rule can actually see: shots, duration, and the static fraction."""
    cams = [str(s.get("camera_movement") or "") for s in bp["shots"]]
    return (len(bp["shots"]),
            round(float((bp.get("video_metadata") or {}).get(
                "estimated_duration_seconds") or 0), 3),
            tuple(sorted(c.lower() for c in cams)))


def test_the_real_corpus_orders_rather_than_collapsing(blueprints):
    """The corpus this was calibrated against is homogeneous — six 6-7 shot, 9.4-9.9s,
    fully-static reels — so the win is ORDERING, not spread. What must never happen again is
    every clip landing on one number, which is how the gate came to reject all of them
    identically.

    Asserted as the property that is actually claimed — DISTINCT SHAPES GET DISTINCT SCORES —
    rather than "the conftest shape order equals the score order", which was true only by
    accident of this dataset. conftest sorts by (shots, duration) while duration spans 30
    points and a shot step is worth ~1, so one 25s clip in the corpus inverts that ordering
    on entirely correct behaviour."""
    if len(blueprints) < 3:
        pytest.skip("fewer than 3 blueprints on disk")
    scored = [(_shape(bp), score_ease({}, bp).score) for bp in blueprints]
    by_shape = {}
    for shape, score in scored:
        by_shape.setdefault(shape, set()).add(score)
    assert all(len(v) == 1 for v in by_shape.values())     # same shape -> same number
    assert len({next(iter(v)) for v in by_shape.values()}) == len(by_shape)  # ...and distinct

    # ...and within one shot count, every extra second still costs. That IS an ordering
    # claim, and it holds whatever else the operator scrapes.
    for n in {s[0] for s, _ in scored}:
        same = sorted(((s[1], v) for s, v in scored if s[0] == n and s[2].count("static")
                       + s[2].count("none") == n))
        assert [v for _, v in same] == sorted((v for _, v in same), reverse=True)


def test_ease_reads_string_durations_off_a_real_corpus_row(corpus_rows):
    """Every corpus field arrives from the hub as a string; the heuristic must not care."""
    row = corpus_rows[0]
    assert isinstance(row["duration_s"], str)
    ease = score_ease(row, None)
    assert ease.score > 0
    assert any(r.startswith(f"{float(row['duration_s']):g}s") for r in ease.reasons)


def test_missing_blueprint_is_duration_only_and_says_so(corpus_rows):
    ease = score_ease(corpus_rows[0], None)
    assert "no blueprint (duration-only)" in ease.reasons
    assert ease.score <= 30          # duration is the only signal left, so the ceiling is 30


def test_ease_survives_a_completely_empty_row():
    ease = score_ease({}, None)
    assert ease.score == 0 and not ease.easy


def test_an_analyzed_clip_with_no_shot_data_says_so():
    """Silence read as "shots were not needed"; it means the strongest term paid nothing."""
    ease = score_ease({"duration_s": "9.5"}, {"content_id": "x", "shots": []})
    assert "shots unknown +0" in ease.reasons


def test_a_malformed_shots_field_skips_one_candidate_rather_than_killing_the_run():
    """`shots` is model-written. A dict there used to raise AttributeError out of score_ease,
    which takes down the whole propose run instead of one candidate."""
    ease = score_ease({"duration_s": "9.5"}, {"content_id": "x", "shots": {"a": 1}})
    assert ease.score > 0 and "shots unknown +0" in ease.reasons


# ---- the shortcode -> content_id join ---------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/reel/Ab1_cdEFghI/", "Ab1_cdEFghI"),
    ("https://www.instagram.com/reels/Ab1_cdEFghI/", "Ab1_cdEFghI"),
    ("https://www.instagram.com/p/C7_QwerSgHh/", "C7_QwerSgHh"),
    ("https://www.instagram.com/tv/ABC123/?igsh=x", "ABC123"),
    ("https://www.instagram.com/somecreator/", None),
    ("", None),
    (None, None),
])
def test_shortcode_extraction(url, expected):
    assert shortcode(url) == expected


class FakeHub:
    """Minimal stand-in for HubClient — select_targets touches exactly these four methods."""

    def __init__(self, top=None, analysis=None, blueprints=None, search=None, content=None):
        self.top, self.analysis = top or [], analysis or []
        self.blueprints, self._search = blueprints or {}, search or []
        self._content = content or []
        self.blueprint_calls = []

    def content(self, platform):
        return self._content

    def corpus_top(self, platform, n=15):
        return self.top[:n]

    def corpus_search(self, platform, q, k=15):
        return self._search[:k]

    def analysis_list(self, platform):
        return self.analysis

    def blueprint(self, platform, content_id):
        self.blueprint_calls.append(content_id)
        return self.blueprints.get(content_id)


def test_content_id_index_joins_url_to_content_id():
    hub = FakeHub(analysis=[
        {"content_id": "111_a", "url": "https://www.instagram.com/reel/AAA/"},
        {"content_id": "222_b", "url": "https://www.instagram.com/reel/BBB/"},
        {"content_id": "333_c", "url": None},          # unjoinable — must be dropped
    ])
    assert content_id_index(hub, "instagram") == {"AAA": "111_a", "BBB": "222_b"}


def test_content_id_index_is_empty_when_the_listing_fails():
    class Broken(FakeHub):
        def analysis_list(self, platform):
            raise RuntimeError("hub down")
    assert content_id_index(Broken(), "instagram") == {}


def test_select_targets_attaches_blueprints_via_the_shortcode_join(single_shot_blueprint):
    """The corpus row has NO content_id — only the join makes the blueprint reachable."""
    bp = single_shot_blueprint
    cid = bp["content_id"]
    hub = FakeHub(
        top=[{"url": "https://www.instagram.com/reel/Ab1_cdEFghI/", "virality_score": "98.7"}],
        analysis=[{"content_id": cid,
                   "url": "https://www.instagram.com/reel/Ab1_cdEFghI/"}],
        blueprints={cid: bp},
    )
    [target] = select_targets(hub, "instagram", count=1, pool=5)
    assert target.content_id == cid
    assert target.blueprint is bp
    assert target.n_shots == 1


def test_prefer_blueprint_false_skips_the_blueprint_fetch_entirely():
    hub = FakeHub(top=[{"url": "https://www.instagram.com/reel/AAA/", "virality_score": "9"}],
                  analysis=[{"content_id": "111_a",
                             "url": "https://www.instagram.com/reel/AAA/"}])
    [t] = select_targets(hub, "instagram", count=1, pool=5, prefer_blueprint=False)
    assert hub.blueprint_calls == []
    assert t.blueprint is None
    assert "no blueprint (duration-only)" in t.ease.reasons


def test_missing_blueprint_degrades_gracefully_rather_than_raising():
    """An un-analyzed top clip is an expected empty state, not an error."""
    hub = FakeHub(top=[{"url": "https://www.instagram.com/reel/ZZZ/",
                        "virality_score": "88", "duration_s": "6.0",
                        "caption": "no blueprint here"}])
    [t] = select_targets(hub, "instagram", count=1, pool=5)
    assert t.blueprint is None
    md = build_recipe("instagram", t.row, t.blueprint, t.ease)
    assert "no blueprint yet" in md
    # ...and the renderer must REFUSE it rather than render placeholder prompts.
    with pytest.raises(RecipeError):
        parse_recipe(md, recipe_filename(t), "instagram")


def test_empty_corpus_raises_proposeerror():
    with pytest.raises(ProposeError, match="EMPTY"):
        select_targets(FakeHub(top=[]), "instagram", count=3)


def test_topic_routes_to_search_not_top():
    hub = FakeHub(top=[{"url": "https://www.instagram.com/reel/TOP/", "virality_score": "99"}],
                  search=[{"url": "https://www.instagram.com/reel/HIT/",
                           "virality_score": "41"}])
    [t] = select_targets(hub, "instagram", count=1, pool=5, topic="fashion")
    assert t.row["url"].endswith("/HIT/")


# ---- ordering --------------------------------------------------------------------------
def _t(vscore, ease_score, threshold=EASE_THRESHOLD, blueprint=...):
    """A scored candidate. `blueprint` defaults to a placeholder rather than None because a
    pool of blueprint-less candidates is a DIFFERENT diagnosis — ease is duration-only there
    and the run must not advise lowering the gate to a duration-only score."""
    if blueprint is ...:
        blueprint = {"content_id": "bp", "shots": [{"shot_index": 1}]}
    return Target(row={}, blueprint=blueprint,
                  ease=Ease(score=ease_score, easy=ease_score >= threshold,
                            threshold=threshold),
                  virality_score=vscore)


def test_ranking_is_easy_first_then_virality():
    hot_but_hard = _t(99.0, 10)
    easy_low = _t(50.0, 90)
    easy_high = _t(80.0, 90)
    picks = rank_targets([hot_but_hard, easy_low, easy_high], 3)
    assert picks[0] is easy_high          # same ease -> higher virality wins
    assert picks[1] is easy_low
    assert picks[2] is hot_but_hard       # backfill, ranked by virality


def test_backfill_never_returns_a_short_list():
    """If nothing clears the ease gate the run still yields `count` picks, honestly scored."""
    picks = rank_targets([_t(90.0, 10), _t(80.0, 20), _t(70.0, 5)], 2)
    assert [p.virality_score for p in picks] == [90.0, 80.0]
    assert all(not p.ease.easy for p in picks)


def test_backfill_order_ease_ranks_the_remainder_by_ease():
    """D7: same starved pool, opposite question. `virality` hands back the proven winners,
    `ease` hands back the least-bad to remake — and the operator chose which they asked for."""
    hot_hard, mid, cold_easiest = _t(90.0, 10), _t(80.0, 20), _t(70.0, 45)
    pool = [hot_hard, mid, cold_easiest]
    assert rank_targets(pool, 2) == [hot_hard, mid]
    assert rank_targets(pool, 2, "ease") == [cold_easiest, mid]
    assert rank_targets(pool, 2, "nonsense") == [hot_hard, mid]   # unknown -> today's order


def test_backfill_order_ease_puts_the_unremakeable_last_whatever_it_scored():
    """A 90s single-shot static clip scores 65 on shots + static alone but can never be easy.
    Leading a "least-bad to remake" list with it is the same lie the gate exists to refuse."""
    long_one = Target(row={}, blueprint=None,
                      ease=score_ease({"duration_s": "90"}, _bp(1, 90.0)))
    ordinary = _t(10.0, 30.0)
    assert long_one.ease.score > ordinary.ease.score
    assert rank_targets([long_one, ordinary], 2, "ease") == [ordinary, long_one]


def test_backfill_order_never_displaces_a_candidate_that_cleared_the_gate():
    """It reorders the REMAINDER only — an easy pick outranks any backfill either way."""
    easy, hot = _t(1.0, 90), _t(99.0, 30)
    assert rank_targets([hot, easy], 2, "ease")[0] is easy
    assert rank_targets([hot, easy], 2, "virality")[0] is easy


def test_ranking_caps_at_the_available_candidates():
    assert len(rank_targets([_t(90.0, 90)], 5)) == 1


# ---- the threshold lifecycle (D2-D6) ----------------------------------------------------
def test_automation_can_only_ever_raise_the_threshold():
    """D3, the load-bearing rule. Enforced by construction — `max()` — so no caller can
    lower the gate through this function whatever it passes in. A wrong restore means fewer
    easy picks (visible, recoverable); a wrong auto-lowering silently degrades every
    proposal, so it must be unrepresentable rather than merely unwritten."""
    for current in (0, 25, 40, 55, 70, 100):
        for target in (None, 0, 10, 39, 40, 41, 55, 100):
            got = automation_threshold(current, target)
            assert got >= current, (current, target, got)
    assert automation_threshold(40, 55) == 55        # restore raises
    assert automation_threshold(55, 40) == 55        # a LOWER target changes nothing
    assert automation_threshold(40, None) == 40


def test_lowering_records_where_the_threshold_came_from():
    assert restore_origin(40, None) == 55            # below the default -> remember 55
    assert restore_origin(40, 70) == 70              # a recorded origin wins over the default
    assert restore_origin(70, None) is None          # above the default -> nothing to record


def test_a_recorded_origin_is_never_recomputed_or_cleared():
    """`ease_restore_to` is human-writable, and recomputing it from a high-water mark rewrote
    the operator's number: `ease_threshold: 40, ease_restore_to: 45` became 55 on the next
    run, and a recorded 70 was ERASED by briefly raising the threshold to 70 — so a later
    restore parked the gate 15 points below where that human had it. A record at or below the
    threshold is already inert (`restorable` needs restore_to > threshold); leaving it alone
    costs nothing and losing it costs the operator's intent."""
    assert restore_origin(40, 45) == 45              # a deliberate sub-default target survives
    assert restore_origin(70, 70) == 70              # ...and is not erased by matching it
    assert restore_origin(60, 55) == 55              # raised past the record -> inert, kept
    assert diagnose_ease(_pool([90] * 15), count=5, threshold=60,
                         restore_to=55, auto_restore=True).kind == "ok"


def _pool(scores, count_easy_at=EASE_THRESHOLD):
    return [_t(float(i), s, count_easy_at) for i, s in enumerate(scores)]


def test_starved_gate_reports_the_numbers_and_what_to_do():
    diag = diagnose_ease(_pool([40] * 15), count=5, threshold=55)
    assert diag.kind == "starved" and diag.level == "warning"
    assert diag.msg.startswith("0 of 15 candidates cleared ease >= 55 — best score was 40.")
    assert "Lower ease_threshold to 40" in diag.msg
    # ...and the same numbers structurally, so the Dashboard never parses the sentence.
    assert diag.data["kind"] == "starved"
    assert diag.data["considered"] == 15 and diag.data["cleared"] == 0
    assert diag.data["best_score"] == 40 and diag.data["suggest_threshold"] == 40
    assert diag.event == "ease.starved"


def test_a_starved_gate_never_lowers_anything_by_itself():
    """D3/D5 again, at the diagnosis level: `starved` carries advice, never an action."""
    diag = diagnose_ease(_pool([40] * 15), count=5, threshold=55, auto_restore=True)
    assert diag.kind == "starved"
    assert diag.applied_threshold is None
    assert diag.suggest_threshold < diag.threshold      # the advice IS to lower it — by hand


def test_a_pool_with_no_blueprints_is_never_told_to_lower_the_gate():
    """Before the analysis-engine stage runs, ease is duration-only and capped at 30 — and a
    search row with no duration scores 0. The advice was then literally "lower ease_threshold
    to 0", which destroys the gate on a pool that is missing its two strongest signals rather
    than scoring badly on them."""
    diag = diagnose_ease([_t(float(i), 0.0, blueprint=None) for i in range(15)],
                         count=5, threshold=55)
    assert diag.kind == "starved" and diag.suggest_threshold is None
    assert "none of them has a blueprint yet" in diag.msg and "analysis-engine" in diag.msg
    assert "Lower ease_threshold" not in diag.msg


def test_a_half_analyzed_pool_advises_off_the_analyzed_clips_and_says_so():
    pool = _pool([52, 51]) + [_t(9.0, 30.0, blueprint=None) for _ in range(3)]
    diag = diagnose_ease(pool, count=2, threshold=55)
    assert diag.kind == "starved" and diag.suggest_threshold == 52   # not the 30 ceiling
    assert "3 of 5 have no blueprint yet" in diag.msg


def test_a_candidate_that_cannot_be_easy_is_not_counted_as_clearing_the_gate():
    """A 90s single-shot static clip scores 65 but can never be easy (see the duration veto).
    Counting it as "cleared" would report a working gate over picks the ranking rejects."""
    long_one = Target(row={"duration_s": "90"}, blueprint=None,
                      ease=score_ease({"duration_s": "90"}, _bp(1, 90.0)))
    diag = diagnose_ease([long_one] + _pool([40] * 5), count=2, threshold=55)
    assert long_one.ease.score > 55 and diag.cleared == 0 and diag.kind == "starved"


def test_a_working_gate_says_nothing_actionable():
    diag = diagnose_ease(_pool([70, 65, 60, 20, 10]), count=2, threshold=55)
    assert diag.kind == "ok" and not diag.actionable and diag.cleared == 3


def test_restore_needs_strictly_more_than_count_candidates():
    """D4: 'enough data' is a margin over `count`, not a blueprint count and not a tie. At
    exactly `count` the gate would flap — one re-analysed clip and it starves again."""
    at_count = diagnose_ease(_pool([60] * 3 + [40] * 12), count=3, threshold=40,
                             restore_to=55)
    assert at_count.kind == "ok"                      # 3 clear 55, count is 3 — not enough
    over = diagnose_ease(_pool([60] * 4 + [40] * 11), count=3, threshold=40, restore_to=55)
    assert over.kind == "restore_ready"


def test_restore_ready_prompts_and_changes_nothing_by_default():
    """D5: prompt-first. `ease_auto_restore` is off, so the run REPORTS and does not act."""
    diag = diagnose_ease(_pool([60] * 12 + [10] * 3), count=5, threshold=40, restore_to=55)
    assert diag.kind == "restore_ready" and diag.applied_threshold is None
    assert diag.msg.startswith("12 of 15 candidates now clear ease >= 55")
    assert "restore ease_threshold to 55" in diag.msg
    assert diag.data["restore_cleared"] == 12 and diag.data["ease_restore_to"] == 55


def test_auto_restore_raises_and_says_so_loudly():
    diag = diagnose_ease(_pool([60] * 12 + [10] * 3), count=5, threshold=40, restore_to=55,
                         auto_restore=True)
    assert diag.kind == "restored" and diag.applied_threshold == 55
    assert "RESTORED ease_threshold 40 -> 55" in diag.msg
    assert diag.event == "ease.restored"


def test_auto_restore_cannot_lower_even_when_the_record_is_lower():
    """A stale or hand-edited `ease_restore_to` BELOW the live threshold must be inert."""
    diag = diagnose_ease(_pool([90] * 15), count=5, threshold=70, restore_to=40,
                         auto_restore=True)
    assert diag.kind == "ok" and diag.applied_threshold is None


def test_re_gating_moves_the_verdict_and_never_the_score():
    """What an in-run restore does to already-scored candidates."""
    ease = score_ease({}, _bp(3, 8.0))
    raised = ease.at(95)
    assert raised.score == ease.score and raised.threshold == 95 and not raised.easy
    assert ease.easy


def test_the_scoring_threshold_is_a_parameter_not_a_constant():
    hard = _bp(7, 9.8)
    assert not score_ease({}, hard).easy
    assert score_ease({}, hard, threshold=40).easy


# ---- the round trip: build_recipe -> parse_recipe ----------------------------------------
def _round_trip(bp, row):
    cid = bp["content_id"]
    target = Target(row=row, blueprint=bp, ease=score_ease(row, bp), content_id=cid,
                    virality_score=float(row.get("virality_score") or 0))
    name = recipe_filename(target)
    return bp, name, parse_recipe(build_recipe("instagram", row, bp, target.ease), name,
                                  "instagram")


@pytest.fixture
def multi_shot_row(hardest_blueprint):
    """A synthetic corpus row for the hardest blueprint. Handle, caption and sound are
    invented; only `duration_s` is derived from the blueprint, because the round trip
    asserts the parsed duration matches the blueprint's own estimate."""
    return {"url": "https://www.instagram.com/reel/Ab1_cdEFghI/", "creator": "example_creator",
            "virality_score": "99.4", "tier": "Viral",
            "duration_s": "9.751999855041504", "audio_title": "Example Sound",
            "audio_artist": "Example Artist", "caption": "Comment below #example"}


def test_recipe_round_trips_through_the_parser(multi_shot_row, hardest_blueprint):
    bp, name, plan = _round_trip(hardest_blueprint, multi_shot_row)
    assert len(plan.shots) == len(bp["shots"])
    assert [s.index for s in plan.shots] == [s["shot_index"] for s in bp["shots"]]
    assert all(s.prompt for s in plan.shots)


def test_round_trip_preserves_every_shot_field(multi_shot_row, hardest_blueprint):
    bp, _, plan = _round_trip(hardest_blueprint, multi_shot_row)
    for src, got in zip(bp["shots"], plan.shots):
        assert got.prompt == src["generation_prompt"].strip()
        assert got.duration_s == pytest.approx(src["duration"])
        if src.get("on_screen_text"):
            assert got.on_screen_text == src["on_screen_text"].strip()
        if src.get("negative_prompt"):
            assert got.negative == src["negative_prompt"].strip()


def test_round_trip_preserves_the_regeneration_guide(multi_shot_row, hardest_blueprint):
    bp, _, plan = _round_trip(hardest_blueprint, multi_shot_row)
    guide = bp["regeneration_guide"]
    assert plan.master_style_prompt == guide["master_style_prompt"].strip()
    assert plan.global_negative_prompt == guide["global_negative_prompt"].strip()


def test_round_trip_preserves_the_duration_and_source_url(multi_shot_row, hardest_blueprint):
    """The recipe carries the MEASURED duration off the corpus row (the platform's own
    `video_duration`), rounded to 2dp by engine/propose.py::_fmt_duration — not the
    blueprint's `estimated_duration_seconds`, which is a vision model's guess at the same
    number and is 0.6s out for one clip in the real corpus. This is the render's total
    length, and the operator attaches the original sound by hand, so drift is cuts landing
    off the beat. Only a row with no duration at all falls back to the estimate."""
    bp, _, plan = _round_trip(hardest_blueprint, multi_shot_row)
    assert plan.target_duration_s == pytest.approx(
        round(float(multi_shot_row["duration_s"]), 2))
    assert plan.source_url == multi_shot_row["url"]

    est = (bp.get("video_metadata") or {}).get("estimated_duration_seconds")
    if est:
        no_dur = {k: v for k, v in multi_shot_row.items() if k != "duration_s"}
        _, _, fallback = _round_trip(bp, no_dur)
        assert fallback.target_duration_s == pytest.approx(round(float(est), 2))


def test_round_trip_carries_the_audio_block(multi_shot_row, hardest_blueprint):
    """The sound comes off the BLUEPRINT's audio block, not the corpus row — the row's
    audio_* fields are a fallback for clips that were never analyzed."""
    bp, _, plan = _round_trip(hardest_blueprint, multi_shot_row)
    assert plan.audio_block and plan.audio_block.startswith("## Audio")
    title = (bp.get("audio") or {}).get("audio_title") or multi_shot_row["audio_title"]
    assert title in plan.audio_block


def test_single_shot_recipe_round_trips_too(single_shot_blueprint):
    row = {"url": "https://www.instagram.com/reel/Jk2_lmNOpqR/", "virality_score": "98.7",
           "tier": "Viral", "duration_s": "6.22"}
    _, _, plan = _round_trip(single_shot_blueprint, row)
    assert len(plan.shots) == 1


def test_filename_carries_a_content_id_fragment_the_parser_can_recover(multi_shot_row,
                                                                      hardest_blueprint):
    bp, name, plan = _round_trip(hardest_blueprint, multi_shot_row)
    assert name.startswith("2026-") or name[4] == "-"
    assert "-similar-" in name and name.endswith(".md")
    assert plan.content_id_prefix
    assert bp["content_id"].startswith(plan.content_id_prefix)


def test_duration_is_rounded_not_dumped_as_a_raw_float():
    """`9.751999855041504s` would parse fine but reads as noise and lands in ffmpeg holds."""
    ease = Ease(score=0, easy=False, reasons=[])
    md = build_recipe("instagram", {"duration_s": "9.751999855041504"}, None, ease)
    assert "**duration:** 9.75s" in md


def test_audio_block_falls_back_when_no_audio_metadata_exists():
    md = build_recipe("instagram", {}, None, Ease(score=0, easy=False, reasons=[]))
    assert "_No audio metadata captured for this clip" in md


# ---- --content-id: reach a specific exemplar, bypassing ranking -------------------------
def _fake_hub_with_content():
    return FakeHub(
        top=[{"content_id": "AAA_1", "virality_score": "99"}],
        content=[
            {"content_id": "AAA_1", "virality_score": "99", "duration_s": "6"},
            {"content_id": "MID_2", "virality_score": "52", "duration_s": "24"},
            {"content_id": "LOW_3", "virality_score": "11", "duration_s": "40"},
        ],
    )


def test_content_id_reaches_a_clip_ranking_would_never_surface():
    """A freshly-scraped creator lands mid-corpus; no pool width brings it into the top N."""
    hub = _fake_hub_with_content()
    [t] = select_targets(hub, "instagram", content_ids=["MID_2"])
    assert t.content_id == "MID_2"
    assert t.virality_score == 52          # far below the top row, still selected


def test_content_id_preserves_the_callers_order_and_ignores_count():
    hub = _fake_hub_with_content()
    got = select_targets(hub, "instagram", count=1, content_ids=["LOW_3", "AAA_1"])
    assert [t.content_id for t in got] == ["LOW_3", "AAA_1"]


def test_unknown_content_id_fails_loudly():
    hub = _fake_hub_with_content()
    with pytest.raises(ProposeError, match="NOPE"):
        select_targets(hub, "instagram", content_ids=["NOPE"])


def test_content_id_bypasses_the_top_endpoint_entirely():
    """It must read the full corpus, not the ranked slice — that is the whole point."""
    hub = _fake_hub_with_content()
    hub.top = []                            # ranking would find nothing at all
    [t] = select_targets(hub, "instagram", content_ids=["MID_2"])
    assert t.content_id == "MID_2"
