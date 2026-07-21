# AnalysisEngine — design notes

This is a design note kept for reference. It records the canonical **analysis blueprint** schema, the
**hub contract** the engine depends on, and the **audio-intelligence layer** that rides on top of both.
It describes the design as built, so it doubles as a rationale document for why the pieces are shaped the
way they are.

Two decisions anchor everything below: the analysis schema is **superseded and unified** — the rich
blueprint is the single canonical `/api/analysis` document, `schema_version` 2, backward-compatible with
the older lean docs — and **gemini-2.5-pro** runs the automatic self-evaluation/judge pass.

Formerly `AnalysisEngine.build-prompts.md` at the repo root.

---

# Architecture decision — backend/hub location

**The FastAPI hub stays inside `ReelScraper` (not moved to the parent dir).** Rationale: agent decoupling
is provided by the HTTP boundary (`http://127.0.0.1:8787` + `/openapi.json`), NOT by folder location, so
relocating buys no decoupling. The hub is deeply coupled to the scraper's `core/`, per-platform data, and
subprocess pipeline control; a true move would require extracting `core` into a shared package + a neutral
data root, and would re-introduce cross-directory file access. Not worth it until there is a SECOND content
source repo or a hub/scraper deployment split.

**Forward-compat rules** (so a future rename/extraction is a one-line change):

- Agents integrate **only via the `BACKEND_API` URL**. The hub's on-disk path is never hardcoded anywhere.
- The single filesystem coupling is the Dashboard `deploy` script → it is parameterized via a `BACKEND_DIR`
  env var (default `../ReelScraper`) instead of a literal `../ReelScraper/frontend/dist`.
- The repo-root `README.md` documents this: "the backend/hub is `ReelScraper` at `:8787` (it also runs the
  scraper); all agents integrate over HTTP." If the name ever bothers you, rename `ReelScraper → Hub` —
  pure naming, no architecture change.

---

# The AnalysisEngine agent

AnalysisEngine is an agent in a short-form-video virality pipeline. It lives in `AnalysisEngine/`.

## The pipeline it joins

The repo holds one directory per agent, plus the hub:

- **`ReelScraper/`** — the backend **hub**: a FastAPI server at `http://127.0.0.1:8787` (package
  `the-cutting-room`, run with `uv run cli.py start`). It is the **single integration point** for the
  whole system. Every agent reads and writes **only over HTTP `/api/*`** — no agent touches another
  agent's files. Stages: `Sources → Scrape → Analyze(virality scoring) → Media → Blueprint(AnalysisEngine) → Studio` (plus Discover via AutoSearch).
  `content_id` is the universal join key across `content.json`, media files (`<content_id>.mp4/.jpg`),
  and analysis files (`analysis/<platform>/<content_id>.json`). Platforms: `instagram`, `x`, `youtube`.
- **`SimilarContent/`** / **`proposal-content/`** — downstream producer agents that consume the analysis
  output to regenerate/clone videos.
- **`Dashboard/`** ("The Cutting Room") — a React control board that surfaces everything from the hub.

An earlier throwaway prototype proved the concept (upload a reel to the Gemini File API, analyze with
`gemini-2.5-pro`, emit a rich analysis document) but was hardcoded, had no memory and no hub integration;
AnalysisEngine supersedes it, and the defects it exhibited are captured as design constraints below.

**Position in the pipeline:** immediately after Media. AnalysisEngine consumes the top-ranked clips that
already have local media downloaded, produces a rich, generation-ready **analysis blueprint** per clip, and
writes it back to the hub. It feeds the producer agents and the Dashboard.

## Prime directive (non-negotiable)

The corpus and clips are read, and results written, **only through the hub API**. The engine never scrapes.
It never opens another project's files. It never adds login cookies or credentials to any platform. On
startup it verifies the hub is up (`GET /api/platforms`); if the hub is down it stops and tells the operator
to run `uv run cli.py start` in `ReelScraper/`.

## Environment

- `BACKEND_API` (default `http://127.0.0.1:8787`) — the hub base URL.
- `GEMINI_API_KEY` (also accepts `GEMINI_KEY`/`GOOGLE_API_KEY`) — required. Read from env only; never
  hardcoded, and secrets are never committed.
- Matching the ecosystem: **Python ≥3.10, `uv`-managed** (`pyproject.toml` + `uv.lock`; console script
  `analysis-engine = "cli:main"`). Dependencies stay minimal. The Gemini REST API is used via
  `urllib`/`httpx` (mirroring the existing stack — no heavy SDK unless it earns its place), plus
  `jsonschema` (validation) and `yt-dlp` (only as a fallback re-download path; the hub's local media is
  preferred).

