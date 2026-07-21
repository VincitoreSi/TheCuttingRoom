# Virality pipeline — backend agent (instructions for Claude Code)

This repo is the **backend agent** of a larger content-virality pipeline. It scrapes
handpicked creator pages per platform (Instagram Reels, X, YouTube Shorts), scores every
post on four virality signals, remembers what works, and serves everything from a **local
API hub** (`python cli.py start` → `http://127.0.0.1:8787`). The **frontend** (a React
control board) and the **producer agents** (content generators) are SEPARATE Claude Code
agents that connect to this hub over HTTP — their creation prompts live in `AGENT_PROMPTS.md`,
the team + roadmap in `ROADMAP.md`. Do NOT rebuild the engine — it works.

## Run the app
```
uv sync                                        # one-time: create .venv + install deps (uv-managed)
uv run cli.py start                            # API hub + web board on localhost (the product)
uv run cli.py scrape|analyze|media <platform>
```
Env is managed by **uv** (`pyproject.toml` + `uv.lock`). Prefix every command with `uv run`
(it uses the project's `.venv`). Per-invocation logs are written to `logs/<start_time>_<command>.log`
(pretty console + JSONL file; see `core/logsetup.py`).
The hub (`api/app.py`, FastAPI, docs at `/docs`) exposes: `/api/platforms`, `/api/config`
(edit weights/tiers/keywords/pages in one place), `/api/content` (board rows + media urls),
`/api/corpus/*` (factors/top/brief/search), `/api/studio` (proposals + human gate), `/api/analysis/*`
(video craft / **schema-2 blueprints**, written by the AnalysisEngine/Gemini agent), `/api/insights`,
`/api/pipeline/*` (+ `/api/events` SSE), and `/media` (locally-persisted video for inline play).
All request bodies are typed Pydantic models, so `/docs` is a real, self-documenting contract.

**The finalized pipeline contract (added for The Cutting Room rollout).** `content_id`
is the universal content join key; **`audio_id` is the sound join key** (parallel to it). New
surfaces — all backward-compatible; existing routes + lean analyses are untouched:
- **Analysis schema v2** — `POST /api/analysis/{p}` also accepts a rich, generation-ready
  **blueprint** (`schema_version:2`: `video_metadata, global_style, audio, audio_strategy,
  characters_and_subjects[], text_overlays[], shots[]` (per-shot `generation_prompt`/
  `negative_prompt`), `regeneration_guide, virality_formula, evaluation`). Old lean docs
  (no `schema_version`) still validate. `GET /api/analysis/{p}/pending` gains filters
  (`min_score, tier, min_duration, max_duration, content_type, limit, reanalyze=<id>, stale=true`);
  default behavior unchanged. `brief` reads `virality_formula` (falls back to lean fields).
- **Audio intelligence** — `GET /api/audio/{p}/trending` (+ `?window=14d&limit=&reusable_only=&mood=&min_trend=`)
  and `GET /api/audio/{p}/sound/{audio_id}`. Trend = adoption velocity within tracked creators
  (NOT the platform-wide chart — MVP). `normalize.py` extracts the `audio_*` fields; `core/audio.py`
  computes `sound_trend_score` + `bucket` (Rising|Hot|Saturated|Evergreen), mirroring `core/virality.py`.
- **Producer registry** — `POST /api/producers/register` (idempotent upsert by `name`) + `GET /api/producers`;
  persisted `producers/registry.json`. Manifest: `{name, kind, consumes[], human_gate, needs_reference,
  produces, output_status, config_schema, secrets, workflow_stages}` — `workflow_stages` is the ordered
  lane list for that agent's live task board (e.g. `["Queued","Analyzing","Self-eval","Done"]` for
  analyzers, `["Queued","Generating","Self-eval","Proposed","Approved","Rejected"]` for producers;
  `Failed` is an implicit terminal lane shown only when occupied).
- **Agent workflow board** — `GET /api/agents/{name}/board?platform=&limit_runs=` reduces `logs/agents.jsonl`
  into `runs -> items -> current stage` for one agent, left-joining studio gate status for producer kinds.
  Beyond the coarse `run.start`/`item.done`/`run.end` events, agents may POST fine-grained per-item
  lifecycle events to `POST /api/logs`: `item.start` (item begins, `data.stage`), `item.stage` (mid-item
  transition, `data.stage`), `item.error` (failed → implicit `Failed` lane). `data.stage` must be one of
  the agent's declared `workflow_stages`; `item.done` carries the terminal stage and, for producers,
  `data.file` so the board can join the human-gate decision.
