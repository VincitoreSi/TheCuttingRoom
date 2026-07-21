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
    EASE_THRESHOLD, ProposeError, build_recipe, content_id_index, rank_targets,
    recipe_filename, score_ease, select_targets, shortcode, Ease, Target,
)
from engine.recipe import RecipeError, parse_recipe

# The blueprints these tests run against are selected by SHAPE (fewest shots / most shots)
# by the fixtures in conftest.py, never by a hard-coded content_id. An Instagram content_id is
# `<media_pk>_<user_pk>` and its trailing half permanently identifies a real account, so those
# ids must not be committed. Selecting on shape also means these tests exercise whatever
# dataset the operator actually has, instead of skipping unless it is one specific corpus.
# Synthetic shortcodes below (AAA/BBB/ZZZ/…) are placeholders and join to nothing real.


# ---- ease scoring ----------------------------------------------------------------------
def test_single_short_static_shot_is_easy(single_shot_blueprint):
    ease = score_ease({"duration_s": "6.22"}, single_shot_blueprint)
    assert ease.easy
    assert ease.score >= EASE_THRESHOLD
    assert "single-shot" in ease.reasons


def test_multi_shot_clip_is_not_easy(hardest_blueprint):
    n = len(hardest_blueprint["shots"])
    ease = score_ease({"duration_s": "9.75"}, hardest_blueprint)
    assert not ease.easy
    assert any(f"{n} shots" in r for r in ease.reasons)


def test_ease_ranks_the_real_analyzed_clips_in_the_expected_order(
        simplest_blueprint, middle_blueprint, hardest_blueprint):
    """The rule must actually separate the corpus: fewer shots beats more shots."""
    one = score_ease({}, simplest_blueprint).score
    grid = score_ease({}, middle_blueprint).score
    hard = score_ease({}, hardest_blueprint).score
    assert one >= grid > hard


def test_ease_reads_string_durations_off_a_real_corpus_row(corpus_rows):
    """Every corpus field arrives from the hub as a string; the heuristic must not care."""
    row = corpus_rows[0]
    assert isinstance(row["duration_s"], str)
    ease = score_ease(row, None)
    assert ease.score > 0
    assert any(r.endswith("s") for r in ease.reasons)


def test_missing_blueprint_is_duration_only_and_says_so(corpus_rows):
    ease = score_ease(corpus_rows[0], None)
    assert "no blueprint (duration-only)" in ease.reasons
    assert ease.score <= 30          # duration is the only signal left, so the ceiling is 30


def test_ease_survives_a_completely_empty_row():
    ease = score_ease({}, None)
    assert ease.score == 0 and not ease.easy


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
def _t(vscore, ease_score):
    return Target(row={}, blueprint=None,
                  ease=Ease(score=ease_score, easy=ease_score >= EASE_THRESHOLD),
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


def test_ranking_caps_at_the_available_candidates():
    assert len(rank_targets([_t(90.0, 90)], 5)) == 1


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
    bp, _, plan = _round_trip(hardest_blueprint, multi_shot_row)
    # build_recipe prefers the blueprint's own estimate over the corpus row's raw float,
    # and rounds it to 2dp (engine/propose.py::_fmt_duration).
    est = (bp.get("video_metadata") or {}).get("estimated_duration_seconds")
    expected = round(float(est if est is not None else multi_shot_row["duration_s"]), 2)
    assert plan.target_duration_s == pytest.approx(expected)
    assert plan.source_url == multi_shot_row["url"]


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