## Project layout

```
AnalysisEngine/
  cli.py                     # entry point: `uv run cli.py run <platform> [filters...]`, `once <content_id>`, `status`
  engine/
    __init__.py
    hub.py                   # typed hub client (fetches /openapi.json FIRST, built strictly against it)
    gemini.py                # File API upload (+ expiry handling), generateContent, JSON-mode calls
    analyze.py               # compose system prompt → analyze → validate → returns rich blueprint
    evaluate.py              # the automatic self-evaluation / judge pass (gemini-2.5-pro)
    memory.py                # load/assemble/distill the markdown memory layer
    schema.py                # the canonical rich blueprint JSON Schema (schema_version 2) + validator
    circuit.py               # 3-strike circuit breaker + pacing (reuses ReelScraper's conventions)
    logsetup.py              # per-run logs/<start>_<cmd>.log, pretty console + JSONL (matches ReelScraper)
  memory/
    MEMORY.md                # index page linking the memory files (Claude-style)
    system_prompt.base.md    # stable base instruction (director/cinematographer/prompt-engineer role + schema rules)
    rubric.md                # the evaluation rubric the judge scores against
    patterns.md              # LEARNED do/don't rules distilled from past evaluations (auto-appended, deduped)
    <platform>/notes.md      # per-platform craft notes (instagram/x/youtube) — separate because IG≠X≠YT
  CLAUDE.md                  # agent identity, prime directive, run commands, memory model, safety
  README.md                  # quick start
  pyproject.toml
  .gitignore                 # venv/, __pycache__/, logs/, *.egg-info/, .DS_Store, work/ (temp downloads)
```

There is exactly ONE `CLAUDE.md`, at the agent root. The `memory/` folder holds the operational markdown
memory only — there is deliberately no second `CLAUDE.md` inside `memory/`.

## The canonical analysis blueprint (schema_version 2)

This is the **single canonical** analysis document. It is a superset: it keeps the rich generation-ready
sections AND embeds the lean retention/formula fields the hub's `brief` endpoint reads, so nothing
downstream breaks. The engine emits exactly this top-level shape and enforces it with `jsonschema`:

```
schema_version: 2
content_id:            REQUIRED — the hub join key (from the pending queue, NOT invented)
url, model, analyzed_by: "AnalysisEngine"
video_metadata:        { estimated_duration_seconds, aspect_ratio, resolution, fps, content_type,
                         one_line_summary, detailed_summary, target_platform, likely_ai_generated,
                         ai_generation_signals[], total_shots }
global_style:          { overall_mood, genre, visual_style, color_grading, dominant_color_palette_hex[],
                         lighting_style, pacing, editing_style, recurring_visual_motifs[], film_look_reference }
audio:                 { music_description, music_genre, tempo_bpm_estimate, music_mood, has_voiceover,
                         voiceover_transcript (VERBATIM), has_lyrics, lyrics_transcript, sound_effects[],
                         audio_sync_notes }
characters_and_subjects[]: { id (stable, e.g. "character_1"), role, detailed_appearance, appears_in_shots[] }
text_overlays[]:       { start_time, end_time, text (VERBATIM), font_style, color, position, animation }
shots[]:               { shot_index, start_time, end_time, duration, description, subjects_present[],
                         setting_location, action_motion, camera_shot_size, camera_angle, camera_movement,
                         lens_feel, lighting, color_palette_hex[], mood, on_screen_text,
                         transition_in, transition_out,
                         generation_prompt (self-contained, ready-to-run text-to-video prompt),
                         negative_prompt }
regeneration_guide:    { recommended_models[], master_style_prompt, global_negative_prompt,
                         consistency_notes, assembly_instructions,
                         shot_prompt_sequence[]  # MUST be the FULL prompt text per shot, IN ORDER —
                                                 # never placeholder strings like "shot_1_generation_prompt" }
virality_formula:      # the LEAN block the hub `brief` endpoint consumes — always populated:
                       { hook: {type, first_seconds, on_screen_text},
                         retention_devices[], pacing: {cuts, avg_shot_len_s},
                         cta: {present, text}, replicable_formula (one-paragraph recipe), tags[] }
evaluation:            # stamped by the self-eval pass (see below)
                       { score_0_100, per_criterion:{...}, passes:int, gaps_remaining[], accepted:bool }
```