- **Render store (producer-generated media)** — `POST /api/renders/{p}` takes a `RenderIn`
  (`file` = the studio filename, plus `assets[]` as **base64**, not multipart — the hub has no
  `python-multipart` dependency). `render_id` is derived server-side from the studio filename, so
  one item maps to one directory and re-rendering overwrites in place. `GET /api/renders/{p}
  [?file=&agent=&kind=]`, `GET|DELETE /api/renders/{p}/{render_id}`; rows are hydrated with
  `video_url`/`poster_url` (cache-busted by `?v=<updated_at ms>`) and `local_path` for manual
  upload. Records also carry `aspect_ratio` (`9:16` reels by default) + `video_fit`, so the
  Dashboard sizes its inline player from what the file actually is rather than assuming.
  Mounted at `/renders` (range-capable) BEFORE the `/` catch-all. `index.json` is a
  derived cache rebuilt at startup from the per-item `render.json`.
- **Per-item render trigger** — `POST /api/studio/{p}/{file}/render` 409s unless the item is
  `approved`, then launches the producer that wrote it, resolved from that producer's registered
  manifest (`renderable: true`, `dir`, `render_cmd` — validated to be a direct sibling dir). The
  job key is deterministic (`{platform}:render:{file}`) so it doubles as a per-item lock and as
  the Dashboard's SSE lookup key. **`render` is deliberately excluded from `RUN_ALL_STAGES`** —
  it spends image-API credits and only ever runs when a human asks.
- **Human gate** — studio items carry `status ∈ {draft, proposed, approved, rejected}` + `agent`/`kind`.
  `POST /api/studio/{p}` **preserves an existing item's status** when the body omits one, so a
  producer re-posting its own markdown cannot silently un-approve it (this bug once destroyed
  five real approvals).
  `POST /api/studio/{p}` accepts them (default `status:"proposed"`); `POST /api/studio/{p}/{file}/status`
  `{status, note}` records a decision (append `studio/{p}/gate.jsonl`); `GET /api/studio/{p}?status=&agent=`
  filters. Sidecar metadata lives in `studio/{p}/meta.json`.
- **Reference ingestion** (only consumer: the template agent) — `POST /api/reference/{p}` `{url}` downloads
  media (safe: yt-dlp if present, else direct GET — never a login/cookie), assigns `ref_<hash>`, marks pending;
  `GET /api/reference/{p}[/pending]`. AnalysisEngine analyzes references and saves them with `is_reference:true`
  to the SAME `analysis/{p}/<ref_id>.json` layout, served at `GET /api/analysis/{p}/{ref_id}`.
- **Platform-wide concerns (§10)** — Logs: `POST /api/logs` (append `logs/agents.jsonl`),
  `GET /api/logs?agent=&level=&since=&run_id=`, streamed on the SSE `log` channel of `/api/events`.
  Evals: `POST /api/evals`, `GET /api/evals?agent=&target_type=&since=` (store `evals/<agent>/…` + `evals.jsonl`).
  Config: `GET/PUT /api/config/agent/{agent}` (store `config/agents/{agent}.json`, defaults from the manifest
  `config_schema`). Secrets: `GET /api/config/agent/{agent}/secrets/status` → `[{name, env_var, present, required}]`
  — **status only, NEVER values; the hub never stores a secret** (declared by env-var NAME only).
