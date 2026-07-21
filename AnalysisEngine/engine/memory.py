#!/usr/bin/env python3
"""engine/memory.py — the evolving markdown memory layer.

DEFECT FIX (companion "Defects to fix" → no memory / static prompt): the effective system
prompt is COMPOSED from markdown on every run, never hardcoded:

    system_prompt.base.md  (stable role + schema rules)
  + top lessons from patterns.md  (learned do/don't, auto-appended + deduped)
  + memory/<platform>/notes.md    (per-platform craft — IG != X != YT)

This is the "automatic system-prompt evaluation" contract (companion D1 step 2). After each
judge pass, `append_pattern()` distils a NEW generalizable lesson back into patterns.md so
future prompts auto-improve; `load_rubric()` feeds the same evolving rubric to the judge.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("ae.memory")

MEM_DIR = Path(__file__).resolve().parents[1] / "memory"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_base() -> str:
    return _read(MEM_DIR / "system_prompt.base.md")


def load_rubric() -> str:
    return _read(MEM_DIR / "rubric.md")


def _bullets(md: str) -> list[str]:
    """Extract markdown bullet lines (learned rules), ignoring headings/blank lines."""
    out = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            out.append(s[2:].strip())
    return out


def top_patterns(n: int = 12) -> list[str]:
    """The current top-ranked learned lessons from patterns.md (order = priority)."""
    return _bullets(_read(MEM_DIR / "patterns.md"))[:n]


def platform_notes(platform: str) -> str:
    return _read(MEM_DIR / platform / "notes.md")


def compose_system_prompt(platform: str, n_patterns: int = 12) -> str:
    """Assemble the effective system prompt for THIS run from evolving memory (never static)."""
    base = load_base().strip()
    patterns = top_patterns(n_patterns)
    notes = platform_notes(platform).strip()

    parts = [base]
    if patterns:
        lessons = "\n".join(f"- {p}" for p in patterns)
        parts.append(
            "## Learned lessons (distilled from past self-evaluations — apply them)\n" + lessons
        )
    if notes:
        parts.append(f"## Platform craft notes — {platform}\n{notes}")
    return "\n\n".join(parts)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def append_pattern(lesson: str) -> bool:
    """Append a distilled lesson to patterns.md, deduped (case/space-insensitive).

    Returns True if a NEW lesson was written, False if it was already present.

    APPENDS rather than rewriting the whole file. This used to read patterns.md, add one
    bullet, and write the lot back — a read-modify-write whose truncate-at-open() window
    covered the ENTIRE accumulated memory, not just the new line. A stop or a crash landing
    there did not lose one lesson, it lost every lesson ever distilled, and nothing would
    have reported it: `_read`'s `except FileNotFoundError: return ""` and the bullet parser
    both treat a ruined file as "no patterns yet", so the next run would have composed a
    system prompt with no learned rules at all and simply carried on. A pure append cannot
    damage what is already in the file, which is better than making the rewrite survivable."""
    lesson = lesson.strip().lstrip("-*").strip()
    if not lesson:
        return False
    path = MEM_DIR / "patterns.md"
    existing = _read(path)
    if _norm(lesson) in {_norm(b) for b in _bullets(existing)}:
        log.debug("pattern already known, skipping", extra={"lesson": lesson[:80]})
        return False
    with open(path, "a", encoding="utf-8") as f:
        if not existing:
            f.write("# Learned patterns (auto-appended, deduped)\n\n")
        elif not existing.endswith("\n"):
            f.write("\n")
        f.write(f"- {lesson}\n")
    log.info("appended learned pattern", extra={"lesson": lesson[:80]})
    return True


def append_platform_note(platform: str, note: str) -> bool:
    """Append a platform-specific craft note (deduped).

    A true append, for the same reason as `append_pattern`: the old read-modify-write put
    every craft note this platform had ever learned inside one truncate window."""
    note = note.strip().lstrip("-*").strip()
    if not note:
        return False
    path = MEM_DIR / platform / "notes.md"
    existing = _read(path)
    if _norm(note) in {_norm(b) for b in _bullets(existing)}:
        return False
    with open(path, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"- {note}\n")
    return True
