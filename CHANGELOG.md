# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
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
