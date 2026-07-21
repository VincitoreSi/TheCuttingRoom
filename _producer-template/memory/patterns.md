# Learned patterns — <AGENT_NAME>

Do/don't rules this agent has learned from its own self-evaluations and (later) outcome feedback.
**Append over time; dedupe.** These are folded into the generation prompt each run, so the agent
improves without code changes. Keep them concrete and transferable-within-this-agent.

Format each rule as a dated bullet with a short rationale:

```
- 2026-07-19  DO: <rule>. — <why / which eval or outcome surfaced it>
- 2026-07-19  DON'T: <rule>. — <why>
```

> Cross-agent findings do NOT go here — post those to the shared exchange (`POST /api/insights`).

<!-- append learned rules below -->
