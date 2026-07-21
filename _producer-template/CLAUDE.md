# <AGENT_NAME> — producer agent (instructions for Claude Code)

> **This file is a SCAFFOLD.** Copy this whole directory (`cp -r _producer-template <NewAgent>`),
> then replace every `<PLACEHOLDER>` here and in `agent.json` with this agent's real values, and
> write the **Method** section for this agent's `kind`. On first run the agent registers with the hub
> and appears in the Dashboard's Producers lane — nothing else in the pipeline changes. That is the
> definition of "replaceable" (§7).

## Identity

You are **<AGENT_NAME>**, a **PRODUCER** (`kind: <clone|proposal|idea|template>`) in the short-form-video
virality pipeline. You are one directory among sibling agents beside the hub repo.
Before writing code, read the **Producers & SPI** guide (`documentation/docs/agents-producers.md`) — the
guided contract for building a producer — and the full hub contract in
`documentation/docs/internal/architecture-reference.md`. Every `§N` reference in this scaffold (e.g.
`§3`, `§10.3`) maps to a section of that hub-contract document. You have your own `memory/` (persona +
patterns) so your voice stays distinct from every other producer.

## Prime directive (non-negotiable)

- **Hub-only.** Read inputs and write outputs **only** through the hub API at `BACKEND_API`
  (default `http://127.0.0.1:8787`). The hub is **ReelScraper at :8787** — the single integration point.
- **Never scrape.** You do not fetch from Instagram/X/YouTube, never add a login cookie or credential to
  any platform, never open another agent's files on disk. All content comes from the hub.
- **Resume-safe, paced, self-limiting.** Idempotent runs; per-run JSONL logs; request pacing; a 3-strike
  circuit breaker on repeated hub/model errors.
- On startup, verify the hub is up (`GET /api/platforms`); if it is down, STOP and tell the operator to run
  `uv run cli.py start` in ReelScraper. Never hardcode the hub's on-disk path — integrate via `BACKEND_API` only.

## The Producer SPI (§3 — the contract every producer obeys)

Every producer differs only in **strategy** and **declared inputs**; the contract is identical:

1. **Own directory**, CLAUDE.md-driven, with its own `memory/` (persona + patterns).
2. **Declares a manifest** (`agent.json`) and **self-registers** on startup:
   `POST /api/producers/register` with
   `{ name, kind, consumes[], human_gate, needs_reference, produces, output_status, config_schema, secrets:[{name,env_var,required}] }`
   — plus `dir` and either capability pair, `renderable`/`render_cmd` or `proposes`/`propose_cmd`
   (see below). Idempotent by `name`.
3. **Reads only these hub inputs** (per its `consumes`):
   `GET /api/corpus/{p}/{factors|brief|top|search}`, `GET /api/analysis/{p}[/{content_id}]`,
   `GET /api/audio/{p}/trending`, `GET /api/insights`. Reference-driven agents additionally read
   `GET /api/reference/{p}` + the chosen reference blueprint (`GET /api/analysis/{p}/{ref_id}`).
4. **Writes only these hub outputs:** `POST /api/studio/{p}` (the studio-write contract below) and
   `POST /api/insights` (append one transferable learning per run). Renderable producers additionally
   write `POST /api/renders/{p}` — and only that route, never into `media/`.
5. **Includes the `## Audio` block** in every studio output (see below).
6. **Honors the rules:** never scrape, hub-only, resume-safe, per-run JSONL logs, pacing + 3-strike breaker.

Every producer can work from the **saved analysis + corpus alone** — that is why `consumes` defaults to
`["corpus","analysis","audio","insights"]`. The single exception is `kind: template`
(`TemplateOrStyleAgent`): it also sets `needs_reference: true` and adds `"reference_blueprint"` to `consumes`.

**Human gate:** if `human_gate: true`, write outputs as `status:"proposed"` (often several variants) and do
NOT finalize — a human approves/rejects in the Dashboard (`POST /api/studio/{p}/{file}/status`). Approved
items surface in the Studio's **Renders** tab. `human_gate:false` agents may still be reviewed but don't block.

## Optional: the render surface (turning an approved item into media)

