# AnalysisEngine

Gemini-powered analyzer for a short-form-video virality pipeline. It watches the top-ranked clips
(that already have local media) **shot by shot** and writes a rich, generation-ready
**schema_version 2 blueprint** back to the pipeline hub over HTTP — feeding the producer agents
and the Dashboard. It sits right after the Media stage, and replaced an early `VideoAnalysis`
scratch prototype that has since been deleted from the repo.

## Quick start
```bash
uv sync                                   # .venv + deps (jsonschema, yt-dlp)

# 1) Check the hub connection + what's missing (works with NO Gemini key / no media):
uv run cli.py status

# 2) A full live run needs BOTH of these first:
export GEMINI_API_KEY=...                  # or GEMINI_KEY / GOOGLE_API_KEY
#    ...and local media downloaded in the hub repo:
#    (in ../ReelScraper)  uv run download_media.py instagram

# 3) Analyze the top 2 pending Instagram clips (+ any pending references):
uv run cli.py run instagram --limit 2
```

The hub base URL is `BACKEND_API` (default `http://127.0.0.1:8787`). Start the hub in ReelScraper
with `uv run cli.py start` if `status` reports it unreachable.

## Commands
| Command | What it does |
| --- | --- |
| `uv run cli.py status` | Hub health, per-platform `media_ready`/`analyzed` counts, secret status (by env-var name), effective config. |
| `uv run cli.py run <platform> [filters]` | Analyze the pending queue **and** the reference queue for a platform. |
| `uv run cli.py once <content_id> [--platform p]` | Analyze / re-analyze one clip by id. |

`run` filters mirror the hub's pending endpoint: `--min-score`, `--tier`, `--min-duration`,
`--max-duration`, `--content-type`, `--limit`, `--stale`, `--reanalyze`, plus `--no-references`
/ `--references-only`.

## How it works
- **Evolving memory → composed prompt.** Every run assembles the system prompt from
  `memory/system_prompt.base.md` + the top lessons in `memory/patterns.md` +
  `memory/<platform>/notes.md`. Nothing is hardcoded; `memory/MEMORY.md` indexes it.
- **Fresh File API upload.** The hub's local media is downloaded to `work/`, uploaded to the
  Gemini File API for a fresh URI (re-uploaded automatically on ~48h expiry), and the temp file
  is cleaned up. No URI is ever hardcoded.
- **Real validation + repair.** `engine/schema.py` validates the blueprint with `jsonschema` and
  semantic checks (e.g. it hard-fails placeholder `shot_prompt_sequence` strings). On failure the
  engine runs a targeted repair pass with the exact errors.
- **Automatic self-eval.** A `gemini-2.5-pro` judge scores the blueprint against
  `memory/rubric.md`; deterministic hard-fails veto acceptance. The refine loop is capped at ~3
  passes and the final `evaluation` block is stamped in.
- **Audio strategy (D3b).** The hub's `audio_*` fields are passed through into `audio`, and the
  model infers a top-level `audio_strategy` block (audio_type, beat markers, reuse recommendation).
- **References.** The reference queue (`GET /api/reference/{p}/pending`) is consumed too; those
  blueprints are saved with `is_reference:true`.

## Verify without a live Gemini call
```bash
uv run python -m tests.test_schema     # offline: good blueprint validates, bad one is rejected
uv run python -m tests.smoke_hub       # live hub round-trip (register/config/secrets/POST+GET/logs/evals)
```

## Secrets
`GEMINI_API_KEY` (or `GEMINI_KEY` / `GOOGLE_API_KEY`) is read from the environment only and is
**never** sent to the hub — the hub sees presence status, never the value. Copy `.env.example` to
`.env` (gitignored) and fill it in, or export it in your shell.