- **Discovery contract (§11.2, the AutoSearch agent's front door)** — `auto-search` (kind `discovery`) finds
  new creators and posts them here for human approval into `pages.txt`; it never writes into this repo
  directly. `POST /api/discovery/{p}` ingests/upserts one candidate (`CandidateIn`: handle, source_term,
  discovered_via, followers, median_plays, sample_reels[], relevance{score,reasons[]}) — `candidate_id` is
  agent-supplied or a stable `cand_<sha1(platform:handle)>` hash so re-ingestion upserts, never dupes;
  `status` is forced to `"pending"` on first insert and is **never** silently un-gated back to pending on a
  later re-ingest of an already-approved/rejected candidate. `GET /api/discovery/{p}[?status=]` lists rows
  newest-first with a derived `in_pages` flag (is the handle already a non-comment line in `pages.txt`?);
  `GET /api/discovery/{p}/pending` is the human review queue. `POST /api/discovery/{p}/{candidate_id}/status`
  `{status, note}` is the gate — on `approved` it calls the new safe, comment-preserving, deduped
  `_append_handle_to_pages` (append-mode, never `put_config`'s whole-file overwrite) and records the outcome
  (incl. `appended_to_pages`) to `discovery/{p}/gate.jsonl`. `agent_board` left-joins that gate log for
  `kind=="discovery"` producers, keyed on `content_id == candidate_id` (parallel to the studio gate-join, but
  keyed by content_id instead of filename since candidates have no `file`). Stage runners `"auto-search"`
  (manual/exhaustive pass) and `"auto-search-beat"` (bounded heartbeat tick) shell out to the sibling
  `../AutoSearch` uv project via the same `STAGE_CMD`/`_run_job`/SSE machinery as every other stage. A
  background daemon thread (started at hub startup, never blocking it) fires `auto-search-beat` for every
  platform every `heartbeat_minutes` ± jitter, but **only** while the per-agent config flag
  `discovery_enabled` (`config/agents/auto-search.json`, default **false**) is true — the kill-switch. The
  scheduler fails closed (treats any config-read problem as disabled) and is idle by default. `discover.py` /
  `find_profiles.py` in `platforms/instagram/` are superseded by `/AutoSearch` for ongoing, hub-gated
  discovery — they remain for manual/offline use.

## Layout
```
core/                 shared, platform-agnostic — written once, reused everywhere
  schema.py           the normalized Content record every platform emits
  virality.py         the 4-signal engine + Excel/CSV reports
  memory.py           ContentMemory (per-platform recall) + SharedInsights (cross-agent)
  runner.py           the shared run.py CLI (analyze | search | insight | insights)
platforms/<p>/        thin per-platform adapter
  scrape.py           platform scraper  (all three built: instagram guest; x session; youtube key-free)
  normalize.py        raw -> core.schema.Content  (the ONLY platform-specific mapping)
  run.py              3-line wrapper -> core.runner
  niche_config.json   this platform's niche + virality weights/tiers (tuned per platform)
  pages.txt           handpicked handles/links
memory/<p>/           per-platform memory (optimized separately)
  MEMORY.md patterns.md persona.md decisions.jsonl content.db
memory/shared/        the ONLY cross-agent channel
  METHOD.md           core idea/use-case shared by all agents
  INSIGHTS.md         rendered transferable findings + negative patterns
  insights.jsonl      structured log agents append to
core/logsetup.py      shared production logging (pretty console + per-session JSONL)
logs/                 per-invocation logs, named by start_time (git-ignored)
api/app.py            the local API hub (REST + SSE + media + serves the frontend build)
cli.py                single entry point: start (open web app) | scrape | analyze | media
download_media.py     persist top videos to media/ so the board plays them inline
studio/<p>/           generated proposals (external producers) + meta.json (status/agent/kind) + gate.jsonl
renders/<p>/<id>/     producer-GENERATED media uploaded via POST /api/renders (render.json + reel.mp4
                      + poster.jpg), served at /renders — kept strictly apart from media/ (see SAFETY)
analysis/<p>/         video breakdowns / schema-2 blueprints (written by AnalysisEngine); ref_<hash>.json = references
producers/            registry.json — producer manifests (self-registered via POST /api/producers/register)
references/<p>/       registry.json — ad-hoc reference/template videos (ref_<hash>), media in media/<p>/ref_*.mp4
discovery/<p>/        candidates.json (creator candidates from auto-search) + gate.jsonl (approve/reject log)
evals/                <agent>/<id>.json + evals.jsonl — self-eval / judge results (§10.2)
config/agents/        <agent>.json — per-agent config (Dashboard-editable; defaults from manifest config_schema)
logs/agents.jsonl     central curated LIFECYCLE log agents POST to (streamed on SSE `log` channel)
.claude/agents/       ig-analyst + x-analyst + yt-analyst (this backend's own scraper agents)
.venv/                uv-managed virtualenv — run everything with `uv run`
```

## Run it (per platform, from the platform folder)
```
cd platforms/instagram
uv run scrape.py --file pages.txt      # scrape (Instagram: guest-safe)
uv run run.py analyze                  # score + write xlsx + index memory
uv run run.py search "linen hook"      # recall past content
uv run run.py insight negative "..." --tags antipattern   # log to shared exchange
```
All three platforms share the same `run.py` CLI. The scrapers differ by access model
(`normalize.py` documents each platform's expected raw shape + metric mapping):
- **instagram** — guest mode (no login), `--file pages.txt`.
- **x** — needs a burner session: `X_AUTH_TOKEN` + `X_CT0` env (or `platforms/x/session.txt`).
  Internal GraphQL; if it starts 400/404-ing, refresh `QID_USER`/`QID_TWEETS` in `scrape.py`.
- **youtube** — key-free (InnerTube); no credentials. `@handle`/`UC…`/URL in `pages.txt`.

## Memory model (important)
- **Per-platform memory is separate and tuned** — X virality ≠ Instagram ≠ YouTube, and
  their scrape data differs. Each agent keeps its own `patterns.md`/`persona.md`/`content.db`.
- **Only transferable knowledge is shared** via `memory/shared/` — the core method, a
  finding that likely holds elsewhere, or a negative pattern to warn other agents. Agents
  READ the shared exchange at the start of a run and APPEND to it at the end.
- Recall is SQLite FTS5 today (zero deps); a semantic upgrade (sqlite-vec + local embedder)
  slots behind the same `ContentMemory.search()` API without changing callers.

## The four virality signals (shared across platforms)
- `engagement_rate` = (likes+comments+shares+saves) / followers
- `reach_multiplier` = plays / followers  (travel past the audience)
- `outlier_score` = plays / creator's median plays  (breakout vs their norm)
- `velocity` = plays / days since posting
Percentile-normalized across the dataset, blended by each platform's config weights into
`virality_score` (0-100) + tier. Reach/outlier surface small-account breakouts.

## Producer + analysis agents (external — consume the corpus, never scrape)
These are **separate** Claude Code agents (their own directories) that talk to the hub over
HTTP. Create them from the prompts in `AGENT_PROMPTS.md`:
- `video-analysis` — uses **Gemini** to watch the top clips' local videos **frame-by-frame**
  and write shot-by-shot craft breakdowns (hook, beats, visual style, audio, retention,
  `replicable_formula`) to `POST /api/analysis/<p>`. Reads `GET /api/analysis/<p>/pending`
  (top-viral clips with media downloaded but not yet analyzed). Bridges *metrics* → *craft*.
- `similar-content` — fresh ideas modeled on the top viral clusters.
- `proposal-content` — 5 full script proposals → adversarial debate → human gate (pick).
- `auto-content` — one highest-conviction concept, full script, every choice factor-mapped.

Producers read `GET /api/corpus/<p>/{factors,brief,top,search}` **and** the new
`GET /api/analysis/<p>[/{id}]` (video craft); they write `POST /api/studio/<p>` +
`POST /api/insights`. The read adapter is `core.corpus.Corpus` — its `brief()` now embeds a
**"Visual formulas"** section from the frame-by-frame analyses, so generators get the on-screen
mechanics, not just captions + metrics. Outputs land in `studio/<platform>/`, closing the
learn→analyze→produce→measure loop.

## Adding a platform
1. `platforms/<p>/`: write `scrape.py` (emit `<content>_raw.json` + `profiles_meta.json`),
   `normalize.py` (map raw → `core.schema.Content`), copy `run.py`, add `niche_config.json`.
2. `memory/<p>/` scaffolding + a `<p>-analyst.md` agent.
That's it — the engine, memory, and reports are inherited from `core/`.

## SAFETY (do not weaken)
- **Generated media NEVER enters the corpus namespace.** `media/<platform>/` holds scraped media
  keyed by `content_id`; `renders/<platform>/<render_id>/` holds producer output. They are
  separate directories behind separate mounts, and `save_render` refuses any asset name shaped
  like a `content_id`. This is not fastidiousness: five real reels were once overwritten with
  generated ones, so `/api/content` served our own videos under real creators' ids with
  `duration_s` that no longer matched. `media/` is gitignored, so nothing about it shows up in
  review — the only defence is that the write path does not exist.
- **Instagram** scraping/hydration is **guest mode only** (no `sessionid` → no ban risk).
  Never add a login cookie to the Instagram scraper. Discovery (`discover.py`) is opt-in and
  the only place a burner `session.txt` is used.
- **X** has no free guest path (removed 2023), so it **requires a logged-in session** —
  supplied as `X_AUTH_TOKEN` + `X_CT0` (env or git-ignored `platforms/x/session.txt`), never
  committed, never logged. **Burner account only**; the scraper paces slower than IG and trips
  a 3-strike circuit breaker, but a real account can still be limited/suspended. This is X-only
  and does NOT relax the Instagram guest-only rule.
- **YouTube** is key-free via YouTube's own InnerTube API — no login, no personal API key.
- Respect rate-limit **circuit breakers** (stop after 3 consecutive rate-limits). Don't hammer
  or slash delays. Resume is automatic (creators already in `*_raw*.json` are skipped).
- New platform scrapers must follow the same rules: prefer official/public access, pace
  requests, add a circuit breaker.

## Notes
- Requires only `openpyxl` + `fastapi`/`uvicorn` (stdlib `sqlite3` powers memory). Deps are
  uv-managed — `uv sync` installs them from `pyproject.toml`/`uv.lock`; run with `uv run`.
- CDN media links expire in hours; metrics/captions are permanent. Offer a downloader if
  the user wants the actual media kept.
- Instagram internals: `X-IG-App-ID` = `936619743392459`; reels via `POST /api/v1/clips/user/`;
  profile via `GET /api/v1/users/web_profile_info/` (guest).
