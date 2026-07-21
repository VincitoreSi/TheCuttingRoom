# scripts/

Supporting scripts. **The things you actually run day to day live at the repo root**, not
here — these are the pieces those root scripts are built from, plus a few one-off operator
tools.

## Start at the root

| Command | What it does |
| --- | --- |
| `./init` | Clean setup from a fresh clone, then launch. Checks prerequisites, installs dependencies, prompts for keys, and opens an **empty** dashboard. |
| `./demo` | Restores the committed snapshot in `demo-data/` and launches. **No API keys, no scraping, no model calls** — every view has content in it. |
| `./docsite` | Builds the mkdocs site and serves it with live reload. |

All three take `--port N`, and each has `--help`. Anything demo-related is `./demo` — there
is no demo script in this directory.

---

## What is in here

| File | What it is |
| --- | --- |
| [`_common.sh`](#_commonsh) | Shared shell helpers the three root scripts source. Not runnable on its own. |
| [`capture-demo.py`](#capture-demopy) | Snapshots the live working install into `demo-data/`. The producer side of `./demo`. |
| [`new-niche.sh`](#new-nichesh) | Scaffolds a new niche onto its own git branch. |
| [`apply-identity.sh`](#apply-identitysh) | Applies git author identity and rewrites `GITHUB_USER` placeholders from `.env`. |
| [`seed_renders.py`](#seed_renderspy) | **Legacy one-shot backfill.** Superseded by `SimilarContent`'s own `render` command. |

---

## `_common.sh`

Sourced by `./init`, `./demo` and `./docsite`; it is a library, so running it directly does
nothing useful. It provides:

- **Output** — `step`, `say`, `ok`, `warn`, `die`, `banner` (consistent formatting across
  all three scripts).
- **Prerequisites** — `have`, `require`, `optional`, `check_python`, `check_node`.
- **Ports and the hub** — `free_port`, `hub_responding`, `wait_for_hub`, `start_hub`,
  `open_browser`. The hub's port is **not** fixed: `start_hub` falls back to a free port and
  reads the real one back out of the `HUB_URL=…` line `ReelScraper/cli.py` prints, exposing
  it as `$HUB_URL`.
- **Setup** — `sync_python_projects`, `build_dashboard`, `write_key`, `prompt_secret`.
  `write_key` appends a secret to a gitignored per-agent `.env` by name, only if absent.

If you are writing another operator script, source this rather than re-implementing any of
it:

```bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$ROOT/scripts/_common.sh"
```

## `capture-demo.py`

Snapshots the current working install into `demo-data/`, which `./demo` restores. Run it
after a real pipeline run, whenever the demo should reflect newer work.

```bash
python3 scripts/capture-demo.py            # capture into demo-data/
python3 scripts/capture-demo.py --dry-run  # show what would be captured, and how big
```

It deliberately **omits** `platforms/*/reels_raw.json`, the xlsx/csv exports, most of
`media/` (only posters plus clips that have blueprints are kept), and every `.env`,
`session.txt` and `content.db`. It **sanitises** Instagram's signed CDN URLs
(`thumbnail_url` / `media_url`), which carry auth parameters and expire within hours.

> **Privacy.** The snapshot contains REAL scraped data — real handles, captions, engagement
> metrics, and frames derived from other people's reels. That is a deliberate choice for a
> **private** repo. Read `demo-data/README.md` before making this repository public.

## `new-niche.sh`

```bash
./scripts/new-niche.sh <niche>      # <niche> matches niches/<niche>.yaml
```

Validates `niches/<niche>.yaml`, creates and switches to a `niche/<niche>` branch (refusing
to clobber an existing one), writes each platform's `ReelScraper/platforms/<p>/niche_config.json`
and a starter `pages.txt` from the YAML's `seed_pages`, then stages the changes. **It does
not commit.**

The seed handles in the YAML are placeholders — replace them with real creators before
scraping. Fashion stays the default on `main`; each other niche lives as a full pipeline on
its own branch.

## `apply-identity.sh`

```bash
bash scripts/apply-identity.sh
```

Reads the repo-root `.env` (falling back to `.env.example`) and sets `git config user.name`
/ `user.email` from `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL`.

It also rewrites the literal token `GITHUB_USER` in files that must name the repo owner
statically and cannot read `.env`: `README.md`, `CHANGELOG.md`,
`.github/ISSUE_TEMPLATE/config.yml` and `documentation/mkdocs.yml`. Nothing is hardcoded —
change the `.env` values and re-run to point everything at a different account.

## `seed_renders.py`

```bash
python3 scripts/seed_renders.py [--platform instagram] [--dry-run]
```

**Legacy one-shot backfill — you almost certainly do not need this.** It exists for a single
historical situation: five slideshow reels were produced in an ad-hoc run *before* the hub's
render store existed, leaving them in `SimilarContent/assets/<slug>/reel.mp4`, outside the
hub's ROOT and unreachable over HTTP. The script walks those assets, matches each to its
studio proposal by slug, derives a poster with ffmpeg, and uploads them through the same
`POST /api/renders/{platform}` endpoint a producer would use.

Safe to re-run (the endpoint upserts). Now that SimilarContent renders through its own CLI —
`uv run cli.py render --platform instagram --file <name>.md`, which posts to that endpoint
itself — this script has no ongoing purpose.

---

## Generating clone recipes

Not a script in here: it is the **SimilarContent producer's own CLI**, run against a hub
that already has a corpus.

```bash
cd SimilarContent
uv run cli.py propose --platform instagram --count 5 --dry-run   # see the picks
uv run cli.py propose --platform instagram --count 5             # publish them
```

- **"best"** = highest `virality_score`.
- **"easy to make"** = simplest production (few shots / short / static single-shot / minimal
  editing). The rule lives in one tunable function, `score_ease()` in
  `SimilarContent/engine/propose.py`.

`propose` needs no image key — it writes markdown recipes into the human gate. Rendering
them is the separate, paid, human-triggered half (`uv run cli.py render`). See
[the CLI reference](../documentation/docs/cli.md) and `SimilarContent/CLAUDE.md`.
