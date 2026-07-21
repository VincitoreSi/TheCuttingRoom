#!/usr/bin/env python3
"""engine/propose.py — turn the corpus's top viral exemplars into clone recipes.

This is the PROPOSE half of the producer (CLAUDE.md "Method" steps 1–4 + 7); `engine/render.py`
is the RENDER half. Nothing here costs money or needs an API key: it reads the corpus and the
schema-2 blueprints over HTTP and writes markdown.

    GET /api/corpus/{p}/top?n=      ranked exemplars (already sorted by virality_score)
    GET /api/analysis/{p}           the shortcode -> content_id index (the join key)
    GET /api/analysis/{p}/{cid}     the blueprint — the source of truth when it exists
    POST /api/studio/{p}            the proposal, into the human gate

`build_recipe()` here and `parse_recipe()` in engine/recipe.py are a MATCHED PAIR: the section
structure this module emits is the grammar that module parses, and tests/test_propose.py
round-trips one through the other so a drift in either fails loudly instead of producing a
recipe that renders half a video.

Pure functions wherever possible — `score_ease` and `build_recipe` take plain dicts and are
fully testable with no hub, and the only impure entry point is `select_targets`.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import re
from dataclasses import dataclass, field, replace

from engine import AGENT_NAME, KIND

log = logging.getLogger("sc.propose")


class ProposeError(RuntimeError):
    """No proposal can be made honestly — refuse rather than emit an empty recipe."""


# ---------------------------------------------------------------------------------------
# the "easy-to-make" rule
# ---------------------------------------------------------------------------------------
# Kept in ONE function on purpose so the whole heuristic is tunable in one place.
#
# "easy to make" == the simplest production: FEW SHOTS, SHORT, STATIC/single-shot, MINIMAL
# EDITING. Each candidate scores 0..100 (higher = easier) and passes the gate at
# `ease_threshold` (default EASE_THRESHOLD). Signals come from the schema-2 blueprint when one
# exists; when it doesn't we can only judge duration, so we score conservatively and say so.
#
# EVERY TERM IS CONTINUOUS AND MONOTONE — that is the whole point of the shape.
# The rule used to be three BANDS, and on a real Instagram corpus all three saturated: reels
# are 6-7 shots (the shot bands stopped at 4, so the strongest signal — 45 of 100 points —
# paid ZERO for every clip), 9.4-9.9s (every clip took the same +20) and fully static (every
# clip took the same +20). Every candidate scored exactly 40 against a gate of 55, nothing
# ever cleared it, and `rank_targets` silently fell through to its virality backfill: the
# operator asked for "easiest to remake" and was handed "most viral" with no way to tell.
#
# So: one extra shot, one extra second and one non-static shot each cost something, always.
# Two genuinely different clips can no longer collapse onto one number.
EASE_THRESHOLD = 55            # gate: candidates scoring >= this are "easy enough"

# 1) SHOTS — 45 / (1 + (n-1)/EASE_SHOT_DECAY): 1->45  2->22.5  4->11.25  6->7.5  7->6.4
#    EASE_SHOT_DECAY is 1.0, not the 1.5 the shape was first drafted with, and the real
#    corpus is why: at 1.5 a 6-shot 9.43s all-static reel scores 55.07 and clears a gate of
#    55 by seven hundredths of a point. A gate that a typical clip passes by accident is the
#    same failure as one nothing passes. At 1.0 the same reel scores 52.2 — below the gate
#    with room to see it — while a 2-shot 7s static clip still scores 70.1.
#    The same objection survives at 1.0 for ONE shape and it is worth saying out loud: a
#    4-shot all-static clip clears at <=10.21s and fails at 10.22s. No constant fixes that —
#    a continuous score against a fixed gate always has some length where a shape flips — but
#    it is why the duration this scores is the MEASURED one (clip_duration): a vision model's
#    estimate is off by up to 0.6s on the real corpus, which is wider than that margin.
EASE_SHOTS_WEIGHT = 45.0
EASE_SHOT_DECAY = 1.0
# 2) DURATION — linear from full marks at <=5s down to zero at EASE_LONG_S.
#    Duration is also the one term with a VETO: EASE_SHOTS_WEIGHT + EASE_STATIC_WEIGHT is 65,
#    which is above any sane gate, so without one a single-shot static clip would be "easy" at
#    ANY length — a 90s single-take talking head would outrank every 10s reel and the render
#    would hold one frame for a minute and a half. Worse, a clip with NO duration at all
#    scored the same 65, so missing data was rewarded. So a candidate can only be called easy
#    if we actually know its duration AND it is under EASE_LONG_S.
EASE_DURATION_WEIGHT = 30.0
EASE_LONG_S = 30.0             # at/over this, duration is worth nothing AND vetoes "easy"
EASE_DURATION_SPAN = 25.0      # ...and full marks at EASE_LONG_S - this (= 5s)
# 3) STATIC — pro rata on the fraction of shots whose camera does not move. `camera_movement`
#    of "None" counts as static: those shots are graphic/text cards with no camera at all,
#    which are the CHEAPEST thing in a blueprint to reproduce, not the dearest.
EASE_STATIC_WEIGHT = 20.0
EASE_MINIMAL_EDIT_POINTS = 12.0   # fallback when there is no per-shot camera data at all
# `camera_movement` is a short controlled-vocabulary field, so it is matched as a TOKEN SET
# and not by substring. Substring matching read "Drone shot" as static (it contains "one
# shot"), "Slow zoom, minimal movement" as static (it contains "minimal") and "Handheld with
# still moments" as static (it contains "still") — each false positive buying the full static
# bonus for the shot types that are hardest to reproduce. Anything not recognised counts as
# movement, which is the safe direction: an unknown phrasing makes a clip look harder, never
# easier.
_STATIC_CAMERA = frozenset((
    "static", "static camera", "static shot", "stationary", "fixed", "fixed camera",
    "locked", "locked off", "locked-off", "lock off", "tripod", "on a tripod",
    "no movement", "no camera movement", "none", "n/a", "na", "no", "still",
))
# ...the free-text `global_style` sentence is a different problem: it IS prose, so a substring
# scan is the only thing that works there. It is only ever a fallback (see score_ease step 3).
_STATIC_HINTS = ("static", "locked", "no cut", "no-cut", "single shot", "single-shot",
                 "one shot", "one-shot", "minimal", "none", "fixed", "still", "no movement")

BACKFILL_ORDERS = ("virality", "ease")


def _pts(v: float) -> str:
    """`+7.5` / `+45` — what one term actually paid, for the `reasons` trail.

    Sub-0.05 payments are printed with two significant figures rather than rounded to `+0`:
    the shots term never reaches zero (that is the point of the curve), so a trail that says
    `+0` would contradict the rule it is explaining.
    """
    if 0 < v < 0.05:
        return f"+{v:.2g}"
    return f"+{round(v, 1):g}"


def _is_static_camera(movement: str) -> bool:
    """Does this `camera_movement` describe a camera that does not move?

    Token-set match, not substring: "Drone shot" is not static because it happens to contain
    "one shot". Compound values ("Static (locked off)", "Static, no movement") are accepted
    only when EVERY part is a no-movement token.
    """
    parts = [p.strip(" .-_") for p in re.split(r"[,/;()]+", movement.strip().lower())]
    parts = [p for p in parts if p]
    return bool(parts) and all(p in _STATIC_CAMERA for p in parts)


def shots_points(n_shots: int) -> float:
    """Continuous, strictly decreasing in the shot count. Never reaches zero, so shot 8 vs
    shot 12 still separates — a band that pays nothing beyond 4 is how this rule died."""
    if n_shots < 1:
        return 0.0
    return EASE_SHOTS_WEIGHT / (1.0 + (n_shots - 1) / EASE_SHOT_DECAY)


def duration_points(dur: float) -> float:
    """Full marks under EASE_LONG_S - EASE_DURATION_SPAN, linear to zero at EASE_LONG_S."""
    frac = (EASE_LONG_S - dur) / EASE_DURATION_SPAN
    return EASE_DURATION_WEIGHT * min(1.0, max(0.0, frac))


def static_points(static_shots: int, n_shots: int) -> float:
    """Pro rata: 6/6 static pays 20, 3/7 pays 8.6. The old all-or-nothing rule threw that
    variation away, and it is the only one of the three signals that genuinely varies on a
    corpus of same-length same-shape reels.

    `n_shots` here is the number of shots that actually CARRY camera data, not the shot count
    — see score_ease step 3."""
    if n_shots < 1:
        return 0.0
    return EASE_STATIC_WEIGHT * (min(static_shots, n_shots) / n_shots)


def _seconds(x) -> float | None:
    """A duration in seconds, or None. Rejects the un-scoreable: NaN/inf (a model can write
    either), and <= 0 — a zero duration is a FAILED measurement, not an instant clip, and
    scoring it as one paid full marks for the term it should have disqualified."""
    v = _fnum(x)
    if v is None or not math.isfinite(v) or v <= 0:
        return None
    return v


def clip_duration(row: dict, blueprint: dict | None) -> tuple[float | None, str]:
    """How long the clip is, and where that number came from — MEASURED beats ESTIMATED.

    `row["duration_s"]` is the platform's own `video_duration` (see the scraper's
    normalize.py): it is measured. `video_metadata.estimated_duration_seconds` is a vision
    model's guess at the same quantity, and on the real corpus one of six blueprints is off by
    0.633s (9.5 vs 10.133) — worth 0.76 ease points, five times the ~0.14 gaps that separate
    the rest of the pool. Under the old duration BANDS both values fell in the same bucket so
    the preference was harmless; making duration continuous is exactly what turned it into a
    ranking bug. Search rows (`/corpus/{p}/search`) carry no duration at all, hence the
    fallback rather than a hard requirement.

    ONE function so that the score, the CLI table (`Target.duration_s`) and the recipe's
    `**duration:**` line can never print three different numbers for one clip.
    """
    measured = _seconds(row.get("duration_s")) if row else None
    if measured is not None:
        return measured, "measured"
    vm = (blueprint or {}).get("video_metadata") or {}
    est = _seconds(vm.get("estimated_duration_seconds"))
    if est is not None:
        return est, "estimated"
    return None, "unknown"


@dataclass
class Ease:
    """How cheap this exemplar is to reproduce, and why.

    `score` is a float, deliberately: rounding to an int re-introduces exactly the collapse
    this rule exists to avoid (a 9.43s and a 9.87s six-shot reel are both "52"), and the
    scores are printed through a formatter anyway.
    """
    score: float
    easy: bool
    reasons: list[str] = field(default_factory=list)
    threshold: float = EASE_THRESHOLD
    eligible: bool = True
    """False when the clip cannot be called easy at ANY threshold — we do not know how long it
    is, or it is EASE_LONG_S or longer. The shots + static terms alone total 65, above any
    sane gate, so without this a single-shot clip of unknown or unlimited length would clear
    on two signals while the third said nothing at all."""

    @property
    def summary(self) -> str:
        return ", ".join(self.reasons) or "n/a"

    def at(self, threshold: float) -> "Ease":
        """The same measurement re-gated at another threshold. The SCORE never moves — only
        the verdict — which is what makes an in-run threshold restore safe to apply.
        Ineligibility survives re-gating: it is a property of the clip, not of the gate."""
        return replace(self, easy=self.eligible and self.score >= threshold,
                       threshold=float(threshold))


@dataclass
class Target:
    """One selected exemplar: the corpus row, its blueprint (if any) and its ease verdict."""
    row: dict
    blueprint: dict | None
    ease: Ease
    content_id: str | None = None
    virality_score: float = 0.0

    @property
    def n_shots(self) -> int | None:
        if not self.blueprint:
            return None
        shots = self.blueprint.get("shots")
        return len(shots) if isinstance(shots, list) else None

    @property
    def duration_s(self) -> float | None:
        """The SAME number score_ease used — see clip_duration. This used to be a separate
        `estimate or row` expression, so the CLI table could print 12.4s next to a score
        computed from 0s with no way to reconcile the two."""
        return clip_duration(self.row, self.blueprint)[0]

    @property
    def title(self) -> str:
        vm = (self.blueprint or {}).get("video_metadata") or {}
        return (vm.get("one_line_summary") or (self.row.get("caption") or "").strip()
                or "clone")


def _fnum(x, default=None) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _g(x) -> str:
    """`52.18`, `55`, `9.43` — a score/threshold for a human, never `52.180000000000004`."""
    return f"{round(float(x), 2):g}"


def score_ease(row: dict, blueprint: dict | None,
               threshold: float = EASE_THRESHOLD) -> Ease:
    """Score how easy this exemplar is to remake, 0..100 (higher = easier).

    The rule, in order of weight — every term continuous and monotone:
      1. FEW SHOTS  — 45 / (1 + (n-1)/1.0):  1->45  2->22.5  4->11.25  6->7.5  7->6.4
      2. SHORT      — 30 * clamp((30 - d)/25):  <=5s->30  9.4s->24.7  15s->18  >=30s->0
                      ...and a VETO: an unknown duration, or one >= EASE_LONG_S, cannot be
                      "easy" at any threshold (shots + static alone total 65).
      3. STATIC     — 20 * (static shots / shots WITH CAMERA DATA); with no per-shot camera
                      data at all, "minimal editing" in the global_style text pays +12.
      4. NO BLUEPRINT — only duration is knowable, so the ceiling is 30. Flagged in reasons.

    Every `reasons` entry carries what that term actually paid (`7 shots +6.4`), because the
    first question a 51.03 provokes is "which term ate the points" and the answer should not
    require re-running the arithmetic by hand.

    Returns an Ease; `easy` is `eligible and score >= threshold`.
    """
    reasons: list[str] = []
    score = 0.0

    shots = (blueprint or {}).get("shots")
    shots = shots if isinstance(shots, list) else []      # a malformed blueprint is one bad
    n_shots = len(shots)                                  # candidate, not a dead run
    gstyle = (blueprint or {}).get("global_style") or {}

    dur, dur_src = clip_duration(row or {}, blueprint)

    # 1) FEW SHOTS — the single strongest signal, and the one that was dead.
    if n_shots >= 1:
        pts = shots_points(n_shots)
        score += pts
        reasons.append(("single-shot" if n_shots == 1 else f"{n_shots} shots")
                       + f" {_pts(pts)}")
    elif blueprint is not None:
        # An analyzed clip whose `shots[]` is empty or malformed. Silence here read as "no
        # shot data was needed"; it means the strongest term paid nothing and nobody knows.
        reasons.append("shots unknown +0")

    # 2) SHORT — cheap to reproduce. Every second costs, right up to EASE_LONG_S.
    if dur is not None:
        pts = duration_points(dur)
        score += pts
        reasons.append(f"{dur:g}s" + (" (long)" if dur >= EASE_LONG_S else "")
                       + f" {_pts(pts)}" + (" (est)" if dur_src == "estimated" else ""))
    eligible = dur is not None and dur < EASE_LONG_S
    if not eligible:
        reasons.append("duration unknown — cannot be called easy" if dur is None else
                       f"{dur:g}s is over {EASE_LONG_S:g}s — cannot be called easy")

    # 3) STATIC / NO CUTS / MINIMAL EDITING — per-shot camera movement, pro rata over the
    #    shots that actually CARRY camera data. Dividing by the shot count instead treated
    #    every dataless shot as moving, so a blueprint that learned one of its seven shots was
    #    static LOST 9 points against the same blueprint that knew nothing — the score fell as
    #    the information improved. The global_style text is only a FALLBACK: it is one
    #    sentence about the whole clip, so it cannot distinguish 3-of-7 static from 7-of-7 and
    #    must never overrule shots that can.
    style_text = " ".join(str(gstyle.get(k) or "") for k in
                          ("pacing", "editing_style", "visual_style")).lower()
    known = [str((s or {}).get("camera_movement") or "").strip()
             for s in shots if isinstance(s, dict)]
    known = [m for m in known if m]
    static_shots = sum(1 for m in known if _is_static_camera(m))
    if known:
        pts = static_points(static_shots, len(known))
        score += pts
        seen = (f"{static_shots}/{len(known)}" if len(known) == n_shots
                else f"{static_shots}/{len(known)} known of {n_shots}")
        reasons.append(f"static camera {seen} {_pts(pts)}")
    elif any(h in style_text for h in _STATIC_HINTS):
        score += EASE_MINIMAL_EDIT_POINTS
        reasons.append(f"minimal editing {_pts(EASE_MINIMAL_EDIT_POINTS)}")

    # 4) No blueprint yet → duration-only judgement. Be honest about it in the reasons.
    if blueprint is None:
        reasons.append("no blueprint (duration-only)")

    # max() FIRST, and the order is load-bearing: max(0.0, nan) is 0.0 while min(100.0, nan)
    # is 100.0, so the other order would turn a NaN into a perfect score. Nothing can reach
    # here with a NaN today (`_seconds` rejects them) — this is the belt to that braces.
    score = round(min(100.0, max(0.0, score)), 2)
    return Ease(score=score, easy=eligible and score >= threshold, reasons=reasons,
                threshold=float(threshold), eligible=eligible)


# ---------------------------------------------------------------------------------------
# threshold lifecycle (D2-D6) — and THE safety asymmetry
# ---------------------------------------------------------------------------------------
def automation_threshold(current: float, restore_to: int | None) -> float:
    """The ONLY function allowed to change the ease threshold without a human, and the one
    place the safety asymmetry is enforced — by construction, not by convention.

    Automation may only ever RAISE the gate back to a value a human already chose. Only a
    human may lower it. The asymmetry is not a preference:

      * a wrong RESTORE means fewer clips clear the gate, the backfill visibly kicks in, the
        run says so, and a human moves the number back. Recoverable, and loud.
      * a wrong AUTO-LOWER means the gate quietly admits clips nobody would call easy, and
        the recipes still look exactly like good ones. Silent, and the operator's only clue
        is that the output got worse.

    `max()` is the whole enforcement: this function CANNOT return a value below `current`,
    so no caller — present or future — can lower the threshold through it, whatever it
    passes in. tests/test_propose.py asserts that over a grid of pairs.
    """
    if restore_to is None:
        return current
    return max(current, float(restore_to))


def restore_origin(threshold: float, restore_to: int | None,
                   default_threshold: float = EASE_THRESHOLD) -> int | None:
    """Where the threshold came from — the value a restore should aim at (D6), or None.

    An ALREADY RECORDED origin is returned untouched, whatever the threshold now is. That is
    the whole rule: `ease_restore_to` is a knob a human can also write, and recomputing it
    from a high-water mark silently rewrote their number — an operator who set
    `ease_threshold: 40, ease_restore_to: 45` had the 45 replaced by the schema default 55 on
    the next run, and one who recorded 70 and then briefly raised the threshold to 70 lost
    the 70 entirely, so a later restore parked the gate 15 points below where they had it.

    Only the empty case is filled in, and only downwards: with nothing recorded and the
    threshold below the schema default, the default is what a restore should aim at.

    An existing record is never CLEARED here either — a record at or below the threshold is
    already inert (`diagnose_ease` requires `restore_to > threshold`), and clearing it is the
    business of a restore that actually happened.

    Known limit: an excursion ABOVE the schema default that no run ever saw lowered is not
    remembered — restore then aims at the default. Recording it would mean an agent writing
    a human's number back at itself every run, which is a worse trade than restoring to 55
    when a human once ran 70.
    """
    if restore_to is not None:
        return int(restore_to)
    return int(default_threshold) if threshold < default_threshold else None


@dataclass
class EaseDiagnosis:
    """What the ease gate did to this pool, in numbers, ready for a human AND for /api/logs.

    This is the feature: the failure it names ("nothing cleared the gate, so you are being
    handed most-viral instead of easiest") used to be invisible — the run printed a table of
    picks that looked exactly like a working one.
    """
    kind: str                       # ok | starved | restore_ready | restored
    msg: str
    threshold: float
    considered: int
    cleared: int
    best_score: float
    count: int
    suggest_threshold: int | None = None
    restore_to: int | None = None
    restore_cleared: int | None = None
    applied_threshold: float | None = None

    @property
    def event(self) -> str:
        return f"ease.{self.kind}"

    @property
    def level(self) -> str:
        return "warning" if self.kind == "starved" else "info"

    @property
    def actionable(self) -> bool:
        return self.kind != "ok"

    @property
    def data(self) -> dict:
        """Structured payload for POST /api/logs — every number in `msg`, so the Dashboard
        can render this later without parsing prose back out of a sentence."""
        return {"kind": self.kind, "ease_threshold": self.threshold,
                "considered": self.considered, "cleared": self.cleared,
                "best_score": self.best_score, "count": self.count,
                "suggest_threshold": self.suggest_threshold,
                "ease_restore_to": self.restore_to,
                "restore_cleared": self.restore_cleared,
                "applied_threshold": self.applied_threshold}

    def unapplied(self, reason: str) -> "EaseDiagnosis":
        """The restore did NOT happen after all — re-word to match what actually occurred.

        `msg` and `kind` are decided before the write is attempted, so a hub that 500s, a
        `--dry-run`, or a mid-run edit by a human used to leave the run printing "RESTORED
        ease_threshold 40 -> 55" and posting `ease.restored` while the gate was still 40 and
        every pick had been ranked at 40. The Dashboard would render a restore that never
        happened. Anything that does not write must come back through here.
        """
        head = (f"{self.restore_cleared} of {self.considered} candidates now clear "
                f"ease >= {self.restore_to}")
        return replace(self, kind="restore_ready", applied_threshold=None,
                       msg=(f"{head} — {reason} ease_threshold is still "
                            f"{_g(self.threshold)}; restore it to {self.restore_to} to rank "
                            f"by ease again."))


def diagnose_ease(targets: list[Target], *, count: int, threshold: float,
                  restore_to: int | None = None,
                  auto_restore: bool = False) -> EaseDiagnosis:
    """Did the ease gate work, is it starved, and may it be handed back (D4/D5)?

    "Enough data to restore" is MORE THAN `count` candidates at or above the recorded
    threshold — strictly more, on purpose. Restoring the moment exactly `count` clear it
    would flap: one clip re-analyzed, or one scrape later, and the gate starves again.

    Counting goes through `Ease.at(...).easy` rather than comparing raw scores, so a
    candidate that cannot be easy at any threshold (unknown or >= EASE_LONG_S duration) is
    not counted as clearing one.
    """
    considered = len(targets)
    cleared = sum(1 for t in targets if t.ease.at(threshold).easy)

    # The best score a lower gate could actually admit. Ineligible candidates are excluded
    # (no threshold reaches them), and so are blueprint-less ones whenever anything HAS a
    # blueprint: their ceiling is EASE_DURATION_WEIGHT, so advising the operator to lower the
    # gate to a duration-only score would admit every un-analyzed clip in the corpus.
    judged = [t for t in targets if t.ease.eligible and t.blueprint is not None]
    admissible = judged or [t for t in targets if t.ease.eligible]
    best = max((t.ease.score for t in admissible), default=0.0)
    blind = sum(1 for t in targets if t.blueprint is None)

    restore_cleared = (sum(1 for t in targets if t.ease.at(restore_to).easy)
                       if restore_to is not None else None)
    restorable = (restore_to is not None and restore_to > threshold
                  and (restore_cleared or 0) > count)

    if restorable:
        applied = automation_threshold(threshold, restore_to) if auto_restore else None
        head = (f"{restore_cleared} of {considered} candidates now clear "
                f"ease >= {restore_to}")
        if auto_restore:
            msg = (f"{head} — RESTORED ease_threshold {_g(threshold)} -> {restore_to} "
                   f"(ease_auto_restore is on). Ranking by ease again.")
        else:
            msg = (f"{head} — restore ease_threshold to {restore_to}. "
                   f"Nothing was changed: set ease_auto_restore to let the run do it.")
        return EaseDiagnosis(kind="restored" if auto_restore else "restore_ready", msg=msg,
                             threshold=threshold, considered=considered, cleared=cleared,
                             best_score=best, count=count, restore_to=restore_to,
                             restore_cleared=restore_cleared, applied_threshold=applied)

    if considered and cleared == 0:
        head = f"0 of {considered} candidates cleared ease >= {_g(threshold)}"
        tail = ("until then every pick is the virality backfill, not the easiest to remake.")
        if blind == considered:
            # Nothing has been analyzed, so ease is duration-only and capped at 30. Telling
            # the operator to lower the gate to that ceiling would destroy it — the pool is
            # missing its two strongest signals, not scoring badly on them.
            suggest = None
            msg = (f"{head} — none of them has a blueprint yet, so ease is duration-only and "
                   f"cannot exceed {EASE_DURATION_WEIGHT:g}. Run the analysis-engine stage "
                   f"rather than lowering the gate; {tail}")
        elif not admissible:
            suggest = None
            msg = (f"{head} — every candidate is {EASE_LONG_S:g}s or longer, or has no known "
                   f"duration, so no threshold can admit one. {tail.capitalize()}")
        else:
            suggest = max(0, math.floor(best))
            msg = (f"{head} — best score was {_g(best)}. Lower ease_threshold to {suggest} "
                   f"to rank by ease; " + tail
                   + (f" ({blind} of {considered} have no blueprint yet and are scored on "
                      f"duration alone.)" if blind else ""))
        return EaseDiagnosis(kind="starved", msg=msg, threshold=threshold,
                             considered=considered, cleared=cleared, best_score=best,
                             count=count, suggest_threshold=suggest, restore_to=restore_to,
                             restore_cleared=restore_cleared)

    return EaseDiagnosis(kind="ok",
                         msg=f"{cleared} of {considered} candidates cleared "
                             f"ease >= {_g(threshold)}",
                         threshold=threshold, considered=considered, cleared=cleared,
                         best_score=best, count=count, restore_to=restore_to,
                         restore_cleared=restore_cleared)


# ---------------------------------------------------------------------------------------
# the shortcode -> content_id join
# ---------------------------------------------------------------------------------------
_SHORTCODE_RE = re.compile(r"/(?:reel|reels|p|tv)/([^/?#]+)")


def shortcode(url: str | None) -> str | None:
    """The reel/post shortcode in a post URL.

    This is load-bearing: `GET /api/corpus/{p}/top` rows carry `url` but NO `content_id`,
    while blueprints are keyed by `content_id`. Without this join every top exemplar would
    look un-analyzed and every recipe would fall back to a duration-only stub.
    """
    if not url:
        return None
    m = _SHORTCODE_RE.search(str(url))
    return m.group(1) if m else None


def content_id_index(hub, platform: str) -> dict[str, str]:
    """shortcode -> content_id, built from the analysis listing (which carries both)."""
    try:
        listing = hub.analysis_list(platform)
    except Exception as e:                              # noqa: BLE001 — never fatal
        log.warning("analysis listing unavailable; blueprints cannot be joined",
                    extra={"err": str(e)})
        return {}
    idx = {}
    for a in listing:
        sc, cid = shortcode(a.get("url")), a.get("content_id")
        if sc and cid:
            idx[sc] = cid
    return idx


# ---------------------------------------------------------------------------------------
# target selection (CLAUDE.md "Method" steps 1–2)
# ---------------------------------------------------------------------------------------
def rank_targets(targets: list[Target], count: int,
                 backfill_order: str = "virality") -> list[Target]:
    """Easy-first, virality-second — then backfill.

    The ease gate is a preference, not a hard filter: if fewer than `count` candidates clear
    it we top up from the remainder rather than returning a short list. Those picks carry
    their real (low) ease score, so the operator can see what they are.

    `backfill_order` decides only the ORDER OF THAT REMAINDER (D7):
      "virality" (default) — the proven winners first, today's behaviour.
      "ease"               — the least-bad-to-remake first, for when the point of the run is
                             production cost and a starved gate should not silently invert it.
    Anything else falls back to "virality"; the CLI warns on an unknown value rather than
    guessing at one.
    """
    easy = sorted((t for t in targets if t.ease.easy),
                  key=lambda t: (-t.ease.score, -t.virality_score))
    picks = easy[:count]
    if len(picks) < count:
        chosen = {id(t) for t in picks}
        if backfill_order == "ease":
            def key(t):
                # Ineligible candidates last, whatever they scored: a 90s single-shot clip
                # scores 65 on shots+static alone, and leading a "least-bad to remake" list
                # with a clip we know is unremakeable-long is the same lie the gate rejects.
                return (not t.ease.eligible, -t.ease.score, -t.virality_score)
        else:
            def key(t):
                return (-t.virality_score, -t.ease.score)
        rest = sorted((t for t in targets if id(t) not in chosen), key=key)
        picks += rest[: count - len(picks)]
    return picks


def score_targets(hub, platform: str, *, pool: int = 15, topic: str | None = None,
                  prefer_blueprint: bool = True, content_ids: list[str] | None = None,
                  threshold: float = EASE_THRESHOLD) -> list[Target]:
    """Fetch the candidate pool and score every one of them — NO ranking, NO truncation.

    Split out of `select_targets` because the diagnosis is about the POOL, not about the
    picks: "0 of 15 cleared the gate" cannot be said from a list that was already cut down to
    5, and an in-run threshold restore has to re-gate candidates that ranking would have
    thrown away.
    """
    if content_ids:
        full = {r.get("content_id"): r for r in hub.content(platform) if r.get("content_id")}
        missing = [c for c in content_ids if c not in full]
        if missing:
            raise ProposeError(f"no corpus row for content_id(s): {', '.join(missing)}")
        rows = [full[c] for c in content_ids]
    elif topic:
        rows = hub.corpus_search(platform, topic, k=pool)
    else:
        rows = hub.corpus_top(platform, pool)
    if not rows:
        raise ProposeError(
            f"the {platform} corpus is EMPTY"
            + (f" for topic {topic!r}" if topic else "")
            + " — nothing to propose. Run the pipeline (scrape -> analyze -> media -> "
              "analysis-engine) first.")

    idx = content_id_index(hub, platform) if prefer_blueprint else {}
    targets: list[Target] = []
    for row in rows:
        cid = row.get("content_id") or idx.get(shortcode(row.get("url")))
        bp = hub.blueprint(platform, cid) if (prefer_blueprint and cid) else None
        targets.append(Target(row=row, blueprint=bp,
                              ease=score_ease(row, bp, threshold), content_id=cid,
                              virality_score=_fnum(row.get("virality_score"), 0.0)))

    with_bp = sum(1 for t in targets if t.blueprint)
    if prefer_blueprint and with_bp == 0:
        log.warning("no schema-2 blueprints found — recipes will have no shot prompts until "
                    "the analysis-engine stage runs; ease is duration-only",
                    extra={"platform": platform, "pool": len(targets)})
    log.info("scored candidates", extra={"platform": platform, "candidates": len(targets),
                                         "with_blueprint": with_bp,
                                         "ease_threshold": threshold})
    return targets


def select_targets(hub, platform: str, *, count: int = 5, pool: int = 15,
                   topic: str | None = None, prefer_blueprint: bool = True,
                   content_ids: list[str] | None = None,
                   threshold: float = EASE_THRESHOLD,
                   backfill_order: str = "virality") -> list[Target]:
    """Pick the `count` easiest-to-make winners out of the top `pool` viral exemplars.

    `content_ids` bypasses ranking entirely and proposes exactly those exemplars. Ranking
    is the right default, but it cannot reach a specific clip: a freshly-scraped creator
    lands mid-corpus, so its blueprint would never surface no matter how wide the pool.

    `score_targets` + `rank_targets` in one call, for callers that do not need the diagnosis.
    `cli.py propose` uses the two halves directly so it can report on the whole pool.
    """
    targets = score_targets(hub, platform, pool=pool, topic=topic,
                            prefer_blueprint=prefer_blueprint, content_ids=content_ids,
                            threshold=threshold)
    if content_ids:
        return targets              # explicitly named: honour the caller's order, no ranking
    return rank_targets(targets, count, backfill_order)


# ---------------------------------------------------------------------------------------
# the recipe markdown  (matched pair with engine/recipe.py::parse_recipe)
# ---------------------------------------------------------------------------------------
def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n].strip("-") or "clone"


def recipe_filename(target: Target, rank: int = 1, date: str | None = None) -> str:
    """`<YYYY-MM-DD>-similar-<slug>-<cid fragment>.md` — the studio join key.

    The trailing digits matter: engine/recipe.py recovers a `content_id_prefix` from them to
    re-find the source row at render time, and slug_from_filename() strips them back off.
    """
    date = date or _dt.date.today().isoformat()
    cid = target.content_id or shortcode(target.row.get("url")) or f"rank{rank}"
    return f"{date}-similar-{slugify(target.title)}-{slugify(str(cid), 12)}.md"


def _audio_block(blueprint: dict | None, row: dict) -> str:
    """The copy-ready `## Audio` block every producer output must carry (Producer SPI §4).

    Parsed by the Dashboard (Dashboard/src/lib/soundStrip.ts) as well as by a human, so the
    bullet keys here are a contract — do not rename them without changing that parser.
    """
    a = (blueprint or {}).get("audio") or {}
    strat = (blueprint or {}).get("audio_strategy") or {}
    title = a.get("audio_title") or row.get("audio_title")
    artist = a.get("audio_artist") or row.get("audio_artist")
    page = a.get("sound_page_url") or row.get("sound_page_url")
    lines = ["## Audio", ""]
    if title:
        lines.append(f"- **Sound:** {title}" + (f" — {artist}" if artist else ""))
    if a.get("music_description"):
        lines.append(f"- **Music:** {a['music_description']}")
    if strat.get("audio_type"):
        lines.append(f"- **Audio type:** {strat['audio_type']}")
    if strat.get("reuse_recommendation"):
        lines.append(f"- **Reuse:** {strat['reuse_recommendation']}")
    if page:
        lines.append(f"- **Sound page:** {page}")
    if strat.get("substitute_brief"):
        lines.append(f"- **If not reusable, substitute:** {strat['substitute_brief']}")
    if len(lines) == 2:
        lines.append("- _No audio metadata captured for this clip — pick a trending "
                     "reusable sound in the same mood._")
    return "\n".join(lines)


def _fmt_duration(dur) -> str:
    """`9.75` not `9.751999855041504` — the duration is re-parsed by engine/recipe.py and
    ends up as ffmpeg frame holds, where a 12-decimal float is only noise."""
    d = _fnum(dur)
    return "?" if d is None else f"{round(d, 2):g}"


def build_recipe(platform: str, row: dict, blueprint: dict | None, ease: Ease) -> str:
    """Render the CLONE recipe markdown for one exemplar.

    The section structure is a CONTRACT with engine/recipe.py::parse_recipe:
      `## Why this one` (**duration:** / **source reel:**), `## Look & feel`,
      `## Regeneration guide` (**Master style prompt:** / **Global negative prompt:** /
      **Consistency:**), `## Shot list …` with `### Shot N — Xs · Camera` and per-shot
      `**Prompt:** / **Negative:** / **On-screen text:**`, `## Replicable formula`, `## Audio`.
    tests/test_propose.py round-trips this through parse_recipe; keep them in step.
    """
    vm = (blueprint or {}).get("video_metadata") or {}
    gstyle = (blueprint or {}).get("global_style") or {}
    guide = (blueprint or {}).get("regeneration_guide") or {}
    shots = (blueprint or {}).get("shots")
    shots = [s for s in shots if isinstance(s, dict)] if isinstance(shots, list) else []
    vf = (blueprint or {}).get("virality_formula") or {}

    summary = vm.get("one_line_summary") or (row.get("caption") or "")[:120] or "clone"
    # The MEASURED duration when the corpus row has one (clip_duration): this number becomes
    # the render's total length, and the operator attaches the original sound by hand, so a
    # vision model's estimate drifting 0.6s from the real clip is cuts landing off the beat.
    dur = clip_duration(row, blueprint)[0]
    src_url = row.get("url") or (blueprint or {}).get("url")

    L: list[str] = []
    L.append(f"# Clone recipe — {summary}")
    L.append("")
    L.append(f"> Proposed by `uv run cli.py propose --platform {platform}` as a **{KIND}** "
             f"recipe from the **{AGENT_NAME}** producer. Faithfully reproduces a proven "
             f"winner 1:1.")
    L.append("")
    L.append("## Why this one (easy-to-make best)")
    L.append(f"- **virality_score:** {row.get('virality_score')}   "
             f"**tier:** {row.get('tier')}")
    L.append(f"- **ease score:** {_g(ease.score)}/100 "
             f"(gate {_g(ease.threshold)}: {'easy' if ease.easy else 'BACKFILL'}) "
             f"— {ease.summary}")
    L.append(f"- **shots:** {len(shots) if blueprint else 'unknown (not analyzed yet)'}"
             f"   **duration:** {_fmt_duration(dur)}s")
    if row.get("creator"):
        L.append(f"- **source creator:** {row['creator']}")
    if src_url:
        L.append(f"- **source reel:** {src_url}")
    L.append("")

    if gstyle:
        L.append("## Look & feel")
        for k in ("overall_mood", "visual_style", "color_grading", "lighting_style",
                  "pacing", "editing_style"):
            if gstyle.get(k):
                L.append(f"- **{k.replace('_', ' ')}:** {gstyle[k]}")
        if gstyle.get("dominant_color_palette_hex"):
            L.append(f"- **palette:** {', '.join(gstyle['dominant_color_palette_hex'])}")
        L.append("")

    if guide:
        L.append("## Regeneration guide")
        if guide.get("master_style_prompt"):
            L.append(f"- **Master style prompt:** {guide['master_style_prompt']}")
        if guide.get("global_negative_prompt"):
            L.append(f"- **Global negative prompt:** {guide['global_negative_prompt']}")
        if guide.get("consistency_notes"):
            L.append(f"- **Consistency:** {guide['consistency_notes']}")
        if guide.get("assembly_instructions"):
            L.append(f"- **Assembly:** {guide['assembly_instructions']}")
        if guide.get("recommended_models"):
            L.append(f"- **Recommended models:** {', '.join(guide['recommended_models'])}")
        L.append("")

    if shots:
        L.append("## Shot list (generation-ready prompts)")
        for s in shots:
            sdur = s.get("duration")
            cam = s.get("camera_movement") or s.get("camera_shot_size") or ""
            L.append(f"### Shot {s.get('shot_index', '?')}"
                     + (f" — {sdur:g}s" if isinstance(sdur, (int, float)) else "")
                     + (f" · {cam}" if cam else ""))
            if s.get("description"):
                L.append(f"- {s['description']}")
            if s.get("on_screen_text"):
                L.append(f"- **On-screen text:** {s['on_screen_text']}")
            if s.get("generation_prompt"):
                L.append(f"- **Prompt:** {s['generation_prompt']}")
            if s.get("negative_prompt"):
                L.append(f"- **Negative:** {s['negative_prompt']}")
            L.append("")
    elif blueprint is None:
        L.append("## Shot list")
        L.append("_This clip has no blueprint yet — run the AnalysisEngine stage "
                 "(analysis-engine) so `shots[]` + prompts are generated, then re-run._")
        L.append("")

    if vf.get("replicable_formula"):
        L.append("## Replicable formula")
        L.append(vf["replicable_formula"])
        L.append("")

    L.append(_audio_block(blueprint, row))
    L.append("")
    return "\n".join(L)
