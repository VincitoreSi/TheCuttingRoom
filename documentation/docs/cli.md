---
title: CLI Reference
---

# CLI Reference

There are two layers. At the repo root, three shell scripts — `./init`,
`./demo`, `./docsite` — are the everyday entry points: they check prerequisites,
install, and launch. Underneath, each agent is an independent
[uv](https://docs.astral.sh/uv/)-managed project with its own `cli.py`, and
they only ever talk to each other over HTTP through
[the hub](architecture.md).

This page documents the root scripts, all four agent CLIs, and the stage
dispatch that lets the hub (and the Dashboard) launch any of them as a
background job.

!!! note "Reading this page"
    Every command below assumes you've run `uv sync` once inside the
    relevant repo, and that you run commands with `uv run` so they execute
    inside that project's `.venv`. See [Architecture](architecture.md) for
    how the repos relate, and [API Reference](api-reference.md) for the HTTP contract
    these CLIs talk to.

## Root scripts — `./init`, `./demo`, `./docsite`

Run from the repo root. Each one checks its prerequisites, installs what is
missing, and then launches. Shared helpers (colour output, `require`,
`free_port`, `start_hub`, `open_browser`) live in `scripts/_common.sh`.

| Script | Usage | Flags | What it does |
|---|---|---|---|
| `./init` | `./init` | `--no-launch`, `--reset`, `--port N` | First-run setup from a clean clone: checks `uv`/Python/Node/npm/curl (and warns about missing `ffmpeg`), syncs every Python project, builds the Dashboard, prompts for `GEMINI_API_KEY` and writes it to `AnalysisEngine/.env` + `SimilarContent/.env`, then starts the hub and opens a browser. You land on an **empty** dashboard — no corpus, no proposals, no renders. `--reset` clears the **stored API keys** (`*/.env`, `platforms/x/session.txt`) so you can re-enter them — your scraped data is kept. To delete the data too, use `./clean`, which archives it first. |
| `./stop` | `./stop` | `--list` | Stops the hub and any stage jobs this checkout started. Matches processes by **working directory**, not command line, so several clones of this repo on one machine never stop each other's hub — and it says so if the usual port is held by a different checkout. `--list` shows what it would stop. Data is untouched. |
| `./clean` | `./clean` | `--yes`, `--keep-keys`, `--list` | Wipes back to a fresh clone: stops everything, **archives every scrap of generated data to `backups/cuttingroom-data-<timestamp>.zip`**, verifies that archive, prints its path, asks, and only then deletes — data *and* stored API keys. If the archive cannot be written or is corrupt, nothing is deleted. Keys are deliberately **not** archived (live credentials do not belong in a zip); `--keep-keys` leaves them in place. Anything git tracks is never removed, and agent-written `memory/*/patterns.md` is restored to the shipped version. |
| `./demo` | `./demo` | `--keep`, `--port N` | Unpacks `demodataset.zip` (shipped separately, placed in the repo root) into `demo-data/data/`, copies it over the working tree and launches, so every view has content. Stops with instructions if no dataset and no zip are present. **No API keys, no scraping, no model calls.** Overwrites generated data (corpus, studio, renders, evals, logs); never touches source or `.env` files. `--keep` launches without overwriting what is already on disk. |
| `./docsite` | `./docsite` | `--build`, `--port N` | Builds this documentation site into `documentation/site` and serves it with live reload. mkdocs is not a standalone install — it lives in ReelScraper's `dev` dependency group, so the script runs `uv run --project ReelScraper mkdocs`. `--build` builds only. |

!!! tip "`./init` or `./demo`?"
    `./demo` is the one to run first if you just want to look around — it is
    instant and offline. `./init` is the real first-run path for someone who
    will scrape their own niche. See
    [Entry Points & Demo Data](entry-points.md) for the full picture,
    including what `demo-data/` contains and its privacy caveat.

!!! note "The port is not fixed"
    All three scripts prefer a default port (8787 for the hub, 8000 for the
    docs) and **fall back to a free one** when it is busy, printing the port
    they actually got. Nothing in the system hardcodes 8787 — see
    [Ports](#ports-nothing-is-hardcoded) below.

---

## `ReelScraper/cli.py` — the hub

Run from inside `ReelScraper/`. This is the entry point for the product as a
whole: it boots the FastAPI hub, serves the Dashboard, and runs individual
pipeline stages on demand.

| Command | Usage | Args / flags | What it does | Example |
|---|---|---|---|---|
| `start` | `uv run cli.py start [--port N] [--host H] [--strict-port] [--no-browser]` | `--port` (default 8787, or `$HUB_PORT`); `--host` (default `127.0.0.1`, or `$HUB_HOST`); `--strict-port` fails instead of falling back; `--no-browser` skips opening one | Boots the API hub (Uvicorn) and opens the web board. This is "the product" — hub + Dashboard together, since the hub mounts the built Dashboard (`frontend/dist`) at `/`. If the preferred port is busy it binds an OS-assigned free one instead of dying. It prints `HUB_URL=<url>` on stdout and exports `BACKEND_API` so every stage it spawns inherits the real address. | `uv run cli.py start --port 9123 --no-browser` |
| `scrape` | `uv run cli.py scrape <platform>` | `<platform>` — one of `instagram`, `x`, `youtube` | Runs the scrape stage manually: shells to `platforms/<platform>/scrape.py --file pages.txt`, which bootstraps a guest session, paginates each creator's posts, and writes normalized `content.json` rows locally (no hub POST from inside `scrape.py` itself). | `uv run cli.py scrape instagram` |
| `analyze` | `uv run cli.py analyze <platform>` | `<platform>` | Runs the scoring stage: shells to `platforms/<platform>/run.py analyze`, which runs the 4-signal virality engine (`core/virality.py`) over scraped content and indexes it into local memory (SQLite FTS5) plus xlsx/CSV reports. | `uv run cli.py analyze instagram` |
| `media` | `uv run cli.py media <platform>` | `<platform>` | Runs the media-download stage: shells to `download_media.py <platform>` (cwd = repo root), which persists the top-viral clips' videos and thumbnails to `media/<platform>/<content_id>.{mp4,jpg}` so the board can play them and AnalysisEngine can watch them. | `uv run cli.py media instagram` |

!!! note "Naming: this stage is called “Media” on the pipeline board, not “Analyze”"
    `cli.py analyze` runs the **scoring** stage (stage 4, virality scoring).
    The **Blueprint** stage (stage 6, Gemini frame-by-frame analysis) is a
    separate agent, AnalysisEngine — see below. The pipeline board deliberately
    labels stage 6 "Blueprint" rather than "Analyze" to avoid this exact
    confusion. See [Pipeline](architecture.md) for the full stage list.

### Per-platform manual commands

Independently of `cli.py`, each platform adapter under `platforms/<p>/` also
exposes its own scripts for manual/one-off use:

| Command | What it does | Example |
|---|---|---|
| `uv run scrape.py --file pages.txt` | Scrapes the handles listed in `pages.txt` directly (same script `cli.py scrape` shells to). | `uv run scrape.py --file pages.txt` |
| `uv run run.py analyze` | Runs virality scoring directly (same script `cli.py analyze` shells to). | `uv run run.py analyze` |
| `uv run run.py search "<query>"` | Searches the platform's local corpus/memory. | `uv run run.py search "morning routine"` |
| `uv run run.py insight negative "..." --tags antipattern` | Appends a manual insight of a given kind, tagged, to shared insights. | `uv run run.py insight negative "hook too slow" --tags antipattern` |

### Stage dispatch via the API (not a CLI, but CLI-adjacent)

The hub also exposes every stage — including the two below that live in
sibling repos — as a subprocess job launched over HTTP, used by the Dashboard's
"run stage" buttons:

```
POST /api/pipeline/{platform}/{stage}
```

`stage` ∈ `scrape`, `analyze`, `media`, `analysis-engine`, `propose`, `auto-search`,
`auto-search-beat`, `render`. For the first three, the hub shells into the
commands in the table above. For the rest, it shells into the **sibling repo's
own CLI**:

| Stage | Shells to |
|---|---|
| `analysis-engine` | `uv run cli.py run <platform>` inside `../AnalysisEngine` |
| `auto-search` | `uv run cli.py run <platform>` inside `../AutoSearch` |
| `auto-search-beat` | `uv run cli.py beat <platform>` inside `../AutoSearch` |
| `render` | the **registered producer's** own `render_cmd`, inside its declared `dir` — the hub hardcodes no producer path. For SimilarContent that resolves to `uv run cli.py render` inside `../SimilarContent`. |

`POST /api/pipeline/{platform}/run-all` runs only the core four in dependency
order — `scrape → analyze → media → analysis-engine` — halting on the first
non-zero exit. Discovery is excluded (opt-in, human-gated) and so is `render`
(paid, per-item, human-triggered). Rendering is normally driven through
`POST /api/studio/{p}/{file}/render`, which resolves the producer from the item
itself and uses a deterministic job id as a per-item lock.

Each call returns `{job_id}`; poll `GET /api/pipeline/status` or subscribe to
the unnamed frames on `GET /api/events` (SSE) for job status. See
[API Reference](api-reference.md#pipeline-events) for the full contract.

```bash
curl -X POST http://127.0.0.1:8787/api/pipeline/instagram/scrape
# => {"job_id": "..."}
curl http://127.0.0.1:8787/api/pipeline/status
```

---

## AnalysisEngine `cli.py`

Run from inside `AnalysisEngine/`. Watches downloaded clips frame-by-frame
with Gemini and writes schema_version-2 blueprints back to the hub. See
[Agents → AnalysisEngine](agents-analysisengine.md) for the full call
sequence.

| Command | Usage | Args / flags | What it does | Example |
|---|---|---|---|---|
| `run` | `uv run cli.py run <platform> [--no-references]` | `<platform>`; `--no-references` skips the reference queue | Bootstraps (health check, self-registers as a producer, fetches its hub-stored config), then drains the analysis work queue: `GET /api/analysis/{p}/pending` (top-viral clips with media but no blueprint yet, or flagged `stale`) plus, unless `--no-references`, `GET /api/reference/{p}/pending`. Each item is analyzed with Gemini, self-judged, optionally refined, and posted to `POST /api/analysis/{p}`, with a matching `POST /api/evals`. Emits the full `run.start → item.start → item.stage → item.done → run.end` lifecycle to `POST /api/logs`. | `uv run cli.py run instagram` |
| `once` | `uv run cli.py once <content_id>` | `<content_id>` — a single content or reference id | Same bootstrap, then searches the pending queues across platforms for the matching id and runs the identical single-item analyze path, wrapped in one `run.start`/`run.end` pair — useful for re-running or debugging one clip without draining the whole queue. | `uv run cli.py once ig_abc123` |
| `status` | `uv run cli.py status` | none | Bootstraps, then prints hub connectivity (`GET /api/platforms`) and this agent's secret presence (`GET /api/config/agent/analysis-engine/secrets/status`) plus the resolved boot config — a quick health/config check with no side effects. | `uv run cli.py status` |

!!! tip "`run` vs `once`"
    Use `run <platform>` for the normal unattended pipeline pass over the
    whole queue. Use `once <content_id>` when you need to force one specific
    clip through analysis — e.g. after fixing a prompt bug, or to unblock a
    producer waiting on one blueprint.

---

## AutoSearch `cli.py`

Run from inside `AutoSearch/`. The "front door" agent: searches Instagram for
new creators, scores niche-fit, and posts candidates to the hub's discovery
queue for human approval. See
[Agents → AutoSearch](agents-autosearch.md) for the full call sequence,
cadence model, and safety rules.

| Command | Usage | Args / flags | What it does | Example |
|---|---|---|---|---|
| `run` | `uv run cli.py run <platform>` | `<platform>` | Manual/exhaustive discovery pass. Bootstraps, checks the `discovery_enabled` kill-switch (fail-closed — skips and logs `run.skip` if false), loads niche config and corpus factors, expands search terms (via Gemini **only** if `term_expansion_enabled` is true and a Gemini key resolves; otherwise seed keywords verbatim — the default), bootstraps a guest IG session, and searches each term — hydrating profiles, sampling reels, scoring niche-fit, and posting every passing candidate to `POST /api/discovery/{p}`. Bypasses the weekly plan/cadence but still respects caps, pacing, and the circuit breaker. | `uv run cli.py run instagram` |
| `beat` | `uv run cli.py beat <platform> [--max_units N]` | `<platform>`; `--max_units` caps work units per tick | The cadence heartbeat tick, meant to be invoked frequently (e.g. by the hub's background heartbeat thread, cron, or a scheduled routine). Bootstraps, checks the kill-switch, then runs a pure-local gate (`rest_day → out_of_window → over_cap → breaker_cooldown → random probability`) — most beats no-op by design and print/log nothing beyond a `beat.skip`. When the gate opens, it runs the same per-candidate discovery flow as `run`, capped to `beat_max_units` (or `--max_units`) work units, and updates the local cadence ledger. | `uv run cli.py beat instagram` |
| `synthetic` | `uv run cli.py synthetic <platform> [N]` | `<platform>`; optional count `N` | Verification path: fabricates `N` candidates locally with no Instagram or LLM calls, and drives the identical `item.start → Searching → Scoring → post_candidate → item.done` sequence as a real run, ending with one `post_insight` and `run.end`. Useful for exercising the hub/Dashboard discovery UI without touching Instagram. | `uv run cli.py synthetic instagram 5` |
| `smoke` | `uv run cli.py smoke` | none | Verification path: asserts the guest cookie jar carries no `sessionid` and performs one `web_profile_info` hydration. No hub writes besides logs — a safety self-check that the guest-only posture is intact before a real run. | `uv run cli.py smoke` |
| `status` | `uv run cli.py status` | none | Bootstraps, then reports hub connectivity, this agent's secret presence, and the resolved config (including `discovery_enabled` and cadence knobs) — a quick health/config check with no side effects. | `uv run cli.py status` |

!!! warning "Kill-switch and pacing are enforced in the code path, not just documented"
    Both `run` and `beat` read `discovery_enabled` from
    `GET /api/config/agent/auto-search` and fail closed (default `false`) if
    it's missing or false. AutoSearch is one of only two agents permitted to
    touch Instagram at all (alongside ReelScraper itself), and it does so
    read-only, guest-first, paced strictly slower than the scraper, with a
    circuit breaker on repeated failures.

---

## SimilarContent `cli.py`

Run from inside `SimilarContent/`. The reference **producer**: it reads the
scored corpus plus blueprints, writes clone recipes into the human gate, and —
once a human approves one — renders it into an actual reel. See
[Agents → Producers & SPI](agents-producers.md) for the manifest contract.

The two halves are deliberately split by cost:

- **`propose` is free.** It reads blueprints and writes markdown. No
  image-provider key required.
- **`render` is paid.** Every frame is an image-API call, so it is
  human-triggered only and never part of the one-click pipeline run.

| Command | Usage | What it does |
|---|---|---|
| `propose` | `uv run cli.py propose --platform instagram` | Ranks the corpus, attaches schema-2 blueprints, and publishes the easiest-to-make winners to `POST /api/studio/{p}` as `proposed`. |
| `render` | `uv run cli.py render --platform instagram --file <name.md>` | Turns ONE approved recipe into a reel: generates frames, stitches with ffmpeg, writes a caption, and uploads the result to `POST /api/renders/{p}`. This is what the hub launches when you press **Render** in the Studio. |
| `status` | `uv run cli.py status [--platform instagram]` | Prints the active image provider and whether its key resolves, whether ffmpeg is present, and a checklist of approved items with their render state. No side effects. |
| `register` | `uv run cli.py register` | (Re)posts this producer's manifest to `POST /api/producers/register`. Idempotent by name. |

!!! warning "The verb is `propose`, not `run`"
    Unlike AnalysisEngine and AutoSearch, SimilarContent has no `run`
    subcommand — because there is no single unattended pass to make. The
    human gate sits between the two halves by design.

### `propose` flags

| Flag | Default | What it does |
|---|---|---|
| `--platform` | `instagram` | Platform to read the corpus from. |
| `--count N` | the `top_n` hub knob (5) | How many recipes to publish this run. |
| `--top N` | `max(15, 3 × count)` | Size of the corpus pool to rank before picking. |
| `--topic "..."` | — | Focus on a topic via `/api/corpus/{p}/search` instead of `/top`. |
| `--content-id ID` | — | Propose these exact exemplars, skipping ranking. Repeatable. Ranking cannot reach a mid-corpus clip (e.g. a freshly scraped creator), so this is the escape hatch. |
| `--dry-run` | off | Select and build the recipes, but POST nothing. |

### `render` flags

`render` requires either `--file` or `--all-approved` — it will not guess.

| Flag | Default | What it does |
|---|---|---|
| `--platform` | `instagram` | Platform whose studio to read. |
| `--file <name.md>` | — | Render exactly one studio item (what the hub passes). |
| `--all-approved` | off | Render every approved item that has no render yet. |
| `--limit N` | — | Cap `--all-approved`. |
| `--max-frames N` | the `max_frames_per_clone` knob (12) | Override the per-clone frame budget. |
| `--force` | off | Re-render even if a render already exists. |
| `--restitch` | off | Re-encode the frames already on disk. Free — no image calls, same pictures, existing caption kept. Use after a stitcher change. |
| `--dry-run` | off | Parse the recipe, allocate frame holds, and print every composed prompt without making a single API call. |

!!! tip "Start with `--dry-run`"
    It is where most mistakes are visible, and where they cost nothing.

### Output shape knobs

Two `register.py` knobs control the canvas, both settable from the Dashboard's
agent config:

| Knob | Default | Values | What it does |
|---|---|---|---|
| `aspect_ratio` | `9:16` | `9:16`, `4:5`, `1:1` | The output canvas. `9:16` (1080×1920) is the reels/shorts/tiktok format and the only one that fills a phone full-bleed. `4:5` = 1080×1350 (IG feed portrait), `1:1` = 1080×1080. |
| `video_fit` | `auto` | `auto`, `cover`, `contain` | How a generated frame meets that canvas. `auto` crops when the frame is within 10% of the canvas aspect and letterboxes when it is further out; `cover` always crops; `contain` always letterboxes. **None of them ever stretch.** |

!!! note "There is deliberately no width/height knob"
    The canvas is derived from `aspect_ratio`, so the output can never be a
    size that disagrees with the aspect it claims to be.

### Guard rails

- **A key is only demanded when one is actually needed.** `propose`,
  `status`, `--dry-run` and `--restitch` all bootstrap with `need_key=False`.
- **`render` skips anything not `approved`**, and skips already-rendered items
  unless `--force` or `--restitch`.
- **Circuit breaker.** Three consecutive image failures — a bad key or an
  exhausted quota — abort the whole run rather than burning the queue one paid
  failure at a time.
- **ffmpeg is checked up front** on any non-dry run.

---

## Ports: nothing is hardcoded

`8787` is a *preference*, not a contract. `cli.py start` probes the port and
binds an OS-assigned free one when it is busy, then:

1. prints `HUB_URL=http://127.0.0.1:<port>` on stdout, so a launcher script can
   discover the real port;
2. exports `BACKEND_API` into its own environment, so every stage subprocess it
   spawns (AnalysisEngine, AutoSearch, a producer's render command) inherits
   the address the hub actually got instead of defaulting to 8787.

Every agent resolves the hub from `BACKEND_API` (falling back to
`http://127.0.0.1:8787`). The Dashboard dev server reads the same variable —
see [Agents → Dashboard](agents-dashboard.md). So when the hub lands on
another port:

```bash
BACKEND_API=http://127.0.0.1:9123 uv run cli.py status   # any agent
BACKEND_API=http://127.0.0.1:9123 npm run dev            # Dashboard dev server
```

Production needs none of this: the hub serves the built Dashboard
same-origin, so it is port-agnostic by construction.

### One port per checkout

A fallback port is fine for a one-off, and useless as an address — it changes on
every restart, so nothing can be bookmarked and no `.env` can point at it. `./init`
therefore **pins** the port a checkout owns into `ReelScraper/.env` as `HUB_PORT`
(8787 for the first clone on the machine, 8788 for the next) and writes that address
into every component's `BACKEND_API`.

That second half is the one that matters. `cli.py start` exports `BACKEND_API` only
for the stages *it* spawns; an agent you run by hand reads its own `.env`. On a
second clone, a `.env` still pointing at 8787 aims that agent at the **first** clone's
hub — where it reads the wrong corpus and writes to the wrong studio, with every call
returning 200. Each agent checks `GET /api/hub` at startup and refuses to run against
a hub belonging to another checkout. See
[Niches → Running two niches at once](niches.md#running-two-niches-at-once).

---

## Planned: unified CLI-parity commands

!!! note "Not yet built"
    The following are planned additions, not present in the current CLIs.
    They're called out here so the eventual command surface is discoverable
    ahead of implementation.

The Dashboard's human gate currently exposes discovery candidate
approve/reject only as an HTTP call
(`POST /api/discovery/{platform}/{candidate_id}/status`, see
[API Reference](api-reference.md#discovery-autosearch)). The plan is to give AutoSearch's
own CLI parity with that gate so it can be operated headlessly (e.g. from a
script or cron job) without going through the Dashboard:

| Planned command | Would do |
|---|---|
| `discover list` | List pending discovery candidates for a platform (CLI equivalent of `GET /api/discovery/{platform}/pending`). |
| `discover approve <candidate_id>` | Approve a candidate — hub appends the handle to `pages.txt` (CLI equivalent of `POST /api/discovery/{platform}/{candidate_id}/status {"status":"approved"}`). |
| `discover reject <candidate_id>` | Reject a candidate (CLI equivalent of the same endpoint with `{"status":"rejected"}`). |

These would call the same `/api/discovery/*` routes the Dashboard already
uses — no new hub endpoints, just a CLI-side client for the existing human
gate contract.

---

## See also

- [Architecture](architecture.md) — how the hub, agents, and Dashboard fit together.
- [Pipeline](architecture.md) — the 8-stage Discover → Studio flow these commands drive.
- [API Reference](api-reference.md) — the full `/api/*` contract every CLI talks to.
- [Agents](agents-reelscraper.md) — per-agent call sequences (AnalysisEngine, SimilarContent, AutoSearch).
