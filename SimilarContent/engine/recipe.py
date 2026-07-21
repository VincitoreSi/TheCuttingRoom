#!/usr/bin/env python3
"""engine/recipe.py — parse an approved clone recipe into a RenderPlan.

The recipe markdown is written by `engine/propose.py::build_recipe` and carries everything
needed to re-render the clip: the blueprint's per-shot generation prompts, the master style
prompt, merged negatives, verbatim on-screen text and the target duration. Those two modules
are a MATCHED PAIR — tests/test_propose.py round-trips one through the other, so a change to
the section grammar here must be made there too.

Pure and dependency-free on purpose — this is the part most likely to break when the recipe
format drifts, so it is fully unit-testable without a hub, an API key, or ffmpeg.

The cardinal rule here is FAIL LOUDLY. A half-parsed recipe would render a plausible-looking
video off placeholder prompts and burn real API credits doing it, so anything ambiguous
raises RecipeError rather than silently rendering less than the recipe asked for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Text that means "the blueprint never filled this in" — never send it to an image model.
_PLACEHOLDER_RE = re.compile(
    r"^\s*(_?no blueprint|_this clip has no blueprint|todo\b|tbd\b|n/?a\s*$|<[^>]+>\s*$)",
    re.I,
)
_SHOT_HEAD_RE = re.compile(
    r"^###\s+Shot\s+(\d+)\s*(?:—\s*([\d.]+)\s*s)?\s*(?:·\s*(.*))?$", re.I
)
_BULLET_KEY_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)$")
_PLAIN_BULLET_RE = re.compile(r"^-\s+(?!\*\*)(.+)$")


class RecipeError(ValueError):
    """The recipe cannot be rendered faithfully — refuse rather than guess."""


@dataclass
class Shot:
    index: int
    prompt: str
    duration_s: float | None = None
    description: str | None = None
    on_screen_text: str | None = None
    negative: str | None = None
    camera: str | None = None


@dataclass
class RenderPlan:
    file: str
    platform: str
    slug: str
    title: str
    shots: list[Shot] = field(default_factory=list)
    target_duration_s: float | None = None
    source_url: str | None = None
    content_id_prefix: str | None = None
    master_style_prompt: str | None = None
    global_negative_prompt: str | None = None
    consistency_notes: str | None = None
    replicable_formula: str | None = None
    audio_block: str | None = None


# ---------------------------------------------------------------------------------------
# section + bullet extraction
# ---------------------------------------------------------------------------------------
def extract_section(md: str, name: str) -> str | None:
    """Lift a `## Section` block (up to the next same-or-higher heading).

    Mirrors the Dashboard's extractSection so the agent and the UI agree on what a section
    is — notably, both treat `### Shot` subheadings as part of `## Shot list`.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    start, level = -1, 0
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m and m.group(2).strip().lower().startswith(name.lower()):
            start, level = i, len(m.group(1))
            break
    if start == -1:
        return None
    out = [lines[start]]
    for line in lines[start + 1:]:
        m = re.match(r"^(#{1,6})\s+", line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return "\n".join(out).strip()


def _bullets(block: str | None) -> dict[str, str]:
    """Parse `- **Key:** value` bullets, keeping multi-line values.

    Values continue across newlines until the next bullet, which matters: on-screen text
    is frequently two lines ("Me wearing skinny jeans all the time before / Vs now....")
    and truncating it would burn the wrong words into the frame.
    """
    out: dict[str, str] = {}
    if not block:
        return out
    key = None
    for line in block.split("\n"):
        m = _BULLET_KEY_RE.match(line)
        if m:
            key = m.group(1).strip().lower()
            out[key] = m.group(2).strip()
        elif key and line.strip() and not line.startswith(("#", "- ")):
            out[key] += "\n" + line.strip()
        elif line.startswith(("- ", "#")):
            key = None
    return {k: v.strip() for k, v in out.items()}


def _is_placeholder(text: str | None) -> bool:
    return not text or not text.strip() or bool(_PLACEHOLDER_RE.match(text))


def slug_from_filename(file: str) -> str:
    """`2026-07-19-similar-a-static-product-grid-in-three-colours-123456789012.md`
    -> `a-static-product-grid-in-three-colours` (matches the existing assets/ dirs)."""
    stem = file[:-3] if file.endswith(".md") else file
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)      # date
    stem = re.sub(r"^(similar|proposal|auto|template)-", "", stem)
    stem = re.sub(r"-\d{6,}$", "", stem)                  # trailing content_id fragment
    return stem or "clone"