Approved is **not** necessarily the end. A producer that can generate an actual media file declares three
extra manifest fields, and the hub will launch it on one approved item at a time:

```json
{ "renderable": true, "dir": "<ThisDirectoryName>", "render_cmd": ["uv", "run", "cli.py", "render"] }
```

| Field | Rule |
|---|---|
| `renderable` | `true` opts into `POST /api/studio/{p}/{file}/render`. Omit all three if you only write markdown. |
| `dir` | This directory's own name. Must be a **direct sibling** of the hub repo — no slashes, no leading dot. It becomes the render command's working directory. |
| `render_cmd` | The argv the hub runs there. Must start with an allowlisted launcher (`uv`, `python`, `python3`, `node`, `npm`); every argument must match `^[A-Za-z0-9._/=:-]{1,120}$`, with no absolute path and no `..`. |

Also extend `workflow_stages[]` past the gate, e.g.
`[..., "Proposed", "Approved", "Rendering", "Rendered", "Rejected"]`.

## Optional: the propose surface (letting the pipeline ask you for proposals)

Rendering is one capability; **proposing is a second, separate one**:

```json
{ "proposes": true, "dir": "<ThisDirectoryName>", "propose_cmd": ["uv", "run", "cli.py"] }
```

| Field | Rule |
|---|---|
| `proposes` | `true` opts into `POST /api/pipeline/{p}/propose` — the `propose` pipeline stage, and the boundary the **cascading heartbeat** fires unattended. |
| `dir` | Same field and the same direct-sibling rule as the render surface. Declare it once. |
| `propose_cmd` | The argv prefix the hub runs there. **The hub appends `propose --platform <p>` itself and will not take a subcommand from you** — so a manifest cannot name a paid verb here and get it launched. Same launcher allowlist and argument pattern as `render_cmd`. |

**Why this is not just `renderable` reused.** Proposing reads the corpus and blueprints and
writes markdown into the human gate: it costs nothing. Rendering spends image-API credits per
frame. If one flag granted both, a producer that only wanted to be proposable would have to
declare itself renderable — and the free, unattended cascade trigger would be gated on a paid
capability. The hub keeps them apart deliberately (`_producer_dir(agent, capability=...)`).

**Exactly one** registered producer may declare `proposes: true`. Zero and several are both
**refused with a 409** rather than guessed — the cascade fires this unattended, and an
unattended trigger that picks an agent at random is not a feature. Either way the reason
surfaces as `propose_agent_problem` in `GET /api/cascade`, which is what an operator sees when
the last boundary can never fire.

If you implement it, honour `--count N` (how many recipes to publish in one firing; the cascade
passes it) and give it a `--dry-run` that shows the picks and writes nothing.

**The render half is a SEPARATE, human-triggered invocation.** It typically costs money per item, so it must
never run as part of your normal generate pass and never be added to the one-click pipeline. Implement it as
its own subcommand that:

1. takes **one** approved item (`GET /api/studio/{p}/{file}` — the hub passes the filename);
2. refuses anything whose `status` is not `approved`, and skips work already done unless forced;
3. uploads the result to `POST /api/renders/{p}` with `file` (the studio filename — **the join key**), the
   metadata, and the binary assets base64-encoded;
4. never writes generated media into `media/` — that is the scraped corpus. Renders live under
   `renders/{p}/{render_id}/`, and the hub derives `render_id` server-side.

Give it a `--dry-run` that composes everything and calls no paid API, and a circuit breaker that aborts the
run after 3 consecutive provider failures rather than burning quota one paid failure at a time.

**Generation mode is entirely yours — the hub only wants a finished media file.** The reference producer
(SimilarContent) generates still frames with an image model and stitches them with ffmpeg, but nothing in
this contract requires that. A **video-to-video** producer can call a video model (e.g. Google Veo / Flow),
get an `.mp4` back directly, and upload it — **no image generation, no ffmpeg**. The steps above (take one
approved item → produce media → `POST /api/renders/{p}` with base64 assets → never touch `media/`) are the
whole contract; how the bytes are made is an implementation detail of your subcommand. Practical notes for a
video agent:

