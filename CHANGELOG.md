# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Two clones on one machine can no longer touch each other**, which is what running two
  niches at once requires. Nothing here writes outside its own directory, so the whole
  coupling was the loopback: the port, and the `BACKEND_API` every agent dials.
    - **A port per checkout.** `./init` pins one (8787, then 8788) into `ReelScraper/.env`
      as `HUB_PORT`. A fallback port is useless as an address — it moved on every restart,
      so nothing could be bookmarked and no `.env` could point at it. `--port` pins too.
    - **`BACKEND_API` written into every component's `.env`** once the port is settled. The
      hub only ever exported it for stages *it* spawned; an agent run by hand read its own
      `.env`, which ships pointing at 8787 — so on a second clone `cd SimilarContent && uv
      run cli.py propose` posted that niche's proposals into the *first* clone's studio,
      against the first clone's corpus, with every call returning 200.
    - **Agents refuse a hub that is not theirs.** Each checks `GET /api/hub` at startup and
      exits 2 naming both directories. A definite mismatch is refused; silence (an older
      hub, an unreachable one) is not treated as one.
    - The Dashboard sidebar carries the **niche name**, since two boards are otherwise
      pixel-identical, and `./stop` reports a foreign checkout on this clone's own port
      rather than the hardcoded 8787.
- **`GET /api/hub`** — `{root, niche, stale}`. `root` is what agents compare against;
  `stale` is true when the hub's sources changed after the process imported them.
- **`./stop`** — shuts down the hub and any stage jobs this checkout started. Processes
  are matched by working directory, not command line, so several clones on one machine
  never stop each other's hub; it reports a foreign checkout holding the port instead of
  touching it. `--list` to preview.
- **`./clean`** — back to a fresh clone. Stops everything, archives every generated path
  to `backups/cuttingroom-data-<timestamp>.zip`, verifies that archive, prints its path,
  asks, and only then deletes — data *and* stored keys. A corrupt or unwritable archive
  aborts before anything is removed. Keys are deliberately not archived. Never removes a
  git-tracked file, and restores agent-written `memory/*/patterns.md` to the shipped
  version rather than leaving observations about real analyzed clips behind.
- **Automatic runs.** Config → Automatic runs repeats `scrape → analyze → media` on a
  timer (6h/12h/day/3 days/week) per platform. `GET /api/schedule`,
  `PUT /api/schedule/{platform}`. The hub must be running — there is no daemon outside
  it — so it is best-effort, and the panel says so. Blueprint generation is opt-in
  because it calls a paid API once per clip; the timestamp is persisted and stamped
  before launch, so a long run cannot double-fire and a restart neither re-fires nor
  loses the schedule.
- **Stage readiness.** `GET /api/platforms` reports per-stage
  `{ready, blocked_by, reason}`, and `POST /api/pipeline/{p}/{stage}` returns **409**
  with that reason instead of launching a subprocess that fails (`?force=true`
  overrides). The Board greys a blocked Run, states why, and offers the stage that
  unblocks it.
- **`watchlist` and `scraped_items`** on `GET /api/platforms`, so the Board can report
  each stage's own progress instead of the end of the pipeline.
- An **Add pages** button on the Board's Sources card, and the same affordance from every
  empty state that traces back to an empty watchlist — self-gating once handles exist.
- A toast surface. Failed requests now show the hub's own explanation; previously every
  mutation swallowed its error.

### Fixed
- **A hub that outlived a `git pull` kept serving the old API, and the Board said
  "undefined pages".** Python imports a module once and serves it from memory, so a hub
  left running from an earlier session goes on answering with the response shape it started
  with — while the Dashboard, served from disk, is current. `./init` saw *something*
  answering on 8787 and reused it, so a new frontend asked a three-hour-old backend for
  `watchlist` and `scraped_items`, got neither, and rendered the word `undefined` where a
  count belonged — a symptom pointing nowhere near the cause. `./init`, `./demo` and
  `./health` now restart a stale hub instead of adopting it, and a missing count renders as
  `—` rather than as a word to decode.
