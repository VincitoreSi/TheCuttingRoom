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
import re
from dataclasses import dataclass, field

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
# EASE_THRESHOLD. Signals come from the schema-2 blueprint when one exists; when it doesn't we
# can only judge duration, so we score conservatively and say so in the reasons.
EASE_THRESHOLD = 55          # gate: candidates scoring >= this are "easy enough"
EASE_MAX_SHOTS = 2           # <= this many shots is the strongest ease signal
EASE_SHORT_S = 15.0          # a clip this short or shorter is cheap to reproduce
_STATIC_HINTS = ("static", "locked", "no cut", "no-cut", "single shot", "single-shot",
                 "one shot", "one-shot", "minimal", "none", "fixed", "still", "no movement")


@dataclass
class Ease:
    """How cheap this exemplar is to reproduce, and why."""
    score: int
    easy: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return ", ".join(self.reasons) or "n/a"


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
        return len((self.blueprint or {}).get("shots") or []) if self.blueprint else None

    @property
    def duration_s(self) -> float | None:
        vm = (self.blueprint or {}).get("video_metadata") or {}
        return _fnum(vm.get("estimated_duration_seconds")) or _fnum(self.row.get("duration_s"))

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


def score_ease(row: dict, blueprint: dict | None) -> Ease:
    """Score how easy this exemplar is to remake, 0..100 (higher = easier).

    The rule, in order of weight:
      1. FEW SHOTS  — single-shot +45, <=EASE_MAX_SHOTS +32, <=4 +12, more scores nothing.
      2. SHORT      — <=7s +30, <=EASE_SHORT_S +20, <=30s +8, longer scores nothing.
      3. STATIC     — every shot's camera_movement reads as static/locked +20; failing that,
                      "minimal editing" anywhere in the global_style text +12.
      4. NO BLUEPRINT — only duration is knowable, so the ceiling is 30. Flagged in reasons.

    Returns an Ease; `easy` is `score >= EASE_THRESHOLD`.
    """
    reasons: list[str] = []
    score = 0

    shots = (blueprint or {}).get("shots") or []
    n_shots = len(shots)
    vm = (blueprint or {}).get("video_metadata") or {}
    gstyle = (blueprint or {}).get("global_style") or {}

    dur = _fnum(vm.get("estimated_duration_seconds"))
    if dur is None:
        dur = _fnum(row.get("duration_s"))

    # 1) FEW SHOTS — the single strongest signal.
    if blueprint is not None:
        if n_shots <= 1:
            score += 45
            reasons.append("single-shot")
        elif n_shots <= EASE_MAX_SHOTS:
            score += 32
            reasons.append(f"{n_shots} shots")
        elif n_shots <= 4:
            score += 12
            reasons.append(f"{n_shots} shots")
        else:
            reasons.append(f"{n_shots} shots (complex)")

    # 2) SHORT — cheap to reproduce.
    if dur is not None:
        if dur <= 7:
            score += 30
            reasons.append(f"{dur:g}s")
        elif dur <= EASE_SHORT_S:
            score += 20
            reasons.append(f"{dur:g}s")
        elif dur <= 30:
            score += 8
            reasons.append(f"{dur:g}s")
        else:
            reasons.append(f"{dur:g}s (long)")

    # 3) STATIC / NO CUTS / MINIMAL EDITING — read pacing + camera movement text.
    style_text = " ".join(str(gstyle.get(k) or "") for k in
                          ("pacing", "editing_style", "visual_style")).lower()
    cam_moves = [str(s.get("camera_movement") or "").lower() for s in shots]
    static_shots = sum(1 for m in cam_moves if any(h in m for h in _STATIC_HINTS))
    if blueprint is not None and shots and static_shots == len(shots):
        score += 20
        reasons.append("static camera")
    elif any(h in style_text for h in _STATIC_HINTS):
        score += 12
        reasons.append("minimal editing")

    # 4) No blueprint yet → duration-only judgement. Be honest about it in the reasons.
    if blueprint is None:
        reasons.append("no blueprint (duration-only)")

    score = max(0, min(100, score))
    return Ease(score=score, easy=score >= EASE_THRESHOLD, reasons=reasons)


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
def rank_targets(targets: list[Target], count: int) -> list[Target]:
    """Easy-first, virality-second — then backfill.

    The ease gate is a preference, not a hard filter: if fewer than `count` candidates clear
    it we top up from the highest-virality remainder rather than returning a short list. Those
    picks carry their real (low) ease score, so the operator can see what they are.
    """
    easy = sorted((t for t in targets if t.ease.easy),
                  key=lambda t: (-t.ease.score, -t.virality_score))
    picks = easy[:count]
    if len(picks) < count:
        chosen = {id(t) for t in picks}
        rest = sorted((t for t in targets if id(t) not in chosen),
                      key=lambda t: (-t.virality_score, -t.ease.score))
        picks += rest[: count - len(picks)]
    return picks


def select_targets(hub, platform: str, *, count: int = 5, pool: int = 15,
                   topic: str | None = None, prefer_blueprint: bool = True,
                   content_ids: list[str] | None = None) -> list[Target]:
    """Pick the `count` easiest-to-make winners out of the top `pool` viral exemplars.

    `content_ids` bypasses ranking entirely and proposes exactly those exemplars. Ranking
    is the right default, but it cannot reach a specific clip: a freshly-scraped creator
    lands mid-corpus, so its blueprint would never surface no matter how wide the pool.
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
        targets.append(Target(row=row, blueprint=bp, ease=score_ease(row, bp),
                              content_id=cid,
                              virality_score=_fnum(row.get("virality_score"), 0.0)))

    with_bp = sum(1 for t in targets if t.blueprint)
    if prefer_blueprint and with_bp == 0:
        log.warning("no schema-2 blueprints found — recipes will have no shot prompts until "
                    "the analysis-engine stage runs; ease is duration-only",
                    extra={"platform": platform, "pool": len(targets)})
    log.info("scored candidates", extra={"platform": platform, "candidates": len(targets),
                                         "with_blueprint": with_bp})
    if content_ids:
        return targets              # explicitly named: honour the caller's order, no ranking
    return rank_targets(targets, count)


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
    shots = (blueprint or {}).get("shots") or []
    vf = (blueprint or {}).get("virality_formula") or {}

    summary = vm.get("one_line_summary") or (row.get("caption") or "")[:120] or "clone"
    dur = vm.get("estimated_duration_seconds") or row.get("duration_s")
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
    L.append(f"- **ease score:** {ease.score}/100 — {ease.summary}")
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
