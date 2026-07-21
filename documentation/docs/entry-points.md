---
title: Entry Points & Demo Data
---

# Entry Points & Demo Data

Four executable scripts at the repo root are the everyday way in. Each checks
its own prerequisites, installs what is missing, and then launches — so none of
them assume a previous step was run. Shared shell helpers live in
`scripts/_common.sh`.

| | Command | Gets you | Needs API keys? |
|---|---|---|---|
| **Look around** | `./demo` | A populated dashboard, instantly and offline | No |
| **Start for real** | `./init` | A clean, empty install ready to scrape your own niche | Gemini key (promptable, skippable) |
| **Read the docs** | `./docsite` | This site, built and served with live reload | No |
| **Check it works** | `./health` | Every test suite, build and repo invariant, with a non-zero exit on failure | No |
| **Stop it** | `./stop` | Every process this checkout started, shut down | No |
| **Start over** | `./clean` | Data archived to a zip, then wiped back to a fresh clone | No |

---

## `./demo` — a populated dashboard, offline

```bash
./demo                 # unpack + load the sample dataset, set up if needed, launch
./demo --keep          # launch without overwriting data already on disk
./demo --port 9000     # prefer a specific port
```

The realistic dataset is **not** in this repo — it ships separately as
`demodataset.zip`. Put that zip in the repo root and `./demo` unpacks it into
`demo-data/data/`, copies it over the working tree, and starts the hub, so the
Corpus grid, the Studio gate, the rendered reels, the evals and the activity
log all have content. **No API keys, no scraping, no model calls** — nothing
here reaches the network.

If no dataset and no `demodataset.zip` are present, `./demo` stops and prints
both ways forward: drop in the zip, or run `./init` and add your own handles.

!!! warning "It overwrites generated data"
    `./demo` replaces the corpus, studio, renders, evals and logs on disk. It
    never touches source code or your `.env` files. Use `--keep` if you have
    real work in the working tree, or `./init` for a clean empty start.

## `./init` — a clean, empty install

```bash
./init                 # check, install, configure, launch
./init --no-launch     # set everything up but don't start the hub
./init --reset         # clear stored API keys first (your data is kept — see ./clean)
./init --port 9000     # pin this checkout to a specific port
```

The first-run path from a clean clone. It verifies `uv`, Python, Node, npm and
`curl` (and warns if `ffmpeg` is absent, since SimilarContent stitches reels
with it), syncs every Python project, builds the Dashboard, then handles your
`GEMINI_API_KEY`. If a key is already present — in the environment or in a
previous run's `AnalysisEngine/.env` / `SimilarContent/.env` — it is **reused
without re-prompting** and re-verified against Google; otherwise it prompts,
verifies the key, and writes it to both `.env` files (telling you where, so you
can remove it by hand). Skipping the prompt is fine — it tells you exactly where
to add the key later.

You land on an **empty** dashboard. That is the point: no corpus, no proposals,
no renders, ready for your own handles in
`ReelScraper/platforms/instagram/pages.txt`.

