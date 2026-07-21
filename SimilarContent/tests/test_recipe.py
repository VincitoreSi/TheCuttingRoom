"""Recipe parsing — the step that decides what actually gets rendered.

Grounded in the real approved recipes in ReelScraper/studio/instagram/ rather than invented
markdown, so a change to the producer's output format fails here rather than silently
rendering something wrong at $0.04 a frame.

Those recipes are selected by SHAPE (shot count), never by filename, and every assertion is
derived from the parsed recipe rather than pinned to a literal. A studio filename embeds a
content_id fragment, and a recipe's on-screen text is a real creator's caption verbatim —
neither belongs in a committed test. This also means the suite exercises whatever corpus the
operator actually scraped instead of skipping unless it is one specific dataset.
"""
import re

import pytest

from engine.recipe import (
    RecipeError, allocate_durations, cap_frames, compose_frame_prompt, compose_negative,
    extract_section, parse_recipe, slug_from_filename,
)


def _pick(studio_files, predicate, why):
    for name, text in studio_files:
        try:
            plan = parse_recipe(text, name, "instagram")
        except RecipeError:
            continue                       # an un-renderable recipe is not a parser fixture
        if predicate(plan):
            return name, text, plan
    pytest.skip(f"no studio recipe {why}")


@pytest.fixture
def multi_shot(studio_files):
    """The most involved recipe available — needs >= 4 shots so the frame-cap test has an
    interior to drop."""
    _, _, plan = _pick(reversed(list(studio_files)), lambda p: len(p.shots) >= 4,
                       "with >= 4 shots")
    return plan


@pytest.fixture
def multi_shot_source(studio_files):
    """(filename, text) for the same recipe `multi_shot` resolves to."""
    name, text, _ = _pick(reversed(list(studio_files)), lambda p: len(p.shots) >= 4,
                          "with >= 4 shots")
    return name, text


@pytest.fixture
def one_shot(studio_files):
    _, _, plan = _pick(studio_files, lambda p: len(p.shots) == 1, "with exactly 1 shot")
    return plan


# ---- structure -------------------------------------------------------------------------
def test_parses_every_shot(multi_shot):
    assert len(multi_shot.shots) >= 4
    assert [s.index for s in multi_shot.shots] == list(range(1, len(multi_shot.shots) + 1))
    assert all(s.prompt for s in multi_shot.shots)


def test_shot_durations_and_target(multi_shot):
    """Every shot carries a positive hold, and they account for the whole clip."""
    durations = [s.duration_s for s in multi_shot.shots]
    assert all(d and d > 0 for d in durations)
    assert multi_shot.target_duration_s > 0
    assert sum(durations) == pytest.approx(multi_shot.target_duration_s, abs=0.01)


def test_multiline_on_screen_text_is_kept_whole(studio_files):
    """Truncating this would burn the wrong words into the frame."""
    _, _, plan = _pick(studio_files,
                       lambda p: any("\n" in (s.on_screen_text or "") for s in p.shots),
                       "with multi-line on-screen text")
    shot = next(s for s in plan.shots if "\n" in (s.on_screen_text or ""))
    # The text survives as several non-empty lines, not just its first one.
    lines = shot.on_screen_text.split("\n")
    assert len(lines) > 1
    assert all(line.strip() for line in lines)


def test_regeneration_guide_extracted(multi_shot):
    assert multi_shot.master_style_prompt.strip()
    assert multi_shot.global_negative_prompt.strip()
    assert multi_shot.consistency_notes.strip()


def test_source_and_identity(multi_shot, multi_shot_source):
    name, _ = multi_shot_source
    assert re.fullmatch(r"https://www\.instagram\.com/reels?/[\w-]+/", multi_shot.source_url)
    assert multi_shot.content_id_prefix.isdigit()
    assert multi_shot.slug == slug_from_filename(name)
    assert multi_shot.title.strip()


def test_audio_block_passed_through_verbatim(multi_shot):
    assert multi_shot.audio_block.startswith("## Audio")
    assert "**Sound:**" in multi_shot.audio_block


def test_single_shot_recipe(one_shot):
    assert len(one_shot.shots) == 1
    assert one_shot.target_duration_s > 0
    assert one_shot.shots[0].duration_s == pytest.approx(one_shot.target_duration_s)


def test_slug_from_filename():
    assert slug_from_filename("2026-01-02-similar-a-slug-goes-here-123456789012.md") == \
        "a-slug-goes-here"
    assert slug_from_filename("plain.md") == "plain"


def test_extract_section_stops_at_next_heading(multi_shot_source):
    _, text = multi_shot_source
    look = extract_section(text, "Look & feel")
    assert look.strip()
    assert "Regeneration guide" not in look


# ---- refusal ---------------------------------------------------------------------------
def test_no_shots_raises():
    with pytest.raises(RecipeError, match="no shots"):
        parse_recipe("# Clone recipe — x\n\n## Audio\n", "x.md", "instagram")