# ---------------------------------------------------------------------------------------
# shots
# ---------------------------------------------------------------------------------------
def _parse_shots(shot_list: str | None) -> list[Shot]:
    if not shot_list:
        return []
    blocks: list[tuple[re.Match, list[str]]] = []
    current: list[str] | None = None
    for line in shot_list.split("\n"):
        head = _SHOT_HEAD_RE.match(line)
        if head:
            current = []
            blocks.append((head, current))
        elif current is not None:
            current.append(line)

    shots = []
    for head, body in blocks:
        text = "\n".join(body)
        keys = _bullets(text)
        desc = next((m.group(1).strip() for m in
                     (_PLAIN_BULLET_RE.match(l) for l in body) if m), None)
        shots.append(Shot(
            index=int(head.group(1)),
            prompt=keys.get("prompt", ""),
            duration_s=float(head.group(2)) if head.group(2) else None,
            description=desc,
            on_screen_text=keys.get("on-screen text") or None,
            negative=keys.get("negative") or None,
            camera=(head.group(3) or "").strip() or None,
        ))
    return shots


def parse_recipe(md: str, file: str, platform: str) -> RenderPlan:
    """Parse an approved clone recipe. Raises RecipeError if it is not renderable."""
    why = extract_section(md, "Why this one")
    guide = _bullets(extract_section(md, "Regeneration guide"))
    formula = extract_section(md, "Replicable formula")

    title_m = re.search(r"^#\s+(?:Clone recipe\s*—\s*)?(.+)$", md, re.M)
    duration_m = re.search(r"\*\*duration:\*\*\s*([\d.]+)\s*s", why or "", re.I)
    source_m = re.search(r"\*\*source reel:\*\*\s*(\S+)", why or "", re.I)
    cid_m = re.search(r"-(\d{6,})\.md$", file)

    plan = RenderPlan(
        file=file,
        platform=platform,
        slug=slug_from_filename(file),
        title=(title_m.group(1).strip() if title_m else slug_from_filename(file)),
        shots=_parse_shots(extract_section(md, "Shot list")),
        target_duration_s=float(duration_m.group(1)) if duration_m else None,
        source_url=source_m.group(1) if source_m else None,
        content_id_prefix=cid_m.group(1) if cid_m else None,
        master_style_prompt=guide.get("master style prompt") or None,
        global_negative_prompt=guide.get("global negative prompt") or None,
        consistency_notes=guide.get("consistency") or None,
        replicable_formula=(
            "\n".join(formula.split("\n")[1:]).strip() if formula else None),
        audio_block=extract_section(md, "Audio"),
    )

    if not plan.shots:
        raise RecipeError(f"{file}: no shots found under '## Shot list' — nothing to render")
    bad = [s.index for s in plan.shots if _is_placeholder(s.prompt)]
    if bad:
        raise RecipeError(
            f"{file}: shot(s) {bad} have a missing or placeholder **Prompt:** — refusing to "
            "render. Re-run the producer against a schema-2 blueprint first.")
    return plan


# ---------------------------------------------------------------------------------------
# prompt composition (CLAUDE.md "Method" step 2/5)
# ---------------------------------------------------------------------------------------
def _split_csv(text: str | None) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,\n]", text) if p.strip()]


def compose_negative(plan: RenderPlan, shot: Shot) -> str:
    """Merge the global and per-shot negatives, case-insensitively deduped."""
    seen, out = set(), []
    for part in _split_csv(plan.global_negative_prompt) + _split_csv(shot.negative):
        k = part.lower().rstrip(".")
        if k not in seen:
            seen.add(k)
            out.append(part.rstrip("."))
    return ", ".join(out)


