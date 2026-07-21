# AnalysisEngine memory index

The effective system prompt is **composed fresh every run** from the files below (see
`engine/memory.py` → `compose_system_prompt()`) — it is never static. This is the
"automatic system-prompt evaluation" contract: memory evolves, so the prompt improves.

| File | Role |
| --- | --- |
| [`system_prompt.base.md`](system_prompt.base.md) | Stable base instruction: director/cinematographer/prompt-engineer role + the schema_version 2 output contract + hard rules. |
| [`patterns.md`](patterns.md) | Learned do/don't lessons, auto-appended and deduped after each self-eval. The top N are injected into every run. |
| [`rubric.md`](rubric.md) | The rubric the self-eval judge scores against (hard-fails + scored criteria, incl. the D3b audio additions). |
| [`instagram/notes.md`](instagram/notes.md) | Per-platform craft notes for Instagram Reels. |
| [`x/notes.md`](x/notes.md) | Per-platform craft notes for X. |
| [`youtube/notes.md`](youtube/notes.md) | Per-platform craft notes for YouTube Shorts. |

**Composition order:** `system_prompt.base.md` + top `patterns.md` lessons +
`memory/<platform>/notes.md`. Platforms are kept separate because IG ≠ X ≠ YT.

**Write-back:** after the judge runs, a remaining gap is distilled into a preventive rule via
`append_pattern()`, and one transferable finding per run is posted to the hub shared exchange
(`POST /api/insights`). The agent's identity/rules live in the repo-root `CLAUDE.md` (there is
exactly one CLAUDE.md, not one here).