## The run loop (per clip)

1. **Pull work** from the hub's filtered pending queue (filters below). Each item gives `content_id`,
   `url`, `video_url` (local hub media URL), `duration_s`, `virality_score`, `tier`, `caption`. Resume is
   automatic: analyzed clips drop off the queue.
2. **Assemble the effective system prompt** = `system_prompt.base.md` + the current top-ranked lessons
   from `patterns.md` + the relevant `memory/<platform>/notes.md`. This is the "automatic system-prompt
   evaluation" contract: the prompt is composed from evolving memory every run, never static.
3. **Ensure the video is in the Gemini File API.** The hub's local media (`video_url`) is downloaded to a
   temp `work/` file, then uploaded to the File API to get a fresh URI. The ~48h File API expiry is handled
   by re-uploading on `PROCESSING`/expired/404 — a URI is never hardcoded. Temp files are cleaned up.
4. **Analyze** with `gemini-2.5-pro` (`responseMimeType: application/json`, temperature ~0.4,
   `maxOutputTokens` high enough to avoid truncation, ~64k), returning the rich blueprint.
5. **Validate** against the schema (`jsonschema`). On failure or truncation → a targeted **repair pass**
   feeding the validator errors back to the model.
6. **Automatic self-evaluation (gemini-2.5-pro judge).** The analysis is scored against `rubric.md`:
   every shot has start/end/duration and a self-contained `generation_prompt` + `negative_prompt`;
   `shot_prompt_sequence` contains FULL prompts in order (NO placeholders); voiceover/overlay text is
   verbatim; hex palettes present; character IDs stable and consistency notes concrete;
   `virality_formula` populated; schema valid. The judge returns `{score, per_criterion, gaps[]}`.
7. **Refine loop:** if `score < THRESHOLD` (e.g. 85) or any hard-fail criterion trips, a refine pass feeds
   the gaps back into the analysis prompt. Capped at N passes (e.g. 3). The final result is stamped into
   the `evaluation` block.
8. **Write to the hub:** `POST /api/analysis/<platform>` with the full blueprint (the hub extends
   `VideoAnalysisIn` to accept schema_version 2; `content_id` is required and must match the queue item).
9. **Distill memory:** if the judge surfaced a NEW generalizable lesson, a deduped rule is appended to
   `patterns.md` (and platform notes) so future prompts auto-improve. One transferable finding is appended
   to the shared exchange via `POST /api/insights` (`kind: "method"` or `"finding"`).
10. Every step is logged to `logs/<start>_<cmd>.log` (JSONL + pretty). Requests are paced; the 3-strike
    circuit breaker trips on repeated Gemini/hub errors.

## Hub API consumed (built against `/openapi.json`, fetched first)

- `GET /api/platforms` — health + `analyzed` counts.
- `GET /api/analysis/<platform>/pending` — ranked queue of clips WITH local media but NO analysis. **This
  is where the analysis-focused filters live** (see the hub contract below). Supports
  `min_score`, `tier`, `min_duration`/`max_duration`, `content_type`, `limit`, and `reanalyze=<content_id>`
  / `stale=true` (re-run when `schema_version` is old). Polled to know what to analyze next.