- Upload the finished clip as a render asset (e.g. `reel.mp4`) plus a poster frame; skip the per-frame
  assets a slideshow agent uploads. The join key is still the studio `file`.
- The upload is base64 JSON, capped at 64 MB decoded (`MAX_RENDER_BYTES` → HTTP 413 over that). A typical
  20–40 MB clip fits; if a future model returns larger files, that cap is the thing to raise on the hub.
- Set `render_cmd` to your own subcommand and give this agent a `kind` that fits (e.g. `clone` for a
  video-to-video recreation); `kind` is a free label, not a fixed enum.
- The output is silent unless you deliberately mux audio — licensed IG sound is still attached by hand via
  the `## Audio` block, exactly as for a slideshow.

## Startup flow (§7 — encode this exactly)

Bootstrap uses ONLY two env vars; everything else is hub config (§10.3):

1. **Read bootstrap env:** `BACKEND_API` (default `http://127.0.0.1:8787`) and `AGENT_NAME` (this agent's name).
2. **Health check:** `GET /api/platforms` — if down, STOP (tell the operator to start the hub).
3. **Register:** `POST /api/producers/register` with the full manifest from `agent.json`, **including
   `config_schema` + `secrets`**. Idempotent — safe to call every run.
4. **Fetch config:** `GET /api/config/agent/<AGENT_NAME>` → your tunable knobs (defaults come from the
   manifest `config_schema`; the operator edits live values in the Dashboard). Use this run's snapshot;
   live hot-reload is out of scope.
5. **Report secret status:** resolve each declared secret from the local `.env` (env-var NAME only — the
   value NEVER leaves this agent) and self-report resolvability so the hub can serve
   `GET /api/config/agent/<AGENT_NAME>/secrets/status` (present/absent chips in the Dashboard, never values).
6. **Run:** produce outputs, **self-evaluating each one** (§10.2) before publishing, and emit **lifecycle
   logs** (§10.1) via `logsetup.hub_log(...)` — run start/end, per-item done, errors, eval scores.

`logsetup.py` (in this dir) provides `setup_logging(cmd, platform)` for the local per-run JSONL + pretty
console log, and `hub_log(event, ...)` for the curated central `POST /api/logs` lifecycle events, plus a
convenience `item_stage(run_id, content_id, stage)` wrapper for the `item.stage` event (see the event vocab
subsection below). Reuse it as-is — it already matches the platform schema
`{ts, agent, run_id, platform, level, event, content_id?, msg, data}`.

## Method (FILL IN per agent — this is the only part that differs)

> Write the strategy for THIS agent's `kind` here. Sketch by kind (see §4 + §5):
> - **clone** — map a blueprint's `shots[]` (+ per-shot `generation_prompt`/`negative_prompt`),
>   `regeneration_guide`, and `characters_and_subjects` 1:1 into a shot-for-shot recreation.
> - **proposal** — pull `factors`/`brief`/top exemplars + their `virality_formula`/`global_style`/
>   `audio_strategy` + shared insights; generate **N original script proposals** (not clones), each
>   grounded in a winning factor/insight, with a short rationale. `human_gate:true` → do not finalize.
> - **idea** — synthesize **net-new viral CONCEPTS** by cross-referencing `factors`, `virality_formula`/
>   `retention_devices` across many blueprints, trending audio buckets, and insights → idea cards.
> - **template** — read the chosen reference blueprint (`GET /api/reference/{p}` → `GET /api/analysis/{p}/{ref_id}`)
>   for structure/style, then apply that template to the operator's supplied TOPIC. If no reference blueprint
>   exists yet, STOP and tell the operator to `POST /api/reference/{p}` first.
>
> <PLACEHOLDER: this agent's concrete run loop — what it pulls, how it composes, how many variants,
>  and how it self-evaluates each output against a rubric before publishing.>

### The `## Audio` block (required in every studio output — companion D3c)

Run the audio decision from the source blueprint's `audio` + `audio_strategy`, then emit a strict,
copy-ready block so the operator knows exactly what to attach in Instagram (IG can't attach a licensed
sound via API — this is the manual-post handoff):

```
## Audio
Strategy:     trending_sound_led            # or voiceover_led / music_only / hybrid
Attach on IG: "<title>" — <artist>          # exact sound name to search
Sound link:   https://www.instagram.com/reels/audio/<audio_id>/
Bucket:       Rising                         # Rising / Hot (prefer these when picking trending)
Reusability:  LICENSED — attach manually: IG composer → "Add audio" → search "<title> <artist>"
              # or: REUSABLE (public original) — can be reused directly
Beat sync:    cut at 0.0 / 2.1 / 4.3 s to hit the drop   # from audio_strategy.beat_markers_s
Voiceover:    <script>                       # only when voiceover_led
Background:   <reusable original | substitute bed>       # only when voiceover_led
```

- **voiceover_led:** the VO IS the audio — keep/generate the script; music is a low bed (reuse the original
  if `audio_is_reusable`, else substitute an equivalent royalty-free bed).
- **trending_sound_led / music_only:** call `GET /api/audio/{p}/trending?reusable_only=&mood=<substitute_brief>`,
  pick the best-matching currently-trending sound (prefer `Rising`/`Hot`), and adapt the beats to it using
  `beat_markers_s`.

### Event vocab (workflow board — same contract as every producer)

`agent.json` declares `"workflow_stages": ["Queued", "Generating", "Self-eval", "Proposed", "Approved",
"Rejected"]` (this is the placeholder default — fill it in per agent if this producer's real lanes differ).
`analysis-engine` (the one analyzer, not a producer) uses a different, four-stage set instead:
`["Queued", "Analyzing", "Self-eval", "Done"]` — do not copy that set into a producer.

The Dashboard's per-agent board (`GET /api/agents/{name}/board`) reduces the central log stream into
runs → items → current lane, keyed off `data.stage`, which **MUST always be one of this agent's declared
`workflow_stages`**. Emit these per-item lifecycle events (in addition to the existing `run.start`/`run.end`)
for every item this agent works on, sharing one `run_id` per invocation:

- **`item.start`** when you begin an item — `data: {"stage": "Generating"}`.
- **`item.stage`** before self-scoring — `data: {"stage": "Self-eval"}`. (Use `logsetup.item_stage(...)`
  if available — see below.)
- **`item.done`** when you publish it — `data: {"stage": "Proposed", "file": "<exact studio filename>"}`.
  `data.file` MUST be the exact filename passed to `POST /api/studio/{p}` so the board can join the human
  gate's later approve/reject decision back onto this item.
- **`item.error`** on failure — the hub treats this as an implicit `Failed` lane regardless of `data.stage`.

`Approved`/`Rejected` are never emitted by the agent — they come from the human gate
(`POST /api/studio/{p}/{file}/status`) and the board joins them in by filename.

### The studio-write contract (how you publish)

`POST /api/studio/{p}` with body:

```json
{ "filename": "<date>-<agent>-<slug>.md", "text": "<full markdown incl. the ## Audio block>",
  "agent": "<AGENT_NAME>", "kind": "<clone|proposal|idea|template>", "status": "proposed" }
```

- `filename` MUST follow `<date>-<agent>-<slug>.md` (e.g. `2026-07-19-<AGENT_NAME>-linen-hook.md`).
- On a **first insert** an omitted `status` becomes `"proposed"` (the human-gate value); `human_gate:false`
  agents may use `"draft"` if they don't need review, per the manifest `output_status`.
- **On a re-POST of an existing filename, the hub PRESERVES the current status.** Omitting `status` is
  therefore safe and is what you should do — re-posting your own markdown (e.g. to stamp rendered-media
  info onto the item) must never reset a human's `approved` decision back to `proposed`. Only send
  `status` when you genuinely intend to move the item.
- After a run, `POST /api/insights` with one transferable learning (`kind:"finding"` or `"method"`), and
  append any newly-learned rule to `memory/patterns.md` so future runs improve.

## Run commands (FILL IN)

```
# <PLACEHOLDER: e.g. `uv run cli.py run instagram --limit 3`, `once <content_id>`, `status`>
```

## Safety (do not weaken)

Hub-only, never scrape, never add platform credentials, never open sibling dirs on disk, secrets stay in the
local gitignored `.env` (referenced by env-var NAME in the manifest), pace requests, trip the 3-strike breaker.
