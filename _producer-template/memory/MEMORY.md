# Memory index — <AGENT_NAME>

This agent's memory layer (separate from every other producer so its voice stays distinct).
Read these at the start of a run; append to them at the end.

- **[persona.md](./persona.md)** — this agent's voice / point of view / stylistic constraints.
- **[patterns.md](./patterns.md)** — learned what-works rules, distilled from past self-evals and
  outcomes. Appended over time, deduped. These fold into the generation prompt each run.

Shared, cross-agent knowledge does NOT live here — it lives in the hub's shared exchange
(`GET /api/insights` to read, `POST /api/insights` to append one transferable finding per run).
Keep this `memory/` folder for THIS agent's private voice + private lessons only.
