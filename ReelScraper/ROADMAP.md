# ViralityLab — team + backend roadmap

End goal: a **modern, animated React "pipeline board"** — one place to configure everything,
run the whole pipeline, watch it live, and review/play generated content inline. This repo
(`viralitylab`) is the **backend brain** every agent and the frontend connect to.

## The agent team
| Agent | Where | Role | Talks to |
|---|---|---|---|
| **viralitylab** (this) | this repo | scrape · score · memory · **corpus API + media + hub** | serves everyone |
| ig-analyst | in-repo | Instagram analyst (built) | hub |
| x-analyst / yt-analyst | in-repo (stubs) | X / YouTube analysts | hub |
| similar-content | own dir | "more of what works" ideas | hub (read corpus, write studio) |
| proposal-content | own dir | 5 scripts → debate → human gate | hub |
| auto-content | own dir | one highest-conviction bet | hub |
| **frontend** | own dir | the React pipeline board (end goal) | hub (REST + SSE) |

Connective tissue = **the hub** (this repo). Agents read/write through it; the frontend
renders it. Standard stack: **MCP** for agents, **REST+SSE** for the frontend (AG-UI later).

## Delivery model — a local CLI that opens the web app
The whole product ships as ONE command. `viralitylab start`:
1. starts the API hub (uvicorn) on `127.0.0.1` — preferring port 8787, falling back to a free
   one when it is busy, and printing the real address as `HUB_URL=…`,
2. serves the built React app (static `frontend/dist`) from the hub (or runs the dev server),
3. opens that URL in the browser.
Everything is localhost — no cloud, no accounts. `cli.py` (console entry point
`viralitylab`) orchestrates: `start` (default), `scrape`, `analyze`, `generate`.
The frontend is a static build the hub serves, so one process = whole app.

## What THIS agent must add (backend, phased)

### Phase 1 — API hub (`api/` FastAPI over `core/`)  ← keystone
One local service, auto-documented at `/docs` (OpenAPI at `/openapi.json`):
- `GET/PUT /config?platform=` — niche_config (weights/tiers/keywords) + pages.txt in ONE place
- `GET /corpus/top|factors|brief|search?platform=&q=` — the Corpus adapter
- `GET /content?platform=` — scored rows for the board (from virality_reels.csv)
- `GET /studio | POST /studio` — proposals; `GET/POST /insights` — shared exchange
- `GET /platforms` — status + counts per platform

### Phase 2 — pipeline control + live status (the "board")
- `POST /pipeline/{scrape|analyze|discover|generate}?platform=` — trigger a stage (async job)
- `GET /pipeline/status` — per-stage state (idle/running/done/error, counts, timings)
- `GET /events` — **SSE stream** of stage transitions so the board animates in real time

### Phase 3 — media (so the board plays video inline)
CDN links expire in hours, so the backend must persist media:
- `download_media.py` — after a scrape, pull `video_url_best`/`thumbnail_best` →
  `media/<platform>/<content_id>.mp4` / `.jpg`
- `GET /media/{platform}/{content_id}.mp4|jpg` — serve them (range requests for inline play)
- `content` rows carry `media_url = /media/...` so the frontend just plays it

### Phase 4 — producer + agent bridge
- `POST /producers/{similar|proposal|auto}/run?platform=&topic=` — kick a producer, stream
  its output to the board; proposals land in `studio/` and are served by `/studio`
- Optional **MCP server** exposing the same corpus/config/studio tools for Claude Code agents

## Data contracts (what the frontend consumes)
- **content**: `{platform, creator, url, media_url, thumb_url, plays, virality_score, tier,
  reach_multiplier, outlier_score, engagement_rate, velocity, duration_s, caption, posted}`
- **factors**: `{baseline, winners[], losers[]}` (each `{feature, bucket, n, mean_score, lift}`)
- **proposal**: `{id, platform, agent, title, script, spec, factor_map, predicted_tier, video_url?}`
- **pipeline event**: `{stage, platform, status, count, ts}`

## Build order
Phase 1 unblocks the frontend + producers immediately (config + corpus). Phase 3 unblocks
inline video. Phase 2 makes the board feel alive. Phase 4 wires producers into the board.
