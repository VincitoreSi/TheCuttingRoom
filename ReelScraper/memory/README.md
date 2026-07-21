# Memory layer

Two tiers, by design:

## Per-platform (optimized separately — X ≠ Instagram ≠ YouTube)
`memory/<platform>/`
- `MEMORY.md`   — the index the agent auto-loads each run (keep it short)
- `patterns.md` — analytical learnings that predict virality on THIS platform
- `persona.md`  — this agent's evolving voice/taste
- `decisions.jsonl` — episodic idea/debate log (hypothesis → outcome), kept raw
- `content.db`  — searchable recall of this platform's scored content (built by core.memory)

Nothing platform-specific leaks across — each agent tunes its own.

## Shared (the ONLY cross-agent channel)
`memory/shared/`
- `METHOD.md`     — core idea / use-case / methodology every agent shares
- `INSIGHTS.md`   — rendered view of transferable findings + negative patterns
- `insights.jsonl`— structured log agents append to (kinds: method, finding, negative, idea)

Rule of thumb: only things that TRANSFER go in shared — the core method, a finding
that likely holds on other platforms, or a dead-end other agents should avoid.