- **A verified API key still showed as `SECRET MISSING`.** `GET /api/config/agent/{agent}/
  secrets/status` replayed `present` out of `producers/registry.json` — whatever the agent
  self-reported the last time it registered. Paste a key into `./init`, watch it verify
  against Google, and the Agent Desk went on saying the secret was absent until that agent
  next happened to run; the Board's readiness check, reading the `.env` directly, said the
  opposite at the same moment. Presence is now evaluated per request against the hub's
  environment and the agent's own `.env`, OR-ed with the self-report — the agent can see
  sources the hub cannot (a `session.txt`, or `GOOGLE_API_KEY` where the manifest names
  only `GEMINI_API_KEY`), so a live miss must never report a working agent as broken. Still
  status only: the hub reads the file to test for a non-empty assignment and never holds,
  returns or logs a value.
- **The same creator could sit on the watchlist twice.** `pages.txt` accepts three
  spellings — `handle`, `@handle`, and the profile URL — and every scraper collapses them
  before fetching. The hub compared raw strings, so approving an AutoSearch candidate (which
  posts the URL form) for a creator someone had typed by hand appended them again. The
  scrape deduped and pulled them once while the Board counted two pages. Both the dedupe and
  the count now normalize the way the scrapers do; case is deliberately not folded, since
  YouTube channel ids are case-sensitive.
- **`Run full pipeline` never ran a pipeline.** `POST /api/pipeline/{p}/run-all` was
  answered by the `/{platform}/{stage}` catch-all — registered above it, and Starlette
  matches in registration order — with `400 "stage must be one of [...]"`. With no
  `onError` anywhere, the click looked inert.
- **A resumed scrape deleted the creators it skipped.** `save_outputs` rewrites
  `reels_raw.json` wholesale and the resume logic skips creators already in it, but the
  accumulator they share was seeded empty. Re-running with nothing to do wrote `{}` over
  the corpus and still exited 0; adding one handle to five deleted the other four.
- **Failure reasons were discarded.** The job tail kept `stdout or stderr`, so any stage
  that printed a progress line lost its stderr — the reason, in exactly the case it was
  needed. Both streams are kept, stderr last.
- A stage that crashed outright recorded `rc: None`, which the run-all supervisor read as
  "unknown stage, skip cleanly" and ran on regardless.
- `PUT /api/config/{platform}` rewrote `pages.txt` from a `GET` that had already stripped
  comments, deleting the file's own instructions on a new user's first save.
- A second `run-all` started a second supervisor over the same files; it is now refused.
- `x` and `youtube` died on an uncaught `FileNotFoundError` when `pages.txt` was absent,
  instead of reaching the friendly message `instagram` already had.
- The header status now carries the failing stage's reason and opens the Floor Log; a
  halted run records where it stopped and what never ran.
- A finished scrape that had not been analyzed yet was reported as if it had found
  nothing. `scrape` writes `<content>_raw*.json`; only `analyze` turns that into the
  `content.json` every corpus view reads — so with 250 reels sitting on disk,
  `/api/platforms` still said `has_data: false, items: 0`, the Board said "no data",
  and the reel grid said "No reels match — run Scrape", pointing the user back at the
  stage they had just watched finish. `/api/platforms` now also returns **`scraped`**,
  read off the filesystem so it survives a hub restart, and the Dashboard uses it to
  tell the two states apart: the empty grid now says "Scraped, not scored yet" and
  offers a Run analyze button. The first-run checklist uses it too, so a hub restart no
  longer walks you back to re-scraping reels you already have.

### Changed
- **`./init --reset` now clears stored API keys and keeps your data.** It used to delete
  generated data — so re-running setup after rotating a key threw away a scrape that cost
  twenty minutes and real Instagram traffic. Wiping data is `./clean`, which archives it
  first. The reset also covers `platforms/x/session.txt`, which it previously missed.
- The "Building the dashboard…" page now links to the published documentation site
  instead of the hub's own `/docs` Swagger UI. Swagger answers "what endpoints
  exist", which is not the question someone waiting on a first build is asking, and
  it renders from a CDN bundle so it shows an empty frame when the schema or the
  network is unhappy. The address is read from `documentation/mkdocs.yml`'s
  `site_url`, so a fork gets its own site once `scripts/apply-identity.sh` has run.
  `/docs` itself is unchanged and still served.

