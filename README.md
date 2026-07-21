# The Cutting Room

A multi-agent system that scrapes handpicked creators, scores every post for
virality, breaks the winners down into generation-ready **blueprints**, and spins
those into ready-to-post content — all behind a single HTTP hub, with a human
gate before anything ships.

Each capability is its **own directory / its own agent**. They integrate **only
over HTTP**, never by touching each other's files.

<!-- VincitoreSi below is a placeholder. Set VincitoreSi in .env, then run
     `bash scripts/apply-identity.sh` — it rewrites every occurrence in this
     file, CHANGELOG.md, .github/ISSUE_TEMPLATE/config.yml and mkdocs.yml. -->
[![Health](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/health.yml/badge.svg)](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/health.yml)
[![CI · Dashboard](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/ci-dashboard.yml/badge.svg)](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/ci-dashboard.yml)
[![CI · Python](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/ci-python.yml/badge.svg)](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/ci-python.yml)
[![Docs](https://github.com/VincitoreSi/TheCuttingRoom/actions/workflows/docs.yml/badge.svg)](https://VincitoreSi.github.io/TheCuttingRoom/)
[![License: MIT](https://img.shields.io/badge/License-MIT-informational.svg)](LICENSE)

📖 **[Documentation](https://VincitoreSi.github.io/TheCuttingRoom/)** — quickstart, architecture, API reference, and per-agent guides.

```
Sources ─▶ Scrape ─▶ Analyze(score) ─▶ Media ─▶ AnalysisEngine(blueprint) ─▶ [ PRODUCERS ] ─▶ Studio ─▶ human gate ─▶ Render ─▶ upload by hand
   └──────────────── ReelScraper (the hub, 127.0.0.1:8787 by default) ──────┘        │                       │            │
                                                                                     │  clone / proposal     │            └─ frames + ffmpeg ─▶ silent 9:16 mp4
                                          Dashboard ("The Cutting Room") ◀── reads/controls everything over HTTP
```

> **Use responsibly.** This tool automates scraping and third-party AI APIs.
> Respect each platform's Terms of Service, use **burner accounts** for any
> session, follow applicable laws, and never commit secrets. Ships with **Fashion**
> as a worked example; `pages.txt` starts from **synthetic** placeholder handles.
>
> ℹ️ **This is the clean, shareable build.** It ships with **no scraped data** —
> `demo-data/data/` is empty, and **no release attaches the dataset**. The realistic demo
> dataset travels **separately** as `demodataset.zip`: it is real scraped content — real
> creator handles, captions, engagement metrics and media — so publishing it would expose
> third-party personal data without consent and would breach Instagram's terms. It is shared
> privately instead. **To request a copy, open a
> [Discussion](https://github.com/VincitoreSi/TheCuttingRoom/discussions).** Without it,
> `./init` gives a clean start you fill with your own watchlist — nothing in the pipeline
> requires the dataset. See [`demo-data/README.md`](demo-data/README.md).

## Quick start

You need **Python ≥ 3.10** with [uv](https://docs.astral.sh/uv/), and **Node ≥ 20**
for the Dashboard. A handful of scripts at the repo root are the whole interface:

```bash
./demo      # a populated studio to look around in — no keys, no scraping, instant
./init      # a clean first run: checks, installs, verifies your Gemini key, launches
./stop      # shut down everything this checkout started
./clean     # archive all data to a zip, then wipe back to a fresh clone
./docsite   # build + serve the documentation site with live reload
./health    # run every test suite, build, and repository invariant
```

Each checks its prerequisites, installs what's missing, picks a free port if 8787 is
taken, and opens a browser. One process serves everything — the hub builds the
Dashboard into its own static directory, so there is no second dev server to run.

**`./demo` is the fastest way to understand the project** — *if you have the dataset.* Put
`demodataset.zip` in the repo root and `./demo` unpacks it and opens on a fully populated
studio: a scored corpus, Gemini blueprints, clone recipes at the human gate, and rendered
reels that play inline. Without the zip, `./demo` explains how to get it or points you at
`./init`. The dataset is real scraped content, so it is shared privately rather than
committed — see [`demo-data/README.md`](demo-data/README.md).

**`./init` is the real first run.** It leaves you on an empty dashboard, which is the
point: you supply your own niche. The key you paste is verified against Google on the spot —
a revoked, mistyped, or wrong-project key is caught then, not partway through a paid run.
Re-check any time (this also covers keys added by hand or via the environment):

```bash
python3 scripts/check-keys.py          # read-only: authenticates, spends nothing
```

From there:

1. **Add a creator.** On the Board, the **Sources** card's *Add pages* button opens the
   watchlist; pin an Instagram handle. (Or edit
   `ReelScraper/platforms/instagram/pages.txt` directly — one handle per line.)
2. **Press *Run full pipeline*.** It runs scrape → analyze → media → blueprints in order
   and stops if one fails. Each card shows its own count as its stage completes, and a
   stage whose input is not ready is greyed out with the reason and a button for the stage
   that unblocks it.
3. **Draft from the corpus** — approve what you like in the Studio.

The same thing without the browser:

```bash
cd ReelScraper && uv run cli.py scrape instagram    # then: analyze, media
cd SimilarContent && uv run cli.py propose --platform instagram --dry-run
```

Config → **Automatic runs** repeats scrape → analyze → media on a timer (daily, weekly, …)
for as long as the hub is running. Blueprint generation is opt-in there because it spends
API credits on every run.

`--port N` pins a port and `--no-launch` sets up without starting anything. When you're
done: `./stop` shuts down everything this checkout started (and nothing belonging to
another clone). `./init --reset` clears stored API keys but keeps your corpus; `./clean`
goes all the way back to a fresh clone — archiving every scrap of data to a verified
`backups/*.zip` and telling you where it is *before* it deletes anything.

**`./health` is the one command to trust before you commit.** It runs all four Python test
suites, the Dashboard's typecheck / lint / unit tests / production build, the docs build, and a
set of repository invariants — then exits non-zero if anything failed, so it works as a
pre-commit hook or a CI gate.

```bash
./health           # everything except the live HTTP surface
./health --quick   # unit tests only, no builds or docs
./health --live    # also boots the hub and exercises the real endpoints
```

The invariants are the interesting part, because each one covers a failure that unit tests
cannot see and that has actually happened in this repo: a generated reel landing in the
scraped-corpus namespace, an unanchored `.gitignore` rule silently swallowing most of
`demo-data/`, a credential file reappearing, secrets entering git history, or the hub growing a
non-loopback bind.

Each agent (AnalysisEngine, AutoSearch, SimilarContent, …) is its own directory with its
own `.env.example`; `uv sync` then `uv run cli.py …` inside it. See `./docsite` for the full
quickstart, API reference, and per-agent guides.

## What you need, and what runs where

**Nothing here requires Claude Code, an editor, or an AI coding session.** Every stage is a
plain CLI you can run, script, or put in cron. The `CLAUDE.md` files and `.claude/` directories
exist so you *can* drive the agents conversationally, but the pipeline does not depend on them
— the agents talk to Google and Anthropic REST APIs directly over stdlib HTTP.

| To do this | You need | Costs money? |
| --- | --- | --- |
| `./demo` — explore a populated dashboard (needs `demodataset.zip`) | uv, python ≥3.10 | no |
| `./docsite` — read the documentation | uv, python ≥3.10 | no |
| `./init` + scrape + score a niche | + node ≥20, npm, curl | no |
| Blueprints (`AnalysisEngine`) | + `GEMINI_API_KEY` | yes — Gemini |
| Clone recipes (`SimilarContent propose`) | nothing extra | no |
| Rendering reels (`SimilarContent render`) | + `GEMINI_API_KEY`, **ffmpeg** | yes — ~$0.04/frame |
| Creator discovery (`AutoSearch`) | + `ANTHROPIC_API_KEY` | yes — Anthropic |

A first run that only wants to *look* at the system needs **uv and Python**. Scraping and
scoring are free and keyless. Only the AI stages cost anything, and each is opt-in.

`ffmpeg` is optional until you render: `./init` warns if it is missing rather than failing.

### The full loop, end to end

Every one of these is a normal command in a normal shell:

```bash
./init                                          # setup + an empty hub

$EDITOR ReelScraper/platforms/instagram/pages.txt   # your creators, one per line

cd ReelScraper
uv run cli.py scrape  instagram                 # guest mode, no login
uv run cli.py analyze instagram                 # score into content.json
uv run cli.py media   instagram --top 60        # persist the winners locally

cd ../AnalysisEngine
uv run cli.py run instagram                     # Gemini -> schema-2 blueprints
uv run cli.py once <content_id>                 # …or just one clip

cd ../SimilarContent
uv run cli.py propose --platform instagram --dry-run   # see the picks, free
uv run cli.py propose --platform instagram             # publish to the human gate
#   approve in the Dashboard (Studio -> Proposals), then:
uv run cli.py render --platform instagram --file <name>.md
```

The rendered reel appears in **Studio → Renders** with its sound sheet, a generated caption,
and the on-disk path to upload by hand. Instagram has no post API for this, so the last step
is deliberately manual.

### Letting it run itself

Doing that by hand once is how you learn the pipeline. After that, two mechanisms run it
unattended, both **off by default** and both per-platform:

- **A timer** — every N hours, run `scrape → analyze → media`.
- **The cascading heartbeat** — a 60s tick that watches how much *new* material has landed
  and fires the next stage that has enough to chew on.

You size the heartbeat as a funnel: one batch (`scrape_count`, 250 reels by default), then
how much of each stage's input is worth passing to the next — 100% analyzed, 60% worth
downloading, 20% of those worth a blueprint, 20% of those worth proposing against. Because
no percentage can exceed 100, **a later stage can never be configured to fire more often
than the one feeding it**; the funnel only ever narrows.

Two things it will never do. It never runs `render`, which is the only step that spends
money per frame — that stays behind a human click, by construction rather than by config.
And the blueprint stage costs Gemini credits, so it sits behind its own explicit opt-in and
stamps its watermark when you enable it, meaning switching it on starts the clock rather
than settling months of backlog in one unattended burst.

Any running stage can be cut short from the board — the Stop button on a running card. Stops
are cooperative: scrapers finish the creator they are on and save, so a stop keeps everything
already written instead of discarding the run.

Both are configured in the Dashboard (**Config**), or over the API — see
[the cascade](documentation/docs/api-reference.md#the-cascade).

## The one principle

The FastAPI hub inside **ReelScraper** (`http://127.0.0.1:8787`) is the **single
integration point**. Every agent reads and writes **only via `/api/*`** and never
touches another agent's files.

- **`content_id`** is the universal content join key (across `content.json`, media
  files, and analysis blueprints).
- **`audio_id`** is the sound join key (parallel to `content_id`).

Decoupling comes from the HTTP boundary, not folder layout.

## The hub is ReelScraper (it also runs the scraper)

The backend/hub lives inside `ReelScraper`; the same repo both runs the scraper
and serves the hub API. Agent decoupling is provided by the HTTP boundary
(`/openapi.json`), *not* by folder location, so relocating buys no decoupling —
the hub is deeply coupled to the scraper's `core/`, per-platform data, and
subprocess pipeline control.

It prefers **127.0.0.1:8787** but does not insist on it: if that port is taken it binds a free
one, prints `HUB_URL=…`, and exports `BACKEND_API` so every agent it spawns follows. `./init`
goes further and *pins* the port a checkout owns (`HUB_PORT` in `ReelScraper/.env`), so a
second clone settles on 8788 and stays there. Override with `--port` / `--host` or
`HUB_PORT` / `HUB_HOST`. Agents resolve the hub from `BACKEND_API`, so nothing hardcodes a
port. The hub binds loopback only — it is a local tool, not a server.

## Agent roster

| Directory | Role | Talks to hub via |
| --- | --- | --- |
| **ReelScraper** | **The hub @ :8787** — scrapes creators, scores virality, and serves the whole `/api/*` contract (corpus, analysis, audio, producers, studio + human gate, references, logs, evals, config/secrets status, SSE). It *is* the pipeline's backend. | serves it |
| **AnalysisEngine** | Sits after Media. Watches top clips and writes rich, generation-ready **blueprints** (schema_version 2) to `POST /api/analysis/{p}`. The shared substrate every producer reads. | `/api/analysis`, `/api/corpus`, `/api/insights` |
| **AutoSearch** | Discovery agent (`kind: discovery`). Finds and scores new creators in the niche; results go through the human gate. | `/api/corpus`, `/api/insights`, producer SPI |
| **SimilarContent** | Producer (`kind: clone`). `propose` turns a blueprint's `shots[]` + `regeneration_guide` into a clone recipe at the human gate; `render` then generates the frames (Nano Banana), stitches them with ffmpeg into a silent 9:16 reel, writes a caption, and uploads it. | producer SPI + `/api/renders` |
| **Dashboard** | "The Cutting Room" — React control board. Reads/controls everything over HTTP; renders producer lanes, the human gate, sounds, blueprints, activity + evals. | reads all of `/api/*` + SSE |
| **`_producer-template/`** | The **reusable producer scaffold** — copy it to spin up a new producer. | — |

### Future producers (spun from `_producer-template/` on demand)

Each is `cp -r _producer-template <Dir>` + fill the blanks:

- **proposal-content** (`kind: proposal`, `human_gate: true`) — original script
  proposals grounded in winning factors; a human approves in the Dashboard.
- **creative-idea** (`kind: idea`) — net-new viral concepts cross-referencing
  factors, formulas across many blueprints, and trending audio.
- **template-content** (`kind: template`, `needs_reference: true`) — applies a
  reference video's structure to the operator's own topic.

## The Producer SPI (what makes producers replaceable)

Every generation agent obeys one contract, differing only in strategy and
declared inputs:

1. Its own directory + its own `memory/` (persona + patterns) so each voice stays distinct.
2. **Self-registers on startup:** `POST /api/producers/register` with
   `{name, kind, consumes[], human_gate, needs_reference, produces, output_status, config_schema, secrets}`.
3. **Reads only hub inputs** (per `consumes`): `/api/corpus/{p}/{factors|brief|top|search}`,
   `/api/analysis/{p}[/{id}]`, `/api/audio/{p}/trending`, `/api/insights`.
4. **Writes only hub outputs:** `POST /api/studio/{p}` and `POST /api/insights`
   (one transferable learning per run). Every output carries the copy-ready **`## Audio`** block.
5. **Human gate:** `human_gate:true` → `status:"proposed"`, approved/rejected in the Dashboard.

## Choosing a niche

Fashion ships as the worked example on `main`. The niche is config-driven —
keywords, virality weights, tiers, and seed pages live in
`ReelScraper/platforms/*/niche_config.json`, sourced from a single definition in
`niches/`. To target a different vertical, branch a full pipeline for it:

```bash
./scripts/new-niche.sh cricket     # or football, travel, or your own
```

See [`niches/README.md`](niches/README.md) for the schema and how to add one.

**Two niches at once = two clones.** They cannot interfere. Nothing here writes outside its
own directory, so the only thing two checkouts share is the loopback — and `./init` settles
that: it pins a port per checkout (8787, then 8788) in `ReelScraper/.env` and points every
agent's `BACKEND_API` at its own hub. Agents verify it too, refusing to run against another
checkout's hub rather than writing this niche's work into that one's corpus. The Dashboard
sidebar names the niche, since two boards otherwise look identical.

## Documentation

- **`documentation/`** — the MkDocs site: quickstart, concepts, architecture, API
  reference, CLI, and per-agent guides. Run **`./docsite`** from the repo root to build
  and serve it with live reload, or **`./docsite --build`** to build only. (`mkdocs` is
  not a global install — it lives in ReelScraper's dev dependency group, so the
  manual equivalent is
  `uv run --project ReelScraper mkdocs build -f documentation/mkdocs.yml`.)
- **Design notes** — the deep reference, also in the docs site under *Design notes*:
  [`documentation/docs/internal/architecture-reference.md`](documentation/docs/internal/architecture-reference.md)
  (hub contract, Producer SPI, platform-wide concerns, discovery) and
  [`documentation/docs/internal/analysis-engine-design.md`](documentation/docs/internal/analysis-engine-design.md)
  (blueprint schema + audio intelligence).

## Contributing

Contributions are welcome! Start with [CONTRIBUTING.md](CONTRIBUTING.md), and note
the golden rule: agents integrate **only over the HTTP hub**. Please also read the
[Code of Conduct](CODE_OF_CONDUCT.md) and [Security Policy](SECURITY.md). See the
[Roadmap](ROADMAP.md) for where things are headed.

## License

[MIT](LICENSE) © The Cutting Room contributors.
