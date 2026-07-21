# Virality pipeline — backend + local app

Analyze what makes short-form content go viral, then generate more of it. This repo is the
**backend agent** of a larger pipeline: it scrapes handpicked creator pages (per platform),
scores every post on four virality signals, remembers what works, and serves it all from a
**local API hub** that a React control board (and other agents) run on.

Everything is **local** — no cloud. Instagram is guest-mode (no login) and YouTube is key-free;
X needs one burner-account session. The environment is managed by **uv**.

## Quick start
```
uv sync                                  # create .venv + install deps (one-time)
# scrape + score a platform
uv run cli.py scrape  instagram          # uses platforms/instagram/pages.txt
uv run cli.py analyze instagram          # -> Virality_Analysis.xlsx + content.json + memory
uv run cli.py media   instagram          # download top videos for inline playback
# launch the app (API hub + web board) on localhost
uv run cli.py start                      # opens http://127.0.0.1:8787
```
`cli.py start` is the whole product: it boots the API hub and serves the web board (once the
frontend is built into `frontend/dist`). API docs at `/docs`. Every command writes a
per-session log to `logs/<start_time>_<command>.log` (pretty console + JSONL file).

### Platform credentials
- **instagram** — none (guest mode).
- **x** — a burner account's cookies: `export X_AUTH_TOKEN=… X_CT0=…` (or put them in
  `platforms/x/session.txt`). Use a throwaway account; see `platforms/x/scrape.py` header.
- **youtube** — none (key-free InnerTube).

## What's here
- `core/` — shared engine: `schema` (normalized record), `virality` (4-signal scoring),
  `memory` (per-platform recall + shared insights), `corpus` (read adapter), `runner` (CLI).
- `platforms/<p>/` — per-platform scraper + config (all three built: instagram/x/youtube).
- `api/app.py` — the local API hub (REST + SSE + media + serves the frontend).
- `cli.py` — the single entry point (`start`, `scrape`, `analyze`, `media`).
- `memory/` — per-platform memory + the shared cross-agent insights exchange.
- `studio/` — generated content proposals (written by the producer agents via the hub).

## The rest of the pipeline
Other agents (producer agents that generate content; the React frontend) are **separate**
Claude Code agents that connect to this hub over HTTP. Copy-paste prompts to create them are
in **`AGENT_PROMPTS.md`**; the team + backend plan is in **`ROADMAP.md`**.

## The four virality signals
`engagement_rate`, `reach_multiplier` (plays/followers), `outlier_score` (plays vs the
creator's median), `velocity` (plays/day) — percentile-normalized and blended per platform's
`niche_config.json` into a 0–100 score + tier. Reach/outlier surface small-account breakouts.

See `CLAUDE.md` for the full architecture and safety rules.