It also **pins the port this checkout owns** — 8787 for the first clone on the
machine, 8788 for the next — into `ReelScraper/.env` as `HUB_PORT`, and writes
that address into every component's `BACKEND_API`. A hub already answering is
only reused if it belongs to this checkout *and* is running the code on disk; a
stale one is restarted and another checkout's is left alone. See
[Niches → Running two niches at once](niches.md#running-two-niches-at-once).

!!! note "`--reset` clears keys, not data"
    It removes the stored API keys (`AnalysisEngine/.env`, `SimilarContent/.env`,
    `AutoSearch/.env`, `ReelScraper/.env`, `platforms/x/session.txt`) and leaves
    every scraped reel where it is — re-running setup after rotating a key should
    not cost you a scrape. To wipe the data too, use [`./clean`](#clean-back-to-a-fresh-clone),
    which archives it to a zip first. Keys are not recoverable either way.

## `./docsite` — this site

```bash
./docsite                 # build, then serve with live reload and open a browser
./docsite --build         # build only, into documentation/site
./docsite --port 9000     # prefer a specific port
```

mkdocs is not a standalone install here — it lives in ReelScraper's `dev`
dependency group, so everything runs through
`uv run --project ReelScraper`. There is nothing to install first.

Building also lights up the hub's own `/documentation` route: the hub mounts
`documentation/site` when that directory exists, but only checks at startup, so
**restart the hub after a first build**.

---

## Ports

All three prefer a port (8787 for the hub, 8000 for the docs) and **fall back
to a free one** when it is busy, printing the port they actually got. Nothing
in the system hardcodes 8787 — the hub exports `BACKEND_API` so every agent it
spawns inherits the real address. See
[CLI Reference → Ports](cli.md#ports-nothing-is-hardcoded).

The hub's port is additionally **pinned per checkout** in `ReelScraper/.env`
(`HUB_PORT`), so a second clone settles on 8788 and *stays* there instead of
taking a different random port on every restart. A pinned port that another
checkout has taken is re-claimed rather than handed over.

---

## `demo-data/` and `demodataset.zip`

This is the clean, shareable build: `demo-data/data/` is **empty**. The ~47 MB
sample dataset ships **separately** as `demodataset.zip`, because it is real
scraped content and is handed out privately rather than committed.

```
demo-data/
  README.md        how to get and use demodataset.zip
  data/            empty here; ./demo unpacks demodataset.zip into it
```

With `demodataset.zip` in the repo root, `./demo` unpacks it and loads a scored
corpus, Gemini blueprints, clone recipes at the human gate, rendered reels that
play, evals and an activity log. Its `data/` mirrors the repo layout exactly, so
loading it is a plain recursive copy into place. Nothing here is required to
*run* the project — `./init` starts clean without any dataset.

**What was stripped when the snapshot was captured:** every signed Instagram CDN
URL (they carry auth parameters and expire within hours), every `.env` and
`session.txt`, the agent memory databases, the raw scrape dump, the xlsx/CSV
exports, and the bulk of `media/` — only posters plus the clips that have
blueprints are kept.

!!! danger "`demodataset.zip` is real scraped data — keep it private"
    It carries real creator handles, real captions, real engagement metrics, and
    AI-generated reels derived from specific third-party videos. That is why it
    is never committed and is shared only with people the owner chooses.
    Publishing it would republish other people's content and personal data
    without consent. `demo-data/README.md` has the details.

---

## See also

- [Quickstart & Usage](quickstart.md) — the guided first run.
- [CLI Reference](cli.md) — every flag on every command, root and per-agent.
- [Architecture](architecture.md) — how the hub, agents and Dashboard relate.


---

## `./health` — one command before you commit

```bash
./health           # test suites + typecheck + lint + builds + docs + invariants
./health --quick   # test suites + typecheck + lint — fast, skips builds, docs and the live smoke
./health --live    # also boots the hub and exercises the real HTTP surface
./health --fix     # run the formatters that have a --write mode, then re-check
```

It exits non-zero when anything fails and prints the tail of each failing command's
output, so it works unchanged as a pre-commit hook or a CI gate.

**What it runs**

| Group | Checks |
|---|---|
| Prerequisites | `uv`, `python3`, `node ≥ 20`, `npm`, `curl`, `git`; `ffmpeg` reported as optional |
| Python | pytest for ReelScraper, AnalysisEngine, AutoSearch, SimilarContent |
| Dashboard | `tsc --noEmit`, `vitest run`, `eslint`, and the production build |
| Documentation | `mkdocs build` |
| Invariants | see below |
| Live (`--live`) | boots the hub, checks the key endpoints, that a render streams with HTTP range support, and that CORS still rejects a foreign origin |

**Why invariants, not just tests**

Several things that have genuinely broken in this repo were invisible to unit tests,
because they are properties of the *repository* rather than of any function:

- a generated reel written into `ReelScraper/media/`, the scraped-corpus namespace —
  which once made the Corpus serve our own output under real creators' IDs;
- an unanchored `.gitignore` rule matching *inside* `demo-data/` and silently dropping
  most of the sample dataset from a commit;
- a `.env` reappearing on disk or entering git history;
- the hub growing a `0.0.0.0` bind.

`./health` asserts each of those directly. If you add a similar structural guarantee,
add the assertion here too — that is what the section is for.


## `./stop` — shut this checkout down

```bash
./stop            # stop the hub and any stage jobs it started
./stop --list     # show what would be stopped, stop nothing
```

Processes are matched by **working directory**, not by command line. The hub runs as
`uvicorn api.app:app` with cwd `<repo>/ReelScraper` and the repo path never appears in its
arguments — so a name match would either miss it or, worse, kill an identically-named
process belonging to a different clone. Several checkouts of this repo on one machine is
normal, and a hub from the wrong one answering on `:8787` is a genuinely confusing way to
lose an afternoon, so `./stop` also tells you when the port is held by a checkout that is
not this one, without touching it.

Nothing on disk changes.

## `./clean` — back to a fresh clone

```bash
./clean               # archive, confirm, then delete data AND keys
./clean --keep-keys   # wipe data only
./clean --list        # show what would go, do nothing
./clean --yes         # skip the prompt (the archive is still written first)
```

The order matters, and it is the whole point of the script:

1. Stop everything.
2. Archive every generated path to `backups/cuttingroom-data-<timestamp>.zip`.
3. **Verify** the archive — a corrupt or empty one aborts with nothing deleted.
4. Print where it is, then ask.
5. Delete.

Restore any time with `unzip -o backups/<archive>.zip -d .`

!!! warning "API keys are deleted and are NOT in the archive"

    Live credentials do not belong in a zip file somebody will forget about, so they are
    removed without a copy. Re-enter them with `./init`, or keep them with `--keep-keys`.
    For the credentials alone — keys cleared, scraped data kept — use `./init --reset`.

Two things it will never do: remove a file git tracks, and leave agent-written memory
behind. `memory/*/patterns.md` ships as curated scaffolding but accumulates observations
about real analyzed clips during a run, so it is restored to the shipped version rather
than deleted or kept.
