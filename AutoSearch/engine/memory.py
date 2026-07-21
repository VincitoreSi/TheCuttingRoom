#!/usr/bin/env python3
"""engine/memory.py — the evolving markdown memory layer (mirrors AnalysisEngine's
engine/memory.py, adapted to AutoSearch's directory layout, AutoSearch/PIPELINE.md §3).

The effective LLM system prompt is COMPOSED from markdown on every run, never hardcoded:

    system_prompt.base.md        (stable role + schema rules)
  + memory/<platform>/notes.md   (per-platform discovery craft — IG != X != YT)
  + memory/trending.md           (recent trending-term insight, auto-appended + deduped)

`append_platform_note()` / `append_trending()` let a run distil a lesson back into memory so
future prompts auto-improve, mirroring AnalysisEngine's `append_pattern()` convention.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("as.memory")

MEM_DIR = Path(__file__).resolve().parents[1] / "memory"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_base() -> str:
    return _read(MEM_DIR / "system_prompt.base.md")


def platform_notes(platform: str) -> str:
    return _read(MEM_DIR / platform / "notes.md")


def load_trending() -> str:
    return _read(MEM_DIR / "trending.md")


def compose_system_prompt(platform: str) -> str:
    """Assemble the effective system prompt for THIS run from evolving memory."""
    base = load_base().strip()
    notes = platform_notes(platform).strip()
    trending = load_trending().strip()

    parts = [base]
    if notes:
        parts.append(f"## Platform craft notes — {platform}\n{notes}")
    if trending:
        parts.append(
            "## Recent trending-term memory (may be stale — treat as a hint, not ground "
            "truth)\n" + trending
        )
    return "\n\n".join(parts)


def _bullets(md: str) -> list[str]:
    """Extract markdown bullet lines (learned rules), ignoring headings/blank lines."""
    out = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            out.append(s[2:].strip())
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def append_platform_note(platform: str, note: str) -> bool:
    """Append a platform-specific discovery craft note, deduped (case/space-insensitive)."""
    note = note.strip().lstrip("-*").strip()
    if not note:
        return False
    path = MEM_DIR / platform / "notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read(path)
    if _norm(note) in {_norm(b) for b in _bullets(existing)}:
        log.debug("platform note already known, skipping", extra={"note": note[:80]})
        return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"- {note}\n", encoding="utf-8")
    log.info("appended platform note", extra={"platform": platform, "note": note[:80]})
    return True


def append_trending(term: str) -> bool:
    """Append a trending-term observation to trending.md, deduped."""
    term = term.strip().lstrip("-*").strip()
    if not term:
        return False
    path = MEM_DIR / "trending.md"
    existing = _read(path)
    if _norm(term) in {_norm(b) for b in _bullets(existing)}:
        return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if not existing:
        existing = "# Trending terms memory (auto-appended, deduped)\n\n"
    path.write_text(existing + f"- {term}\n", encoding="utf-8")
    log.info("appended trending term", extra={"term": term[:80]})
    return True
