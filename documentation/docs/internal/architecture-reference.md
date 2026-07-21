# Architecture reference

**Design note.** This is the deep reference for the virality pipeline's architecture: the hub
contract, the Producer SPI, the analysis substrate, the platform-wide concerns (logging,
evaluation, config, secrets), and the discovery agent. It describes the system **as built**.

Its companion is [AnalysisEngine — design notes](analysis-engine-design.md), which covers the
analysis blueprint schema and the audio-intelligence layer in detail.

!!! note "About this document"
    This began life as `PIPELINE.md` at the repo root, where it doubled as a one-time build
    plan. That framing — the orchestrator instructions (§0) and the per-directory rollout
    slices (§6) — described work that is now finished and has been removed. **The remaining
    section numbers are unchanged**, because `§N` references to this document are cited
    throughout the codebase (for example `PIPELINE.md §10.4` in each agent's `.env.example`).
    That is why the numbering jumps from §5 to §7.

---

## 1. System overview

```
Sources ─▶ Scrape ─▶ Analyze(score) ─▶ Media ─▶ AnalysisEngine(blueprint) ─▶ [ PRODUCERS ] ─▶ Studio ─▶ human gate ─▶ post
   └──────────────── ReelScraper (the hub @ 127.0.0.1:8787) ────────────────┘        │                    │
                                                                                     │  clone / proposal / idea / template
                                          Dashboard ("The Cutting Room") ◀── reads/controls everything over HTTP
```

**The one principle:** the FastAPI hub in ReelScraper (`http://127.0.0.1:8787`) is the single integration
point. Every agent reads and writes **only via `/api/*`** and never touches another agent's files.
`content_id` is the universal content join key; `audio_id` is the sound join key. Decoupling comes from the
HTTP boundary, not folder layout.

**Backend location — settled:** the hub stays inside ReelScraper (see the architecture-decision block at the
top of the [AnalysisEngine design note](analysis-engine-design.md)). Forward-compat rules every component
honors: integrate via the `BACKEND_API` URL only, never a hardcoded path; the Dashboard deploy target is
parameterized via `BACKEND_DIR` (default `../ReelScraper`); the repo-root `README.md` documents "the hub is
ReelScraper at :8787."

---

## 2. The finalized hub contract (what ReelScraper exposes)

Existing (keep): `/api/platforms`, `/api/content/{p}`, `/api/config/{p}`, `/api/corpus/{p}/{factors|brief|top|search}`,
`/api/analysis/{p}[/pending|/{content_id}]`, `/api/studio/{p}`, `/api/insights`, `/api/pipeline/{p}/{stage}`,
`/api/pipeline/status`, `/api/events` (SSE), `/media/...`.

New in this finalization:
- **Analysis blueprint v2** — `POST /api/analysis/{p}` accepts `schema_version:2` (rich blueprint +
  `virality_formula` + `audio_strategy` + `evaluation`). Backward-compatible. *(Detail: the design note's "The AnalysisEngine agent" + "ReelScraper (the hub) — the extended contract".)*
- **Audio intelligence** — `GET /api/audio/{p}/trending`, `GET /api/audio/{p}/sound/{audio_id}`; audio fields
  on content rows. *(Detail: the design note's "ReelScraper (hub) — collecting and scoring sounds".)*
- **Producer registry (pluggability backbone)** —
  - `POST /api/producers/register` `{name, kind, consumes[], human_gate, needs_reference, produces, output_status}`
    → upsert a producer manifest (stored `producers/registry.json`). Idempotent by `name`.
  - `GET /api/producers` → the roster (Dashboard renders lanes from this).
- **Human gate on studio proposals** —
  - Studio items carry `status ∈ {draft, proposed, approved, rejected}`, `agent`, `kind`, plus existing `file`/`text`.
  - `POST /api/studio/{p}` extended to accept `status`/`agent`/`kind` (default `status:"proposed"`
    **on first insert only** — a re-POST without an explicit `status` PRESERVES the existing gate
    decision, so a producer re-posting its own markdown cannot silently un-approve it).
  - `POST /api/studio/{p}/{file}/status` `{status, note}` → record a gate decision (append to `studio/{p}/gate.jsonl`).
  - `GET /api/studio/{p}?status=&agent=` → filterable list. `GET /api/studio/{p}/{file}` → one item.
- **Render store (producer-generated media)** —
  - `POST /api/renders/{p}` `RenderIn` `{file, agent, kind, caption, duration_s, frames[], assets[]}`
    where `assets[]` carry **base64** payloads (not multipart — the hub has no `python-multipart`
    dependency, and every agent speaks stdlib urllib). `render_id` is derived SERVER-side from the
    studio filename, so one studio item ⇒ one render directory and re-rendering overwrites in place.
  - `GET /api/renders/{p}[?file=&agent=&kind=]`, `GET|DELETE /api/renders/{p}/{render_id}`. Rows are
    hydrated with `video_url` / `poster_url` (cache-busted `?v=<updated_at ms>`) and `local_path`.
  - Served at `/renders` (range-capable), mounted before the `/` catch-all. Stored under
    `renders/{p}/{render_id}/`.
  - **This namespace is strictly separate from `media/{p}/`** (scraped corpus, keyed by
    `content_id`). `save_render` refuses any asset name shaped like a `content_id`. Mixing them
    makes `/api/content` serve generated video under a real creator's id — it has happened.
- **Per-item render trigger** —
  - `POST /api/studio/{p}/{file}/render` `{force?}` → 409 unless the item is `approved`; launches the
    producer that wrote it, resolved from that producer's manifest (`renderable`, `dir`,
    `render_cmd`), never a hardcoded path. Deterministic job key `{p}:render:{file}` doubles as the
    per-item lock (`already_running: true`) and the Dashboard's SSE lookup key.
  - **`render` is NOT in `RUN_ALL_STAGES`** — it spends image-API credits, so it only runs on an
    explicit human action.
- **Reference/template ingestion (only consumer: the template agent)** —
  - `POST /api/reference/{p}` `{url}` (URL only; multipart upload is deliberately not a dependency) → register an ad-hoc reference, download media, assign a
    synthetic id `ref_<hash>`, mark it pending. It is NOT corpus content (not scored, not a real reel).
  - AnalysisEngine analyzes references too (its pending queue includes `is_reference` items, or a sibling
    `GET /api/reference/{p}/pending`); the blueprint is saved with `is_reference:true` and served at
    `GET /api/analysis/{p}/{ref_id}` and listed by `GET /api/reference/{p}`.

---

## 3. The Producer SPI (the generation-agent contract) — the heart of pluggability

Every generation agent (existing SimilarContent and all future ones) obeys ONE contract, differing only in
strategy and declared inputs. This is what makes them replaceable.

**Every producer:**
1. **Is its own directory**, CLAUDE.md-driven (script optional), with its own `memory/` (persona + patterns)
   — separate memory keeps each agent's voice distinct.
2. **Declares a manifest** and **self-registers** on startup: `POST /api/producers/register` with:
   ```
   { name, kind: "clone"|"proposal"|"idea"|"template",
     consumes: ["corpus","analysis","audio","insights", ("reference_blueprint")],
     human_gate: bool, needs_reference: bool,
     produces: "studio_markdown", output_status: "proposed"|"draft",
     config_schema: { ... },              # JSON Schema of the agent's tunable knobs + defaults (§10.3)
     secrets: [ {name, env_var, required} ] }   # declared by NAME only, never values (§10.4)
   ```
3. **Reads only these hub inputs** (per its `consumes`): `GET /api/corpus/{p}/{factors|brief|top|search}`,
   `GET /api/analysis/{p}[/{content_id}]`, `GET /api/audio/{p}/trending`, `GET /api/insights`. Reference-driven
   agents additionally read `GET /api/reference/{p}` + the chosen reference blueprint.
4. **Writes only these hub outputs:** `POST /api/studio/{p}` `{filename, text, agent, kind, status}` with
   filename `<date>-<agent>-<slug>.md`; and `POST /api/insights` (append one transferable learning per run).
   A producer that also RENDERS its approved items writes `POST /api/renders/{p}` as well, and declares
   `renderable: true` + `dir` + `render_cmd` in its manifest so the hub knows how to launch it. Rendered
   media goes to the render store and **never** into `media/{p}/` (the scraped-corpus namespace).
5. **Includes the `## Audio` block** (see the design note's audio layer) — reuse original / substitute / pick trending sound +
   the exact Instagram sound name + link for manual posting.
6. **Honors the rules:** never scrape, hub-only, resume-safe, per-run JSONL logs, pacing + 3-strike breaker.

**The input distinction you asked for, encoded once:** every producer can work from the **saved analysis +
corpus alone** — that's why `consumes` defaults to `["corpus","analysis","audio","insights"]`. The single
exception, `TemplateOrStyleAgent`, additionally sets `needs_reference:true` + `consumes:[...,"reference_blueprint"]`,
and the hub's reference-ingestion path is what feeds it. No other agent needs external material.

**Human gate:** if `human_gate:true`, the agent writes proposals as `status:"proposed"` (often several
variants), and a human approves/rejects in the Dashboard (`POST /api/studio/{p}/{file}/status`). Approved
items surface in a "ready to post" list. Agents with `human_gate:false` may still be reviewed but don't block.

---

## 4. The analysis is the shared substrate

AnalysisEngine's blueprint (schema v2 — see the design note) is deliberately a superset so ALL producer kinds read
one document:
- **clone** (SimilarContent) → uses `shots[]` + `regeneration_guide` for 1:1 recreation.
- **proposal / idea** → use `virality_formula` (hook/retention/replicable_formula), `global_style`, `audio_strategy`,
  and corpus `factors` to invent original content grounded in what works.
- **template** → uses a *reference* blueprint (same schema, `is_reference:true`) for structure/style, applied
  to the operator's own topic.

So once a clip is analyzed and saved, every agent except the template agent has everything it needs from the
hub. Keep the blueprint rich and generation-ready; do not fork per-agent schemas.

---

## 5. Producer roster — manifests

Built:

- **similar-content** — `kind:clone`, `consumes:[corpus,analysis,audio,insights]`, `human_gate:false`,
  `needs_reference:false`, `output_status:proposed`.

Designed but not built. Each is spun up from the `_producer-template/` scaffold (Section 7) when
wanted; the notes below are the intended manifest and method for each.

### 5a. proposal-content
Manifest: `kind:proposal`, `consumes:[corpus,analysis,audio,insights]`, `human_gate:true`,
`needs_reference:false`, `output_status:proposed`.

A producer that reads the corpus and analysis and writes only through the hub API — it never scrapes and
never touches another agent's files. Method: pull `factors`/`brief`/top exemplars plus their blueprints
(`virality_formula`, `global_style`, `audio_strategy`) and the shared insights; generate **N original
script proposals** (not clones) grounded in the winning factors, each as `<date>-proposal-<slug>.md` with
`status:"proposed"`. It presents multiple distinct angles per topic with a short rationale tying each to a
factor or insight, and includes the `## Audio` block. Because `human_gate:true` it never finalizes — a
human approves in the Dashboard. One shared insight per run; its own `memory/` persona and patterns; JSONL
logs; resume-safe.

### 5b. creative-idea
Manifest: `kind:idea`, `consumes:[corpus,analysis,audio,insights]`, `human_gate:false`,
`needs_reference:false`, `output_status:proposed`.

Same hub-only contract and registration. Method: synthesize **net-new viral concepts** — not clones, not
tied to one exemplar — by cross-referencing `factors` (which levers correlate with reach), the
`virality_formula`/`retention_devices` across many blueprints, trending audio buckets
(`GET /api/audio/{p}/trending`), and shared insights. Output is idea cards `<date>-idea-<slug>.md`
(`status:"proposed"`): concept, why-it-could-pop (grounded in specific factors/insights), hook, format,
suggested trending sound plus `## Audio` block, and a rough beat outline a downstream clone or proposal
agent could execute.

### 5c. template-content — the reference-driven one
Manifest: `kind:template`, `consumes:[corpus,analysis,audio,insights,reference_blueprint]`,
`human_gate:false`, `needs_reference:true`, `output_status:proposed`.

The only agent that requires external reference material (`needs_reference:true`). Input flow: the operator
provides a reference/template video via `POST /api/reference/{p}`; the hub downloads it and AnalysisEngine
analyzes it into a blueprint with `is_reference:true`. Method: read the chosen reference blueprint
(`GET /api/reference/{p}` → `GET /api/analysis/{p}/{ref_id}`) for its structure, style, `shots` and
`regeneration_guide`, then produce content applying that template to the operator's supplied topic,
grounded additionally in `factors`/`insights`. Output `<date>-template-<slug>.md` (`status:"proposed"`)
carries the template mapping (which reference beat maps to which new beat), the `## Audio` block, and
assembly notes. With no reference blueprint available it stops and asks the operator to POST a reference
first.

---

## 7. Adding a new producer later (the copy-scaffold recipe)

`_producer-template/` — copy it to a new dir, fill the blanks, and it plugs in:
```
_producer-template/
  CLAUDE.md          # identity, prime directive (hub-only, never scrape), the Producer SPI (§3), method stub
  agent.json         # the manifest — name/kind/consumes/human_gate/needs_reference/config_schema/secrets
  logsetup.py        # shared logging convention (§10.1): local JSONL + POST /api/logs lifecycle events
  memory/
    MEMORY.md        # index
    persona.md       # this agent's voice
    patterns.md      # learned what-works rules (append over time)
  .claude/settings.local.json   # narrow allowlist: hub health check only
  .env.example       # declares the env-var NAMES this agent's secrets use (§10.4) — never real values
  .gitignore         # ignores .env, logs/, __pycache__/
```
On startup a producer: reads bootstrap env (`BACKEND_API`, `AGENT_NAME`) → registers (`POST /api/producers/register`
incl. `config_schema`+`secrets`) → fetches its config (`GET /api/config/agent/{name}`) → reports secret status →
runs, self-evaluating each output (§10.2) and emitting lifecycle logs (§10.1).
To add an agent: `cp -r _producer-template <NewAgent>`, edit `agent.json` + CLAUDE.md method, open a Claude
session there. On first run it registers with the hub and shows up in the Dashboard producers lane. Nothing
else in the pipeline changes — that is the definition of "replaceable."

---

## 8. Honest limitations to keep visible
- **Trending audio = rising within YOUR tracked creators**, not the true platform-wide chart (MVP derives it
  from scraped reels). Broaden later with a dedicated trending-audio scraper. Don't present it as global.
- **Human gate is lightweight** (a status field + Dashboard buttons), not a workflow engine. Sufficient for a
  single operator; revisit if multiple reviewers/roles appear.
- **Reference ingestion reuses AnalysisEngine**, so a template video costs one analysis pass before the
  template agent can run — surface that in the Dashboard intake UI so it's not a silent wait.
- **proposal vs idea overlap:** both generate original content; kept separate per your naming and for distinct
  memory/voice. If they converge in practice, fold `idea` into `proposal` as a mode rather than maintaining two.

---

## 9. Dashboard frontend — consolidated spec

"The Cutting Room": React 18 + TS + Vite + Tailwind v4 + TanStack Query/Virtual + Framer Motion + Recharts.
Stays same-origin with the hub in prod (`BASE=""`); dev proxies `/api` + `/media`. Preserve every existing
convention. **Invoke the `frontend-design` skill before writing UI.**

### 9.1 Types + api client (`src/lib/types.ts`, `src/lib/api.ts`)
Add interfaces mirroring the live hub (verify against real responses, per the existing "mirror the contract"
rule — not just OpenAPI):
- `Blueprint` — schema_version 2: `video_metadata, global_style, audio, audio_strategy,
  characters_and_subjects[], text_overlays[], shots[]` (with `generation_prompt`/`negative_prompt`),
  `regeneration_guide, virality_formula, evaluation, is_reference?`.
- `TrendingSound` — `{audio_id, title, artist, is_original, is_reusable, sound_page_url, uses_in_corpus,
  trend_score, bucket, example}`.
- `Producer` — `{name, kind, consumes[], human_gate, needs_reference, produces, output_status}`.
- Extend `Reel` with the audio fields + `analyzed:boolean`; extend `Proposal` with `agent, kind, status`.
Add typed calls: `GET /api/analysis/{p}[/{id}]`, `GET /api/audio/{p}/trending`, `GET /api/audio/{p}/sound/{id}`,
`GET /api/producers`, `GET /api/studio/{p}?status=&agent=`, `POST /api/studio/{p}/{file}/status`,
`POST /api/reference/{p}`, `GET /api/reference/{p}`.

### 9.2 Navigation + views (`src/views/`, Sidebar)
Keep Dashboard, Corpus, Studio, Playbook, Config. **Add four sidebar entries: Sounds, Producers, Activity, Evals**
(Activity = the live agent log timeline §10.1; Evals = score trends §10.2). Per-agent **Config** (schema-driven
form from `config_schema`, `PUT /api/config/agent/{agent}`) and **Secrets status** (present/absent chips, never
values) live inside each ProducersView card. Do NOT add a top-level Blueprint view — it would duplicate the virtualized grid AND the blueprint renderer. Instead: the
full blueprint lives in `ReelModal` (§9.4), and cross-clip browsing/QC is served by a **facet on the existing
Corpus grid** — an `analyzed` filter + a sortable `evaluation.score` column/badge, so "which clips are
analyzed / which scored low / need re-analysis" is answerable in the list you already have (doubles as the QC
view for AnalysisEngine's self-eval loop). New/changed views:
- **StudioView (change):** group proposals by `agent`; show a `status` chip; Approve/Reject buttons
  (`POST /api/studio/{p}/{file}/status`). This is the human gate. *(As built, StudioView is two tabs —
  `Proposals` and `Renders`. The originally-planned "Ready to post" list of approved items was superseded by
  the **Renders** tab: an approved item is rendered into a playable reel via
  `POST /api/studio/{p}/{file}/render`, and its `## Audio` attach instruction shows in the render's Post-kit
  card. See [Producers & SPI → Render](../agents-producers.md).)*
- **SoundsView (new):** trending-sound table (title, artist, `trend_score`, `bucket` chip, uses, reusable flag,
  example reel with `/media` preview). Reuse the existing table + gauge components; `bucket` uses color
  semantics (Rising/Hot emphasized, Saturated muted).
- **ProducersView (new):** render lanes from `GET /api/producers` — one card per producer (name, `kind`,
  `human_gate`, `needs_reference`, `consumes`), plus its recent studio outputs. New producers appear
  automatically = pluggability made visible. Include the **Reference intake** panel here (only the template
  agent needs it): a form to `POST /api/reference/{p}` (paste a template URL) + a list of reference blueprints
  with their analysis status. Show the "one analysis pass before it's ready" wait explicitly (§8).

### 9.3 PipelineBoard (`src/components/PipelineBoard.tsx`) — touch the signature motif carefully
The measuring-tape board goes from 5 to **6 nodes**: `Sources → Scrape → Analyze → Media → Blueprint → Studio`.
- The new **Blueprint** node (AnalysisEngine) sits after Media, before Studio. Label it "Blueprint" — NOT
  "Analyze" — so it isn't confused with the existing virality-scoring "Analyze" node. Show its `analyzed`
  count; if the hub exposes the `analysis-engine` stage runner, a Run button wired through the SSE
  `/api/events` job stream like the other stages.
- Keep the measuring-tape rhythm intact at 6 marks (re-space, don't restyle). Do **not** inline producer nodes
  onto the board — the Studio node stays single and links to ProducersView; the producer fan-out lives there.
  Rationale: the linear board reads the pipeline; the fan-out is a separate concern. Preserves the motif.

### 9.4 Component changes (`src/components/`)
- **ReelCard:** add an "analyzed" badge (from `Reel.analyzed`) and a small audio glyph when a trending sound is
  attached.
- **ReelModal:** add a **Blueprint tab** for the selected `content_id` (`GET /api/analysis/{p}/{id}`) rendering:
  shot list with per-shot `generation_prompt`/`negative_prompt` + copy buttons; `regeneration_guide`; character
  sheet; text-overlay timeline; the `evaluation` score; and the **Audio card** (strategy, sound name + artist +
  `sound_page_url` copy button, prominent "attach manually in IG" callout, `beat_markers_s`). Empty state when
  no blueprint yet.
- **Audio card** is a shared component reused in ReelModal and the Studio **Renders** tab (RenderModal).

### 9.5 Live data strategy (`src/lib/hooks.ts`)
Keep the existing SSE `/api/events` for JOB status only. The new resources (producers, studio+status, audio,
blueprints) are plain REST → use TanStack Query with a sensible `staleTime` + refetch-on-focus, and invalidate
the relevant query when a related job (`analysis-engine`, `scrape`, `media`) completes in the SSE stream. Do
NOT try to push these through SSE.

### 9.6 Design constraints (non-negotiable)
Match the cutting-room / measuring-tape / seam / chalk visual language; Tailwind utilities + CSS variables
(`var(--ink-*)`); Framer Motion **entrance-only** animation; dark/tactile aesthetic. Honor enforced color
semantics: keep tier colors; add gate-status semantics (proposed = neutral, approved = signature accent,
rejected = muted). Preserve the ONE signature motif — introduce no competing motif. Use the `frontend-design`
skill to keep the new views feeling native, not like a generic admin panel.

### 9.7 Build/deploy
Parameterize `deploy` via `BACKEND_DIR` (default `../ReelScraper`) → `$BACKEND_DIR/frontend/dist`. Keep
`build = tsc && vite build`.

### 9.8 Acceptance checklist
- Sounds + Producers appear in the sidebar; ProducersView populates from `GET /api/producers`.
- A blueprint renders in the ReelModal Blueprint tab with working prompt copy buttons + Audio card.
- The Corpus grid can filter by `analyzed` and sort by `evaluation.score` (no separate Blueprints view).
- A proposal can be Approved/Rejected; an approved one can be rendered in the **Renders** tab, which shows the reel plus its IG attach line.
- Reference URL can be submitted and the reference blueprint lists once analyzed.
- Pipeline board shows 6 nodes, measuring-tape rhythm intact, Blueprint node distinct from Analyze.
- Activity view streams live agent log events; Evals view charts score trends; per-agent Config form + Secrets
  status chips render from the manifest; data-flow animation runs on live activity and stills under reduced-motion.
- `npm run build` is clean; `deploy` respects `BACKEND_DIR`.

---

## 10. Platform-wide concerns (logging, evaluation, config, secrets, motion)

Cross-cutting: defined once here, wired into every agent + the hub + the Dashboard. Unifying principle — the
hub is the single source, agents are thin HTTP clients, and **secrets never leave the agent**.

### 10.1 Unified agent logging — durable local + observable central
Two tiers, one schema `{ts, agent, run_id, platform, level, event, content_id?, msg, data}`:
- **Local (full fidelity):** every agent uses the shared `logsetup` convention (already in ReelScraper
  `core/logsetup.py`; AnalysisEngine + producers reuse the exact shape): per-run `logs/<ISO-start>_<cmd>.log`,
  pretty console + JSONL. All debug detail lives here.
- **Central (curated):** agents `POST /api/logs` for LIFECYCLE events only — run start/end, per-item done,
  errors, eval scores — never every debug line. `GET /api/logs?agent=&level=&since=&run_id=`; streamed on the
  SSE `log` channel. `run_id` links a hub event back to its local file. This stream feeds the Activity view and
  the data-flow animation (§10.5).

### 10.2 Evaluation pipeline — quality now, outcome later
Three layers, one contract:
- **Self-eval (per artifact):** generalize AnalysisEngine's rubric→judge→score→refine loop (see the design note) into a
  shared convention. Every producing agent scores its own output against a rubric before publishing and stamps an
  `evaluation {score, per_criterion, judge_model, verdict}` block. Blueprint → analysis quality; clone → fidelity
  to the blueprint; proposal/idea → grounded in real factors; all → audio strategy soundness.
- **Eval store (hub):** `POST /api/evals {agent, target_type, target_id, scores, verdict, judge, notes}` /
  `GET /api/evals?…`. Store `evals/<agent>/<id>.json` + `evals.jsonl`. Decouples evaluation from the artifact.
- **Outcome feedback (the "measure" loop — phase 3):** an operator sets `posted_content_id` on an approved studio
  item when they post it; a scheduled job re-scrapes that reel's metrics after N days and scores
  predicted-vs-actual virality → an `outcome` eval. Highest-value signal; ships after self-eval + store because it
  needs the manual-post→reel link. Flag the dependency; don't fake it.
- **Dashboard:** Evals view — per-agent score trends over time (Recharts, already a dep), low-score drill-down
  (re-run/refine), outcome loop once wired.

### 10.3 Configuration — hub-stored, schema-driven, Dashboard-editable
"Everything configurable" without cross-dir file access:
- **Central store:** the hub owns per-agent config `config/agents/<agent>.json`; agents READ it over HTTP at run
  start (`GET /api/config/agent/<agent>`) — like every other input. Edited from the Dashboard via
  `PUT /api/config/agent/<agent>`. Generalizes the existing `GET/PUT /api/config/{platform}`.
- **Schema-driven UI:** each manifest declares a `config_schema` (JSON Schema of knobs + defaults); the Dashboard
  renders a generic form from it — no per-agent UI code, so a new agent is configurable the moment it registers.
- **Bootstrap exception:** only `BACKEND_API` + `AGENT_NAME` come from env (chicken-and-egg); everything else is
  hub config. Existing `niche_config.json`/`image_config.json` migrate into this store (or the hub serves them as
  the platform-scoped slice).
- **Propagation:** read at run start; a running agent uses its snapshot. Live hot-reload is out of scope — flag it.

### 10.4 Secrets — per-agent, env-reference, never centralized
The ONE thing that never enters the hub/config store:
- **Per-agent, local:** each agent keeps its own secrets in a gitignored `.env` (or OS keychain), isolated per
  agent — matches SimilarContent's existing pattern and the safety rules.
- **Reference-by-name:** config/manifest reference secrets by ENV VAR NAME, never value — the existing
  `api_key_env: "GEMINI_API_KEY"` indirection. Manifest declares `secrets:[{name, env_var, required}]`;
  `.env.example` documents the names.
- **Status-only surfacing:** `GET /api/config/agent/<agent>/secrets/status` → `[{name, env_var, present, required}]`
  (the agent self-reports resolvability on registration/heartbeat; the hub NEVER stores a secret). The Dashboard
  shows present/absent chips only — misconfig is visible, values are not.

### 10.5 Data-flow animation — SSE-driven, extends the signature motif
- **Driven by** the SSE `/api/events` stream (jobs + §10.1 log events) — no polling. When a stage/agent acts,
  animate a "packet" traveling the seam between the relevant pipeline-board nodes, carrying the content count.
- **Motif discipline:** this is a NEW continuous motion pattern, so frame it AS the signature — a chalk-thread /
  stitch traveling down the measuring tape — reinforcing, not competing. Active-only (animates only during live
  activity; idle board is still), subtle, **honors `prefers-reduced-motion`** (static pulse/count-bump fallback),
  pauses when the tab is hidden, and must never jank the board. Framer Motion path/dash animation on the existing
  seam connectors, keyed by which stage is live.

### 10.6 Where each concern lives
These are cross-cutting, so they are not one component's job. The hub owns the endpoints. The shared
`logsetup` + self-eval + config-fetch + secret-status convention is implemented by AnalysisEngine,
SimilarContent, the `_producer-template` scaffold and therefore every future producer (§5). The
Activity/Evals views, the Config/Secrets UI and the data-flow animation are the Dashboard's (§9).
§10.1–10.4 ship with their agents and §10.5 with the Dashboard; the outcome loop (§10.2) is still
outstanding — it depends on the manual-post → reel link.

---

## 11. AutoSearch — the discovery agent (the new front door)

**What it is.** `auto-search` (kind `discovery`) is a SOURCE-side agent that searches Instagram (keyword/
creator search, related-creator chaining, guest profile hydration) to **find new creators worth scraping**,
scores them for niche-fit (heuristics + a Claude relevance judgment), and posts them as **candidates** to the
hub. A human approves candidates in the Dashboard → the hub appends the handle to `pages.txt` → the next scrape
ingests them. It closes the loop that is manual today (hand-curated `pages.txt`).

**Pipeline shape becomes 7 stages:**
```
Discover → Sources → Scrape → Analyze → Media → Blueprint → Studio
   ▲ AutoSearch (candidates → human gate → pages.txt)
```

**The build prompt is `AutoSearch/PIPELINE.md`** (self-contained; paste into a session in `/AutoSearch`). This
section is the integration/orchestration view: the SAFETY contract, the cadence model, the hub changes, the
effect on every existing agent, the frontend design, and verification.

### 11.0 The one architectural rule
AutoSearch is, **alongside ReelScraper, the only agent permitted to touch Instagram** — read-only, guest-first,
burner-opt-in, paced strictly slower than the scraper, with a kill-switch. **Producers still never scrape.** It
integrates over HTTP only (`BACKEND_API=http://127.0.0.1:8787`), never imports a sibling's code, and never
writes into another project's directory — the hub (not AutoSearch) appends approved handles to `pages.txt`. The
SAFETY spec in `AutoSearch/PIPELINE.md §1` is a hard contract and is **never weaker than** ReelScraper's
guest-only rule.

### 11.1 Cadence — weekly budget → random daily → heartbeat (anti-bot)
Discovery never runs as a burst. A **weekly budget** is scattered into randomized **daily** allotments (with
rest days), and each day's allotment is executed as a thin trickle across **heartbeat** ticks during organic
hours. Full spec in `AutoSearch/PIPELINE.md §2`. Hub side:
- **Kill-switch (fail-closed):** per-agent config flag `discovery_enabled` (default **false**). The agent and
  the hub scheduler stay idle until the operator turns it on. Checked at run start, between every surface, and
  at the top of every heartbeat; if the hub is unreachable/ambiguous, the agent fails closed and stops.
- **Hub heartbeat scheduler (opt-in, local-first):** a background daemon thread in the hub that — only while
  `discovery_enabled` — fires the `auto-search-beat` stage every `heartbeat_minutes` ± jitter. Most beats
  no-op (organic scatter); a few do a tiny bounded slice. Off by default; alternatives: OS `cron` / a
  `schedule` routine pinging `cli.py beat`, or a manual `cli.py run` pass.

### 11.2 Hub changes (`ReelScraper/api/app.py`) — the only backend edits
Reuse the generic helpers (`_read_json/_write_json/_append_jsonl/_read_jsonl`, `pdir`). Add:
- **Model + constant:** `CandidateIn` (near `ReferenceIn`); `CANDIDATE_STATUSES = {"pending","approved",
  "rejected"}`. `StatusUpdate` reused as-is.
- **`discovery/{platform}/candidates.json`** store (clone the reference-registry pattern) + `_candidates_path`.
  Candidate record: `{candidate_id, handle, platform, source_term, discovered_via, followers, median_plays,
  sample_reels[], relevance:{score,reasons[]}, status, added_at, updated_at, ts}`. `candidate_id` = agent
  value or `"cand_"+sha1(f"{platform}:{handle}")[:10]` (stable → upsert, no dupes).
- **Routes:**
  - `POST /api/discovery/{p}` — ingest one candidate (upsert, force `status:"pending"` on first insert, never
    silently un-gate an approved/rejected one).
  - `GET /api/discovery/{p}` (+ `?status=`) — rows with derived `in_pages` (handle already a non-comment line
    in `pages.txt`), newest-first.
  - `GET /api/discovery/{p}/pending` — the human review queue.
  - `POST /api/discovery/{p}/{candidate_id}/status` `{status, note}` — the gate: mirrors
    `set_studio_status`; on `approved`, append the handle to `pages.txt` via a **new safe, comment-preserving,
    deduped `_append_handle_to_pages`** (append-mode, NOT the whole-file `put_config` overwrite); append a
    `discovery/{p}/gate.jsonl` record with `appended_to_pages`.
- **Board gate-join (edit `agent_board`):** add a `kind=="discovery"` branch that overwrites Approved/Rejected
  lanes from `discovery/{p}/gate.jsonl`, keyed on `content_id` (= candidate_id) — no `data.file` needed. Leave
  the studio branch untouched.
- **Stage runners (`STAGE_CMD`):** `"auto-search"` → `["uv","run","cli.py","run",p]` (manual pass) and
  `"auto-search-beat"` → `["uv","run","cli.py","beat",p]` (heartbeat), both cwd `../AutoSearch`. The generic
  `run_stage`/`_run_job`/SSE machinery streams status for free.
- **Heartbeat scheduler thread + kill-switch** (§11.1).
- Docs: `CLAUDE.md`, `AGENT_PROMPTS.md`, regenerate `/openapi.json`.

### 11.3 Effect on each existing agent
Adding discovery touches surprisingly little, which is the point of the HTTP boundary:

- **ReelScraper hub** — carries the discovery contract (§11.2): the `CandidateIn` model, the
  `discovery/{p}/candidates.json` store, the four `/api/discovery/*` routes, the safe comment-preserving
  `_append_handle_to_pages`, the `agent_board` `kind=='discovery'` gate-join, the `auto-search` +
  `auto-search-beat` `STAGE_CMD` entries, and the opt-in heartbeat scheduler thread with the
  `discovery_enabled` kill-switch. Backward-compatible: every existing route is kept.
- **ReelScraper `discover.py` / `find_profiles.py`** — *no code change.* They carry a "superseded by the
  AutoSearch agent (`/AutoSearch`) — same guest-first/burner-opt-in engines, now Claude-scored and hub-gated;
  these remain for manual/offline use" note at the top of each.
- **AutoSearch itself** — its own build spec is `AutoSearch/PIPELINE.md`; this section is the
  integration view.
- **AnalysisEngine** — *no change.* (Optional later: read the shared trending-terms insight for grounding.)
- **SimilarContent + all producers** — *no code change.* They already read shared insights; AutoSearch's one
  `POST /api/insights` per run (trending terms) is picked up for free. Producers still never scrape.
- **`_producer-template` / future producers** — *no change* (discovery is a distinct kind, not a producer).
- **CLI-parity epic (E4)** — add discovery commands: `discover list [--status]`, `discover approve|reject
  <candidate_id> [--note]`, `discover run|beat <platform>`, and surface the 7th stage in `run <stage>`.
- **Parent `README.md` / docs** — update the pipeline diagram to the 7-stage shape; document the discovery
  endpoints, the candidate → gate → `pages.txt` flow, the guest-first/burner-opt-in safety, and the weekly/
  heartbeat cadence.

### 11.4 Final frontend design (folds into the frontend epics E1–E3)
The registry-driven surfaces light up **automatically** the moment AutoSearch registers — it appears in the
home **Agents** strip, in **ProducersView**, and gets a full **Agent Desk** board with its `workflow_stages`
lanes and a schema-generated config form (incl. the cadence knobs + the `discovery_enabled` kill-switch toggle),
with **zero new UI code** (that is the pluggability the board already provides). On top of that, add:

- **Types/api (`lib/types.ts`, `lib/api.ts`, `lib/hooks.ts`):** `Candidate` interface; `getCandidates(p,
  status?)`, `getPendingCandidates(p)`, `setCandidateStatus(p, id, status, note)`; `useCandidates`,
  `useSetCandidateStatus` (invalidate `["candidates", p]`); extend `Stage` union + `useInvalidateOnJobDone`
  with `auto-search`/`auto-search-beat` → invalidate `["candidates", p]` + `["config", p]` (pages.txt).
- **PipelineBoard → 7 nodes:** `Discover → Sources → Scrape → Analyze → Media → Blueprint → Studio`. New
  **Discover** node FIRST (before Sources), showing the pending-candidate count + a Run button wired to the
  `auto-search` stage via SSE. Re-space the measuring-tape to 7 marks (re-space, don't restyle); keep the ONE
  seam motif. Add `"discover"` to the `Stage` union + `Header.VIEW_TITLE`.
- **DiscoveryView (new sidebar entry "Discover"):** the candidate review gate — cards per pending candidate
  (handle, avatar/initial, followers, median plays, `discovered_via` chip, `relevance.score` gauge +
  `reasons[]`, sample-reel links, an `in_pages` badge) with **Approve → pages.txt / Reject** buttons reusing
  the StudioView human-gate pattern (mutation + invalidation, sage-approve / danger-reject / neutral-pending
  tri-state). A "Recently approved (now scrapable)" section, and a small cadence panel showing the current
  weekly plan / today's target / kill-switch state. Reuse the `Seam`/tape/chalk motif and `var(--ink-*)`
  tokens; entrance-only Framer Motion; reduced-motion + hidden-tab rules honored.
- **Design constraints:** identical to §9.6 — one signature motif, gate-color semantics, WCAG-AA both themes,
  `frontend-design` skill for the new view.

### 11.5 Verification
Per `AutoSearch/PIPELINE.md §7`, all runnable WITHOUT a live IG session or any API key: `status`; the weekly-
plan unit test (deterministic distribution + beat-gating); `synthetic` end-to-end (candidate → events → POST →
board Proposed lane); guest `smoke` (assert no `sessionid`); hub roundtrip (POST → pending → approve→pages.txt
append/dedupe/gate.jsonl → reject purge → 404/400); mocked-Claude unit test. Safety invariants asserted:
guest-only default completes without a session; AutoSearch never writes into `ReelScraper/`; pacing strictly
slower than the scraper; breaker + daily/per-term caps + run-duration cap + kill-switch enforced; the scraper's
guest-only rule untouched.
