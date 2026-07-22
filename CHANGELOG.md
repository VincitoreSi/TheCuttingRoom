# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-07-22

### Added
- **Published container images.** Every `v*` tag builds `linux/amd64` and `linux/arm64` on
  native runners and pushes one multi-arch manifest to `ghcr.io/vincitoresi/thecuttingroom`
  (~190 MB compressed, public, no login). GHCR and not Docker Hub because Docker Hub
  rate-limits anonymous pulls to 100 per six hours per IP — a limit on the people trying to
  run this, not on the maintainer. Manual runs publish `edge` + `sha-<short>` and can never
  move `latest`.
- **`blueprint_top_pct`** (cascade, default 20) — what share of one firing's *new* clips get a
  paid blueprint. Its own field rather than a reuse of `blueprint_pct`, which is the trigger
  cadence `_cascade_steps` derives the funnel invariant from: one number meaning both is the
  same trap as `media_pct: 60` sitting beside `download_media.py --top 60`. Sized against the
  clips that triggered the firing, never the corpus, and it rounds up and floors at 1 so a
  boundary that fired can never advance its watermark over clips nothing looked at.
- **`max_duration_s`** (analysis-engine config, default 30, 0 disables) — the duration veto,
  and the only "easy to remake" signal that exists *before* a blueprint does. 65 of the ease
  score's 100 points come from shot count and static-camera fraction, both read out of the
  blueprint this stage decides whether to pay for. It lives in the agent's own config, not the
  cascade, so a manual Run from the Board obeys it too.
- **`DELETE /api/studio/{platform}/{file}`** — remove a rejected card. Rejected only; the
  `gate.jsonl` audit trail is appended to rather than touched; and an item holding rendered
  media is refused with the route that deletes media on purpose, so tidying a list can never
  destroy paid output.

### Fixed
- **Producers could never register.** The Board's Propose button could only ever answer "no
  registered producer declares proposes:true" — a bootstrap deadlock, not a stale registry.
  Registration is lazy, and a producer's only two hub routes (`propose`, `render`) both resolve
  through `_producer_dir`, which refuses anything unregistered. Nothing in the product could
  perform the first registration. `./init` and `./cr` now do it once the hub answers.
- **A black bar down a rendered reel.** Not stitching: frame 0 came back from the provider with
  a band in it, and frame 0 is the reference for every later frame, so all six carried it at
  identical columns. Frame 0 is now vetted before it earns that job — retries are unanchored by
  construction, and giving up raises rather than spending on frames 1..N.
- **Recipes with no shot list.** With the ease gate starved, ranking falls through to a virality
  backfill, and virality is known for every scored row whether or not the analyzer reached it —
  so the backfill reached first for exactly the clips that had no blueprint, producing recipes
  `recipe.py` then refuses to render. Un-blueprinted now sorts last; a preference, not a filter.
- **Two Dashboard controls that could only be switched on.** `setFlag` hardcoded `true` and both
  Discovery buttons unmounted once their flag was set, so the discovery kill-switch and the
  Gemini term-expansion spend switch were one-way latches.
- **Two controls named "discovery" that were never connected.** Config wrote
  `niche_config.discovery.enabled`, read only by `discover.py` — which the hub, `./cr` and
  `./init` never launch. It governed a script the Dashboard cannot run while sitting above
  keywords that do reach the agent. Removed; the agent's kill-switch owns the Discover page.
- **The desk tag painted outside its card.** Both flex items were non-shrinking, so on an
  eight-node track "Blueprint" + "DESK →" exceeded the card. Glyph-only now.
- **A render guard that read the wrong root under test.** `tests/conftest.py` repoints `ROOT`,
  `MEDIA`, `PRODUCERS_FILE`, `LOGS_FILE` and `FRONTEND` — not `RENDERS`, which is bound at
  import — so a guard reading that constant consulted the developer's real renders directory.

### Changed
- Per-creator scrape caps 250/200 → **100** across all three platforms, their `niche_config.json`,
  the four niche templates and the scrapers' own fallbacks, so switching niches cannot silently
  restore the old number. `default_limit` 15 → **10**; `propose_count` 3 → **5**.

## [1.1.0] - 2026-07-22

### Added
- **An eighth stage: Propose.** `discover → sources → scrape → analyze → media → blueprint →
  propose → studio`. A producer that declares `proposes: true` ranks the winners by how cheap
  they are to remake, joins each to its blueprint, and writes a recipe into the human gate.
  It is deliberately a *separate* capability from `renderable`: proposing reads the corpus and
  writes markdown and costs nothing, while rendering spends image-API credits per frame. The
  free, unattended trigger must never be gated on — or grantable by — the paid one, so
  `_producer_dir(agent, capability=…)` enforces the split and the hub appends the `propose`
  subcommand itself rather than trusting a manifest to spell it out. With no producer
  declaring it, or more than one, the hub returns 409 rather than guessing.
