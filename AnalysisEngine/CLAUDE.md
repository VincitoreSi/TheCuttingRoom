# AnalysisEngine — agent identity + rules (for Claude Code)

**What this is.** AnalysisEngine is the pipeline's **analyzer** (the stage right after Media). It
watches the top-ranked short-form clips that already have local media, uses **Google Gemini**
(`gemini-2.5-pro`) to break each one down **shot by shot**, and writes a rich, generation-ready
**schema_version 2 blueprint** back to the hub for the producer agents and the Dashboard to
consume. It replaced an early throwaway prototype (`VideoAnalysis`), which has since been deleted.

There is exactly ONE `CLAUDE.md` — this one, at the repo root. The `memory/` folder holds the
operational markdown memory (composed into the system prompt each run); it is not a second
identity file.

## Prime directive (non-negotiable)
Read work and write results **only through the hub API** (`BACKEND_API`, default
`http://127.0.0.1:8787`). Never scrape. Never open another project's files. Never add login
cookies/credentials to any platform. On startup verify the hub is up (`GET /api/platforms`); if it
is down, stop and tell the operator to run `uv run cli.py start` in ReelScraper.

## Run commands
```
uv sync                                  # create .venv + install deps (jsonschema, yt-dlp)
uv run cli.py status                     # hub health + analyzed counts + secret status + config
uv run cli.py run instagram --limit 2    # analyze the pending (+ reference) queue for a platform
uv run cli.py run instagram --min-score 70 --limit 3 --tier S
uv run cli.py once <content_id>          # analyze / re-analyze a single clip
```
Filters on `run` mirror the hub's `GET /api/analysis/{p}/pending`: `--min-score`, `--tier`,
`--min-duration`, `--max-duration`, `--content-type`, `--limit`, `--stale`, `--reanalyze`, plus
`--no-references` / `--references-only` for the reference queue.

## To fully validate a live run the operator must
1. `export GEMINI_API_KEY=...` (or `GEMINI_KEY` / `GOOGLE_API_KEY`), and
2. download media in ReelScraper: `uv run download_media.py instagram`,
then: `uv run cli.py run instagram --limit 2`. Without a key and local media there is nothing to
analyze — `status` reports exactly what is missing.

## The blueprint (schema_version 2) — the shared substrate
The single canonical analysis doc (superset). Enforced by `engine/schema.py` with `jsonschema`
plus semantic checks. Top-level: `schema_version, content_id, url, model, analyzed_by,
video_metadata, global_style, audio, audio_strategy, characters_and_subjects[], text_overlays[],
shots[]` (each with a self-contained `generation_prompt`/`negative_prompt`), `regeneration_guide`
(with `shot_prompt_sequence` = FULL per-shot prompts, in order), `virality_formula` (the lean
block the hub `brief` reads), and the self-eval `evaluation` block. `content_id` is the universal
join key and comes from the queue — never invented. `audio_id` (in `audio`) is the sound join key.

## Run loop (per clip)
1. Pull the filtered pending queue **and** the reference queue (`GET /api/reference/{p}/pending`).
2. Compose the system prompt from evolving memory: `system_prompt.base.md` + top `patterns.md`
   lessons + `memory/<platform>/notes.md` (never static).
3. Download the hub's local media to `work/`, upload FRESH to the Gemini File API, wait ACTIVE,
   use the returned URI (re-upload on expiry — never a hardcoded URI), clean up temp files.
4. Analyze with `gemini-2.5-pro` (JSON mode, temp ~0.4, ~64k max tokens).
5. Validate (`jsonschema` + semantic); on failure run a targeted repair pass with the errors.
6. Self-eval judge (`gemini-2.5-pro`) scores against `memory/rubric.md`; deterministic hard-fails
   veto acceptance. Refine loop capped at ~3 passes; stamp the final `evaluation` block.
7. Pass through the hub's `audio_*` fields into `audio`; infer `audio_strategy`.
8. `POST /api/analysis/{p}` (references saved with `is_reference:true`).
9. Distil a lesson into `patterns.md`; post one shared insight (`POST /api/insights`).
10. Log every step locally (JSONL) + `POST /api/logs` lifecycle events; `POST /api/evals` the
    self-eval; pace requests; 3-strike circuit breaker.

## Defects from the original scratch prototype — fixed here
<!-- Historical: the `VideoAnalysis` prototype these refer to no longer exists in the repo.
     Kept because each bullet explains why a guard in this engine is there. -->

- **Dead validation** → `engine/schema.py` actually validates + drives a repair pass.
- **Placeholder `shot_prompt_sequence`** → `schema.semantic_errors()` hard-fails placeholders;
  the judge rejects them.
- **Hardcoded/expiring `FILE_URI`** → `engine/gemini.py` uploads fresh + re-uploads on expiry.
- **No memory / static prompt** → `engine/memory.py` composes the prompt from evolving markdown
  and self-evaluates every run.

## Platform-wide conventions (PIPELINE.md §10)
- **Logging (§10.1):** shared `engine/logsetup` per-run `logs/<start>_<cmd>.log` (pretty + JSONL);
  `POST /api/logs` for LIFECYCLE events only, stamped with `run_id`.
- **Eval (§10.2):** self-eval judge → `POST /api/evals` per artifact.
- **Config (§10.3):** `GET /api/config/agent/analysis-engine` at run start over defaults; knobs
  declared in the manifest `config_schema` (Dashboard-editable).
- **Secrets (§10.4):** referenced by env-var NAME only (`GEMINI_API_KEY`); the hub sees status,
  never values. `.env.example` documents the names; `.env` is gitignored.

## Layout
```
cli.py                  run / once / status
engine/hub.py           typed hub client (built against /openapi.json)
engine/gemini.py        Gemini REST: File API upload (+ expiry), JSON generateContent
engine/analyze.py       compose prompt -> analyze -> validate -> repair
engine/evaluate.py      the self-eval / judge pass (hard-fails + rubric score)
engine/memory.py        assemble/distil the markdown memory layer
engine/schema.py        canonical schema_version 2 + validator (structural + semantic)
engine/circuit.py       3-strike breaker + pacing
engine/logsetup.py      per-run pretty console + JSONL
memory/                 MEMORY.md, system_prompt.base.md, rubric.md, patterns.md, <platform>/notes.md
tests/                  test_schema.py (offline), smoke_hub.py (live hub round-trip)
```