## [1.0.0] - 2026-07-21

First release. A multi-agent content pipeline: scrape handpicked creators, score
every post for virality, break the winners into generation-ready blueprints, and
spin those into ready-to-post drafts behind a human gate.

### Added
- **ReelScraper** — the hub at `127.0.0.1:8787` and the scraper in one component.
  Serves the whole `/api/*` contract (corpus, analysis, audio, producers, studio +
  human gate, references, discovery, renders, logs, evals, per-agent config and
  secret *status*, SSE) and drives the pipeline stages as subprocesses. Scrapers
  for Instagram (guest-only), X (burner session), and YouTube (key-free InnerTube).
- **Four virality signals** — engagement rate, reach multiplier, outlier score, and
  velocity, percentile-normalized and blended per platform into a 0–100 score + tier.
- **AnalysisEngine** — watches top clips and writes rich schema-v2 blueprints
  (shots, generation prompts, regeneration guide, virality formula, self-evaluation)
  to `POST /api/analysis/{platform}`.
- **The Producer SPI** — every generation agent self-registers a manifest, reads only
  hub inputs, and writes only hub outputs. `SimilarContent` (`kind: clone`) ships as
  the worked producer; `_producer-template/` is the scaffold for new ones.
- **AutoSearch** — discovery agent that finds and scores new creators; candidates go
  through the human gate before the hub appends them to `pages.txt`. Off by default
  behind a fail-closed `discovery_enabled` kill-switch.
- **Dashboard** — "The Cutting Room" React control board: producer lanes, the human
  gate, sounds, blueprints, per-agent workflow boards, activity and evals.
- **Audio intelligence** — `audio_id` as the sound join key, plus trend scoring and
  Rising/Hot/Saturated/Evergreen buckets derived from tracked creators.
- Config-driven niche system with Fashion as the worked example, plus a
  `scripts/new-niche.sh` converter that branches a full pipeline per niche.
- One-command demo (`./demo`) that runs the pipeline end to end and surfaces
  five easy-to-make clone recipes.
- CI/CD via GitHub Actions (Dashboard lint/typecheck/build/test, a Python test matrix
  across all four Python components, MkDocs site deploy, tagged releases) and a
  pre-commit config.
- Docs: the MkDocs site under `documentation/`, plus README, CONTRIBUTING,
  CODE_OF_CONDUCT, SECURITY, and ROADMAP.

### Security
- Pre-publication audit before the first public push. Test fixtures no longer carry real
  creator handles, captions, content IDs or audio metadata — `SimilarContent` and the
  Dashboard suites now select fixtures by *shape* (shot count, on-screen-text structure),
  which removes the third-party data and makes the tests run against any operator's corpus
  instead of one specific dataset.
- `.gitignore` now covers the scored corpus (`platforms/*/content.json`), the CSV/xlsx/raw
  exports, `memory/shared/insights.jsonl` and the `renders/` sidecars. `./demo` copies the
  private dataset into exactly those paths, so previously a single `./demo` followed by
  `git add -A` could have published it. `./health` checks all of them by path.
- `POST /api/reference/{platform}` validates the supplied URL: http(s) only, and the host is
  resolved and rejected if it lands on a private, loopback or link-local address. Previously
  `urllib.request` would honour `file://`, writing arbitrary local files into the
  `/media`-served directory, and could reach the cloud metadata service.
- Third-party GitHub Actions pinned to full commit SHAs.
- Secrets are declared by env-var **name** only and read from gitignored per-agent
  `.env` files. The hub surfaces secret *status* (present/absent) and never stores or
  returns a value.
- Instagram access is guest-only; X requires a burner session and is never a personal
  account. Rate-limit circuit breakers stop after three consecutive limits.
- Generated media is kept in a separate namespace from the scraped corpus, so a
  producer can never overwrite a real creator's video.

[Unreleased]: https://github.com/VincitoreSi/TheCuttingRoom/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/VincitoreSi/TheCuttingRoom/releases/tag/v1.0.0