- **The cascading heartbeat, as a percentage funnel.** One anchor (`scrape_count`) and a
  percentage per stage; the number of runs each stage needs is *derived*
  (`step[stage] = ceil(step[previous] × 100 / pct[stage])`). Because every percentage is ≤ 100,
  the derived steps can only ever increase down the funnel — monotonicity is structural rather
  than validated, so there is no invalid configuration to reject. **The cascade stops at the
  studio and can never fire `render` under any setting**; `render` is not in `CASCADE_STAGES`
  and an assertion keeps it out.
- **Stop.** Every running stage can be stopped from the Board. The stage card's Run button
  *becomes* the stop control while a job is running rather than growing a second button —
  with eight nodes on the track there is no room for two, and the earlier two-button layout
  clipped out of the card.
- **A container lane.** `./cr` plus a single image: `./cr build`, `up`, `down`, `agent`,
  `health`, `docsite`, `shell`, `keys`, `status`, `verify-loopback`. Install Docker and
  nothing else — no uv, Python, Node or ffmpeg on the host. Measured at **305.8 MB
  uncompressed / 92 MB to pull**, an 86 s cold build and 8 s after a source edit. Windows is
  supported through WSL2 only. Documented in full at `documentation/docs/docker.md`.
- **`./health --strict`**, for CI and releases. Four invariants — the tracked-`.env` check,
  the git-history secret scan, the demo-dataset check and the working-data ignore check —
  degraded to skips whenever git could not be read, and the run still printed HEALTHY having
  checked materially less than it claimed. Nobody reads a skip counter. `--strict` turns
  environment-driven skips into failures; a bare `./health` still degrades gracefully, which
  is right for a tarball.
- **Two new invariants for the container boundary.** The existing "hub binds loopback only"
  check is a grep for `0.0.0.0` in the hub source, and in container mode it keeps passing
  while proving nothing — the hub binds `0.0.0.0` inside its own network namespace on
  purpose, because that is the only address compose can forward to. `compose publishes on
  loopback` lints every `ports:` entry for a `127.0.0.1:` or `[::1]:` prefix and needs no
  Docker; `off-loopback refused` records whether the property was actually *observed*, which
  only `./cr verify-loopback` can do from the host.
- **The host-lane scripts refuse to run against a containerized checkout.** All six stop with
  the `./cr` equivalent instead of guessing. `./stop` matters most: it identifies the hub by
  working directory, which is meaningless across a container boundary, so it found nothing,
  reported success, and left the container running. `TCR_FORCE_HOST=1` overrides.
- **`./demo --no-launch`** — load the dataset and start no hub. Required by `./cr demo`, which
  runs the script in a one-shot container where reaching the launch step would start a second
  hub on a port nothing publishes.
- Skipped tests are counted and named in `./health`'s summary. A green suite that quietly ran
  fewer tests than it could is the failure mode worth surfacing: a broken ffmpeg install turns
  ~13 real stitch tests into silence, and the run still said HEALTHY.
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
- **`score_ease` collapsed every clip onto the same number, so nothing was ever proposed.**
  All three terms were banded and all three saturated: the shot bands stopped at 4 while real
  reels run 6–7 (scoring 0 on a 45-point signal), the duration bands made 9.43 s and 9.87 s
  identical, and the static-camera term was all-or-nothing. Six distinct blueprints all scored
  exactly 40 against a gate of 55 — the ordering test passed on `assert 40 > 40` only because
  its fixture guaranteed shot-count contrast while asserting score contrast. The terms are now
  continuous, and those six blueprints separate into 52.18 / 51.80 / 51.66 / 51.03 / 50.70 /
  50.62. This was a live defect since v1.0.0, found because the skipped-test reporting above
  made it visible.
- **The cascade config saved nothing and silently reverted.** The form sent `scrape_count` and
  the per-stage percentages to a Pydantic model that declared none of them, and Pydantic v2
  ignores unknown fields by default — so the `PUT` returned 200, persisted nothing, and the
  success handler refetched a row without those fields, snapping every control back to a
  hardcoded default. A save that reports success and discards the data is worse than one that
  fails.
- **The Stop button clipped out of its card.** Adding the `propose` node took the board from
  seven cells to eight (−12.5 % width each) at the same moment a second, non-shrinking button
  was added to a slot that had just stopped being full-width. One button that changes meaning
  fits; two never did.
- The documentation site deployed without `--strict`, so a dead cross-reference or a nav entry
  pointing at a renamed file was a warning, an exit 0, and a broken page found by a reader
  rather than in review. Both `./health` and the deploy workflow now use the identical command.
- `./init` aborted on any host with GNU coreutils: `mktemp -t vp-setup` is valid BSD and
  invalid GNU, and every developer here is on macOS while every container is not.
- `./clean` deleted git-tracked files when `git` errored rather than answered. "No `.git`" and
  "git could not read the `.git` that is right there" are not the same state, and a bind mount
  owned by another uid produces the second; the check now fails closed.
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

[Unreleased]: https://github.com/VincitoreSi/TheCuttingRoom/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/VincitoreSi/TheCuttingRoom/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/VincitoreSi/TheCuttingRoom/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/VincitoreSi/TheCuttingRoom/releases/tag/v1.0.0