def test_placeholder_prompt_raises():
    md = ("# Clone recipe — x\n\n## Shot list\n### Shot 1 — 2s · Static\n"
          "- **Prompt:** _This clip has no blueprint yet_\n")
    with pytest.raises(RecipeError, match="placeholder"):
        parse_recipe(md, "x.md", "instagram")


def test_missing_prompt_raises():
    md = "# Clone recipe — x\n\n## Shot list\n### Shot 1 — 2s · Static\n- just a description\n"
    with pytest.raises(RecipeError):
        parse_recipe(md, "x.md", "instagram")


# ---- prompt composition ----------------------------------------------------------------
def _with_text(plan):
    return next((s for s in plan.shots if s.on_screen_text), None)


def _without_text(plan):
    return next((s for s in plan.shots if not s.on_screen_text), None)


def test_frame_prompt_layers_everything(multi_shot):
    shot = _with_text(multi_shot) or multi_shot.shots[0]
    p = compose_frame_prompt(multi_shot, shot)
    assert p.startswith(multi_shot.master_style_prompt.strip())
    assert shot.prompt.strip() in p
    assert "CONSISTENCY:" in p and "AVOID:" in p
    if shot.on_screen_text:
        assert shot.on_screen_text.split("\n")[0] in p
    assert "9:16" in p


def test_textless_shot_is_told_to_suppress_the_anchor_overlay(multi_shot):
    """Frames after the first are generated with frame 0 attached as an anchor, and the
    model copies the anchor's text overlay unless explicitly told not to — which put the
    hook caption on shots the recipe wanted clean."""
    shot = _without_text(multi_shot)
    if shot is None:
        pytest.skip("every shot in this recipe carries on-screen text")
    p = compose_frame_prompt(multi_shot, shot)
    assert "Burn this EXACT" not in p
    assert "NO on-screen text" in p
    assert "omit" in p.lower()


def test_shot_with_text_is_not_told_to_suppress_it(multi_shot):
    shot = _with_text(multi_shot)
    if shot is None:
        pytest.skip("no shot in this recipe carries on-screen text")
    assert "NO on-screen text" not in compose_frame_prompt(multi_shot, shot)


def test_negatives_merge_and_dedupe(multi_shot):
    """A term present in BOTH the global and per-shot negative must appear once."""
    shot = next((s for s in multi_shot.shots if s.negative), None)
    if shot is None:
        pytest.skip("no shot in this recipe carries a per-shot negative")
    neg = compose_negative(multi_shot, shot).lower()
    terms = [t.strip() for t in neg.split(",") if t.strip()]
    assert len(terms) == len(set(terms))
    shared = {t.strip().lower() for t in multi_shot.global_negative_prompt.split(",")} & \
             {t.strip().lower() for t in shot.negative.split(",")}
    for term in shared:
        assert neg.count(term) == 1


# ---- duration allocation ---------------------------------------------------------------
def test_real_recipe_durations_pass_through_unchanged(multi_shot):
    """They already sum to the target, so scaling must be a no-op."""
    declared = [s.duration_s for s in multi_shot.shots]
    out = allocate_durations(multi_shot.shots, multi_shot.target_duration_s)
    assert out == pytest.approx(declared)
    assert sum(out) == pytest.approx(multi_shot.target_duration_s, abs=0.01)


@pytest.mark.parametrize("durations,target", [
    ([2.0, 2.0, 2.0], 9.0),          # all known, needs scaling up
    ([4.0, 4.0, 4.0], 6.0),          # all known, needs scaling down
    ([None, None, None], 7.5),       # none known
    ([3.0, None, None], 9.0),        # mixed
    ([None, 2.0, None], 10.0),       # mixed, interior known
])
def test_allocation_always_sums_to_target(durations, target):
    from engine.recipe import Shot
    shots = [Shot(index=i, prompt="p", duration_s=d) for i, d in enumerate(durations)]
    out = allocate_durations(shots, target)
    assert len(out) == len(shots)
    assert sum(out) == pytest.approx(target, abs=1e-6)
    assert all(d > 0 for d in out)


def test_impossible_target_raises():
    from engine.recipe import Shot
    shots = [Shot(index=i, prompt="p") for i in range(5)]
    with pytest.raises(RecipeError, match="drop frames"):
        allocate_durations(shots, 1.0, min_hold=0.6)


def test_no_target_honours_declared_durations():
    from engine.recipe import Shot
    shots = [Shot(index=0, prompt="p", duration_s=3.0), Shot(index=1, prompt="p")]
    assert allocate_durations(shots, None) == [3.0, 0.6]


# ---- frame cap -------------------------------------------------------------------------
def test_cap_drops_shortest_interior_keeping_hook_and_payoff(multi_shot):
    shots = multi_shot.shots
    kept = cap_frames(shots, len(shots) - 1)
    assert len(kept) == len(shots) - 1
    assert kept[0] is shots[0]                    # the hook survives
    assert kept[-1] is shots[-1]                  # the payoff survives


def test_cap_is_a_noop_under_budget(multi_shot):
    assert cap_frames(multi_shot.shots, len(multi_shot.shots) + 6) == multi_shot.shots
