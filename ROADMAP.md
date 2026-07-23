# Roadmap

This is a direction sketch, not a promise. Priorities shift with contributions
and real-world use. Have an idea? Open a discussion or an issue.

Tracked on the [Roadmap board](https://github.com/users/VincitoreSi/projects/7), which
mirrors the sections below: *Next* → Next, *Later* → Backlog.

## Now (foundation)

- ✅ Clean open-source release: curated history, MIT license, contributor docs.
- ✅ Config-driven niches with Fashion as the worked example + a per-niche
  branch converter (`scripts/new-niche.sh`).
- ✅ CI/CD: Dashboard checks, Python test matrix, docs site deploy, tagged
  releases.
- ✅ Containerization: `docker-compose` + `./cr` bring up the hub + Dashboard,
  a multi-arch image publishes to GHCR on every release, and the README documents
  both lanes — so running the pipeline needs no manual multi-terminal setup.
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
  synthetic, `demo-data/data/` ships empty, and **no release attaches `demodataset.zip`** —
  release assets on a public repo are downloadable by anyone, which is the whole point of
  the policy. The dataset is shared privately on request; see
  [`demo-data/README.md`](demo-data/README.md). A fully synthetic corpus that *could* be
  published is the "Later" item above.
- Storing secret values in the hub. Secrets are referenced by env-var name only.
- Breaking the HTTP-hub boundary between agents for convenience.