def compose_frame_prompt(plan: RenderPlan, shot: Shot, aspect_ratio: str = "9:16") -> str:
    """Build one frame's prompt: master style + shot prompt + consistency + negatives + text.

    Negatives are folded in as an AVOID clause rather than passed as a parameter because
    the active image provider (Gemini / Nano Banana) is a text-to-image model with no
    `negative_prompt` field — see engine/nanobanana.py.
    """
    parts = []
    if plan.master_style_prompt:
        parts.append(plan.master_style_prompt.strip())
    parts.append(shot.prompt.strip())
    if plan.consistency_notes:
        parts.append(f"CONSISTENCY: {plan.consistency_notes.strip()}")
    neg = compose_negative(plan, shot)
    if neg:
        parts.append(f"AVOID: {neg}.")
    if shot.on_screen_text:
        parts.append(
            "Burn this EXACT on-screen text into the image — large, legible, high contrast, "
            "inside the title-safe area, spelled exactly as written, with no extra words:\n"
            f'"{shot.on_screen_text.strip()}"')
    else:
        # Say this explicitly rather than merely omitting the burn-in instruction. Later
        # frames are generated with frame 0 attached as a consistency anchor, and the model
        # faithfully reproduces the anchor's text overlay unless told not to — which puts
        # the hook caption on shots the recipe wanted clean.
        parts.append(
            "This frame carries NO on-screen text. Do not render any words, captions, "
            "subtitles or watermarks; if the reference image contains a text overlay, omit "
            "it entirely here.")
    parts.append(f"Vertical {aspect_ratio} aspect ratio, photorealistic, high resolution.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------------------
# duration allocation
# ---------------------------------------------------------------------------------------
def allocate_durations(shots: list[Shot], target_s: float | None,
                       min_hold: float = 0.6, places: int = 3) -> list[float]:
    """Per-shot hold times that sum EXACTLY to the source clip's duration.

    Exactness matters: the operator attaches the original sound by hand in the Instagram
    composer, so if the render drifts from the source duration the beats stop landing on
    the cuts and the clone loses the thing that made the original work.

    Known durations are scaled to fit; unknowns split the remainder; rounding residue is
    folded into the last shot.
    """
    if not shots:
        return []
    n = len(shots)
    if not target_s or target_s <= 0:                       # nothing to fit — honour or default
        return [round(s.duration_s or min_hold, places) for s in shots]
    if target_s < n * min_hold:
        raise RecipeError(
            f"cannot fit {n} shots into {target_s}s at a {min_hold}s minimum hold — "
            "drop frames first")

    known = [s.duration_s for s in shots]
    known_sum = sum(d for d in known if d)
    unknown_n = sum(1 for d in known if not d)

    if unknown_n == n:                                       # none known: split evenly
        out = [target_s / n] * n
    elif unknown_n == 0:                                     # all known: scale to fit
        scale = target_s / known_sum if known_sum else 1.0
        out = [max(min_hold, d * scale) for d in known]
    else:                                                    # mixed: honour knowns, spread rest
        remainder = target_s - known_sum
        if remainder < unknown_n * min_hold:                 # knowns crowd out the unknowns
            scale = (target_s - unknown_n * min_hold) / known_sum if known_sum else 1.0
            out = [(d * scale) if d else min_hold for d in known]
        else:
            share = remainder / unknown_n
            out = [d if d else share for d in known]

    out = [round(d, places) for d in out]
    residue = round(target_s - sum(out), places)
    out[-1] = round(out[-1] + residue, places)
    return out


def cap_frames(shots: list[Shot], max_frames: int) -> list[Shot]:
    """Trim to the frame budget by dropping the SHORTEST INTERIOR shots.

    Never the first (the hook that earns the watch) or the last (the payoff). Callers are
    expected to log what was dropped — a silently shortened render reads as a faithful one.
    """
    if max_frames <= 0 or len(shots) <= max_frames:
        return list(shots)
    if max_frames <= 2:
        return [shots[0], shots[-1]][:max_frames]
    interior = sorted(range(1, len(shots) - 1),
                      key=lambda i: (shots[i].duration_s or 0.0))
    drop = set(interior[:len(shots) - max_frames])
    return [s for i, s in enumerate(shots) if i not in drop]
