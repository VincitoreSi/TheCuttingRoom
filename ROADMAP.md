# Roadmap

This is a direction sketch, not a promise. Priorities shift with contributions
and real-world use. Have an idea? Open a discussion or an issue.

## Now (foundation)

- ✅ Clean open-source release: curated history, MIT license, contributor docs.
- ✅ Config-driven niches with Fashion as the worked example + a per-niche
  branch converter (`scripts/new-niche.sh`).
- ✅ CI/CD: Dashboard checks, Python test matrix, docs site deploy, tagged
  releases.
- Harden offline test coverage across all Python components.
- A one-command local bootstrap (hub + Dashboard) for first-time users.

## Next

- **More platforms.** X and YouTube niche configs ship today; deepen their
  scrapers and normalizers to parity with Instagram.
- **The three template producers** (spun from `_producer-template/` on demand):
  - `proposal-content` — original script proposals grounded in winning factors,
    behind the human gate.
  - `creative-idea` — net-new viral concepts cross-referencing factors, formulas,
    and trending audio.
  - `template-content` — apply a reference video's structure to your own topic.
- **Containerization** — a `docker-compose` that brings up the hub + Dashboard so
  running the pipeline doesn't require a manual multi-terminal setup.
- **Pluggable model backends** — make the analysis/generation model providers
  swappable via config rather than per-agent code.

## Later

- A hosted demo / sample corpus (fully synthetic) so newcomers can explore the
  Dashboard without scraping anything.
- Evaluation dashboards for producer quality over time.
- A producer marketplace pattern: discover and install community producers.
- Optional hub/scraper deployment split (only worth it once there's a second
  content-source repo — see the README architecture note).

## Non-goals

- Bundling real scraped data or targeting lists in a **public** release. Examples stay
  synthetic. (`demo-data/` is a deliberate, documented exception while the repo is private
  — see [`demo-data/README.md`](demo-data/README.md); it must be replaced or purged from
  history before publishing.)
- Storing secret values in the hub. Secrets are referenced by env-var name only.
- Breaking the HTTP-hub boundary between agents for convenience.