- `GET /api/corpus/<platform>/factors` and `GET /api/corpus/<platform>/brief?q=` — optional context to
  ground the analysis (what's winning on this platform) — read-only.
- `GET /api/analysis/<platform>` and `.../<content_id>` — read existing blueprints (for `reanalyze`/diff).
- `POST /api/analysis/<platform>` — write one blueprint.
- `POST /api/insights` — append a transferable learning to the shared exchange.

## Design constraints learned from the earlier prototype

The prototype's defects are the reason several rules above are normative:

- **Dead validation:** the prototype had a full `SCHEMA` dict that was never used. AnalysisEngine actually
  validates with `jsonschema`, with a repair pass on failure.
- **Placeholder `shot_prompt_sequence`:** the prototype degraded this to `"shot_1_generation_prompt"`
  strings. AnalysisEngine emits the FULL prompt text per shot, in order — the judge hard-fails placeholders.
- **Hardcoded, expiring file URI:** never hardcoded; the engine uploads fresh and handles expiry/resume.
- **No memory / static prompt:** AnalysisEngine composes the system prompt from evolving markdown memory
  and self-evaluates every run.

## Conventions matched (from ReelScraper's ecosystem)

`uv` + `pyproject.toml`; per-run `logs/<start_time>_<command>.log` in pretty console + JSONL; `content_id`
as the join key; automatic resume; 3-strike circuit breaker + request pacing; module docstrings; env-var
secrets only; snake_case JSON. `ReelScraper/CLAUDE.md` and `ReelScraper/AGENT_PROMPTS.md` (section D is the
video-analysis spec) are the reference documents, and the hub client is built strictly against
`/openapi.json`.

## Definition of done

- `uv run cli.py run instagram --min-score 70 --limit 3` pulls the queue, produces 3 schema-valid
  blueprints (each self-evaluated ≥ threshold, no placeholders), and they appear via
  `GET /api/analysis/instagram`.
- Re-running is idempotent (analyzed clips are skipped; `reanalyze`/`stale` re-runs them).
- `memory/patterns.md` gains at least one distilled lesson, and a shared insight is posted.
- A short `README.md` documents the run commands and the memory/self-eval loop.

---

# Hub + downstream contract changes

AnalysisEngine produces a rich, generation-ready analysis blueprint and writes it to
`POST /api/analysis/<platform>`. The hub and the downstream agents adapt to that contract. Everything stays
**backward-compatible** — existing lean analyses must still load.

## ReelScraper (the hub) — the extended contract

1. **`VideoAnalysisIn` (`api/app.py`) is extended to schema_version 2.** It gains optional nested models for
   `video_metadata`, `global_style`, `audio`, `characters_and_subjects[]`, `text_overlays[]`, `shots[]`
   (with `generation_prompt`/`negative_prompt`), `regeneration_guide`, `virality_formula`, and
   `evaluation`. `content_id` stays required and `model_config={"extra":"allow"}` is kept. A
   `schema_version: int = 1` field is added; the hub still stamps `platform` + `analyzed_at`. Old lean docs
   (no `schema_version`) must still validate and load.
2. **Analysis-focused filters on `GET /api/analysis/<platform>/pending`.** Query params:
   `min_score`, `tier`, `min_duration`, `max_duration`, `content_type`, `limit`, plus `reanalyze=<content_id>`
   and `stale=true` (surfacing clips whose stored blueprint has an older `schema_version`). The default
   behavior (unanalyzed clips with local media, ranked by virality) is unchanged when no filters are passed.
3. **`brief` endpoint:** the "Visual formulas" assembly reads `virality_formula`
   (`hook` / `retention_devices` / `replicable_formula`) from schema-2 docs, and still falls back to the
   old lean fields for legacy docs.
4. **`GET /api/platforms`:** the `analyzed` count reflects any doc present (v1 or v2).
5. **Pipeline node:** where stage runners are exposed, an `analysis-engine` stage route lets the
   Dashboard trigger it, mirroring the existing scrape/analyze/media subprocess pattern (it shells out
   to AnalysisEngine's `uv run cli.py run <platform>`).
6. `CLAUDE.md`, `AGENT_PROMPTS.md` (an AnalysisEngine section / section D updated to the v2 schema), and
   `analysis/README.md` document schema_version 2 and the new filters. `/openapi.json` is regenerated so
   downstream agents build against it.
7. `content_id` as the join key and the `analysis/<p>/<content_id>.json` file layout are not broken.

## SimilarContent (producer) — consuming the blueprints

The upstream AnalysisEngine publishes rich, generation-ready blueprints per clip via the hub. The
producer's `CLAUDE.md` reflects this:

1. API surface adds `GET /api/analysis/<platform>` and `GET /api/analysis/<platform>/<content_id>`
   — the canonical blueprint (schema_version 2) keyed by `content_id`.
2. In the "method": when cloning an exemplar, if a blueprint exists for its `content_id`, it is preferred as
   the source of truth. The blueprint's `shots[]` (with `generation_prompt`/`negative_prompt`),
   `regeneration_guide` (`master_style_prompt`, `global_negative_prompt`, `consistency_notes`,
   `assembly_instructions`, ordered `shot_prompt_sequence`), and `characters_and_subjects` map directly into
   the shot-for-shot output — instead of re-deriving beats from scratch.
3. The image-provider render flow and the `virality_formula` block for hook/retention framing are kept, as
   are the `<date>-similar-<slug>.md` studio filename convention and the "never scrape, hub-only" rule.
4. Per-shot `negative_prompt`s and `global_negative_prompt` inform the image prompts.

## Dashboard ("The Cutting Room") — surfacing AnalysisEngine

The AnalysisEngine stage sits after Media and produces rich analysis blueprints. The Dashboard surfaces it:

1. **Types (`src/lib/types.ts`, `src/lib/api.ts`):** a `Blueprint` interface mirrors the hub's
   schema_version 2 (`video_metadata`, `global_style`, `audio`, `characters_and_subjects`, `text_overlays`,
   `shots` with per-shot prompts, `regeneration_guide`, `virality_formula`, `evaluation`), plus typed calls
   `GET /api/analysis/{platform}` and `GET /api/analysis/{platform}/{content_id}`. These are verified
   against real responses, per the existing "mirror the live hub contract" convention.
2. **Pipeline board (`src/components/PipelineBoard.tsx`):** a node **after Media, before Studio**,
   labelled distinctly (e.g. "Blueprint" / "AnalysisEngine") so it isn't confused with the existing
   "Analyze" (virality-scoring) node. It shows the `analyzed` count and, where the hub exposes the stage
   runner, a Run button that POSTs the analysis-engine stage (wired through the SSE `/api/events` job stream
   like the other stages).
3. **A blueprint view:** either an extension of `ReelModal` or a separate view that, for a selected
   `content_id`, renders the blueprint — shot list with per-shot `generation_prompt`/`negative_prompt`
   (copy buttons), the `regeneration_guide`, the character sheet, text-overlay timeline, and the
   `evaluation` score.
4. It reuses the existing "cutting room / measuring-tape / seam / chalk" visual language, Tailwind + CSS
   variables, TanStack Query, and Framer Motion entrance animations. The `npm run deploy` step is kept, but
   its target is parameterized via a `BACKEND_DIR` env var (default `../ReelScraper`) →
   `$BACKEND_DIR/frontend/dist`, so a future hub rename/move is a one-line change (per the architecture
   decision at the top).

---

# Audio / music intelligence layer

This layer applies on top of the two sections above. Decisions locked: **fold into the existing three
touchpoints** (no new agent) and **derive "trending now" from the audio metadata already embedded in
scraped reels** (MVP; a dedicated trending-audio scraper is a later upgrade). Three jobs:
**collect+score sounds (hub)** → **decide per-clip strategy (AnalysisEngine)** → **pick sound + emit
manual-post instruction (producers + Dashboard)**.

## Shared audio data model

Per-reel audio fields (extracted by ReelScraper from `reels_raw.json` — IG exposes
`clips_metadata.music_info.music_asset_info` for licensed tracks and `original_sound_info` for original
audio):

```
audio_id            # IG audio_cluster_id (licensed) or audio_asset_id (original) — the join key for sounds
audio_title         # track title / original_audio_title
audio_artist        # display_artist / original creator username
audio_is_original    bool  # original audio vs a licensed/commercial track
audio_is_reusable    bool  # can you legally reuse it (public original) vs licensed (attach manually only)
sound_page_url      # https://www.instagram.com/reels/audio/<audio_id>/
audio_uses_count    # reels-using count if present in metadata (else null)
```

Trending corpus (computed in the hub, exactly like virality scoring): aggregate by `audio_id` across recent
scraped reels; `sound_trend_score` = adoption velocity (distinct recent reels using it, recency-weighted,
scaled by those reels' virality). Buckets: `Rising | Hot | Saturated | Evergreen`. A representative viral
reel is kept per sound.

## ReelScraper (hub) — collecting and scoring sounds

The hub stays in `ReelScraper/` — see the architecture decision above.

1. **`normalize.py`:** extracts the per-reel audio fields above from `music_info` / `original_sound_info`
   and adds them to `core.schema.Content` (+ `content.json` records + the CSV/xlsx as appropriate).
   Everything is null-tolerant (older raws may lack them).
2. **New signal + corpus (`core/`):** a `sound_trend_score` computation (velocity of adoption across
   recent reels, weighted by recency and the using-reels' `virality_score`) and a `bucket`, mirroring the
   percentile/normalization style in `core/virality.py`.
3. **New endpoints (`api/app.py`):**
   - `GET /api/audio/<platform>/trending?window=14d&limit=50&reusable_only=&mood=&min_trend=` → ranked
     sounds: `{audio_id, title, artist, is_original, is_reusable, sound_page_url, uses_in_corpus,
     trend_score, bucket, example:{content_id,url,virality_score}}`.
   - `GET /api/audio/<platform>/sound/<audio_id>` → sound detail + reels using it.
   - The audio fields are added to `GET /api/content/<platform>` rows.
   - Optionally an `audio_type` / `sound_freshness` bucket in `factors` (does voiceover-led vs
     trending-sound-led correlate with virality?).
4. `CLAUDE.md`, `AGENT_PROMPTS.md` and `/openapi.json` document that `audio_id` is the sound join key
   (parallel to `content_id`).

## AnalysisEngine — audio strategy in the blueprint

This extends the schema and run loop described above.

1. **The `audio` block is enriched** by passing through the hub's audio fields for the clip (from the
   pending queue item): `audio_id, audio_title, audio_artist, audio_is_original, audio_is_reusable,
   sound_page_url`. Gemini can't read IG metadata; the hub supplies it — the model fills the rest from
   the actual audio track.
2. **A new top-level `audio_strategy` block** is inferred by the model from the video+audio:
   ```
   audio_strategy: {
     audio_type: "voiceover_led" | "trending_sound_led" | "music_only" | "hybrid",
     voiceover_role, music_role,
     beat_markers_s[],            # timestamps where cuts sync to the beat (for beat-matching a substitute)
     reuse_recommendation: "reuse_original" | "substitute_equivalent" | "pick_trending",
     substitute_brief,            # mood/genre/tempo/energy to match if substituting
     sync_notes
   }
   ```
3. **Rubric addition** (`memory/rubric.md` + judge): `audio_strategy.audio_type` is set, `beat_markers_s`
   present when `trending_sound_led`, and `voiceover_transcript` is verbatim when `voiceover_led`.

## SimilarContent (+ proposal-content) — decision and Instagram manual-post handoff

Before finalizing a clone's studio markdown, the producer runs the **audio decision** from the exemplar's
blueprint `audio` + `audio_strategy`:

- **voiceover_led:** the voiceover IS the audio — keep/generate the VO script. Music is a low bed: reuse
  the original if `audio_is_reusable`, else substitute an equivalent royalty-free bed. Output the VO
  script + bed choice.
- **trending_sound_led / music_only:** call
  `GET /api/audio/<platform>/trending?reusable_only=&mood=<substitute_brief>` and pick the best-matching
  **currently trending** sound (prefer `Rising`/`Hot`). **Adapt the script's beats to that sound** using
  `beat_markers_s` — align the hook and cuts to its structure.

It then emits a strict, copy-ready **`## Audio` block** — the "tell me what to add on Instagram"
deliverable:

```
## Audio
Strategy:     trending_sound_led            # or voiceover_led
Attach on IG: "<title>" — <artist>          # exact sound name to search
Sound link:   https://www.instagram.com/reels/audio/<audio_id>/
Bucket:       Rising                         # Rising / Hot
Reusability:  LICENSED — attach manually: in the IG composer tap "Add audio" → search "<title> <artist>"
              # or: REUSABLE (public original) — can be reused directly
Beat sync:    cut at 0.0 / 2.1 / 4.3 s to hit the drop
Voiceover:    <script>                       # only when voiceover_led
Background:   <reusable original | substitute bed>   # only when voiceover_led
```

One shared insight (`kind: "finding"`) is posted about which sound bucket / audio_type is winning, so it
feeds the corpus memory. The `<date>-similar-<slug>.md` filename and hub-only rules are kept.

## Dashboard — surfacing sounds and the attach instruction

1. **Types/api:** a `TrendingSound` interface + `GET /api/audio/{platform}/trending` and
   `.../sound/{audio_id}` calls.
2. **A "Sounds" sidebar view:** a trending table (title, artist, trend_score, bucket, uses, reusable
   flag, example reel with preview link), reusing the existing table/gauge components.
3. **Audio card** on the blueprint view and on each studio proposal: strategy, sound name +
   artist + `sound_page_url` (copy button), a prominent "attach manually in IG" callout, and the
   `beat_markers_s` sync markers, matching the cutting-room visual language.
4. No new pipeline-board node is needed — audio rides on the existing Scrape/AnalysisEngine/Studio stages;
   Sounds is just a new view.

## Why this shape

`audio_id` becomes a second join key alongside `content_id`; trending is derived from data already
scraped (zero new scraping surface, respecting the safety rules); the reuse-vs-trending branch lives with the
producer that actually writes the post; and because IG can't attach a licensed sound via API, the concrete
output is a copy-ready "search this exact name" instruction in both the studio markdown and the Dashboard.
