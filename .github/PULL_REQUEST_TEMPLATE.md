<!-- Thanks for contributing! Keep PRs focused — one logical change per PR. -->

## What & why

<!-- What does this change do, and why? Link any related issue: Closes #123 -->

## Component(s) touched

- [ ] ReelScraper (hub / scraper)
- [ ] AnalysisEngine
- [ ] AutoSearch
- [ ] SimilarContent / producer
- [ ] Dashboard
- [ ] Docs / CI / tooling

## Checklist

- [ ] I kept the change scoped and only touched files relevant to it.
- [ ] No secrets, API keys, session cookies, or personal data are included.
- [ ] Agents still integrate **only over the HTTP hub** (no cross-directory file access).
- [ ] Docs updated if behavior or config changed.
- [ ] Tests / checks pass locally (see below).

## How I tested

<!--
Dashboard:  cd Dashboard && npm ci && npm run lint && npm test && npm run build
Python:     cd <Component> && uv sync && uv run pytest -q
-->
