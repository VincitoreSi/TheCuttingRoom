# Contributing

Thanks for your interest in improving The Cutting Room! This project is a
set of independent agents that cooperate **only over an HTTP hub**. That one
rule shapes almost everything below.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## The one principle

The FastAPI hub inside **ReelScraper** (`http://127.0.0.1:8787`) is the single
integration point. Every agent reads and writes **only via `/api/*`** and never
touches another agent's files. When in doubt, if your change makes one component
import from or read the files of another, it's probably wrong — route it through
the hub instead.

## Project layout

| Directory | What it is | Stack |
| --- | --- | --- |
| `ReelScraper/` | The hub (`:8787`) + the scraper | Python ≥3.10, FastAPI, uv |
| `AnalysisEngine/` | Turns top clips into generation-ready blueprints | Python ≥3.10, uv |
| `AutoSearch/` | Discovers new creators in a niche | Python ≥3.10, uv |
| `SimilarContent/` | A producer that clones winning clips | Python ≥3.10 |
| `_producer-template/` | Copy this to build a new producer | Python |
| `Dashboard/` | "The Cutting Room" — React control board | Node ≥20, Vite, TS |
| `documentation/` | MkDocs site | Markdown |
| `niches/` | Niche definitions + the `new-niche.sh` converter | YAML + bash |

## Development setup

**Python components** (each is self-contained; use [uv](https://docs.astral.sh/uv/)):

```bash
cd ReelScraper && uv sync          # repeat per component
uv run pytest -q                   # offline unit tests (live smoke tests skip)
```

Live smoke tests that need a running hub or real API keys are skipped unless you
set `RUN_LIVE_SMOKE=1`.

**Dashboard:**

```bash
cd Dashboard
npm ci
npm run lint && npm test && npm run build

# The dev server proxies /api, /media and /renders to the hub. The hub's port is
# NOT fixed — `cli.py start` falls back to a free one and prints `HUB_URL=…`.
# Point the dev server at whatever it actually got:
BACKEND_API=http://127.0.0.1:8787 npm run dev     # http://localhost:5173
```

`BACKEND_API` defaults to `http://127.0.0.1:8787`, so you can omit it when the
hub did get that port.

**Secrets:** copy each `.env.example` to `.env` and fill in *your own* keys.
Never commit a `.env`, a `session.txt`, or any real key — see [SECURITY.md](SECURITY.md).
Use burner accounts for any platform session.

## Adding a producer

Producers are replaceable by design. To add one:

```bash
cp -r _producer-template MyProducer
# edit MyProducer/agent.json  (name/kind/consumes/human_gate/needs_reference/…)
# edit MyProducer/CLAUDE.md   (fill in the Method section for your producer's kind)
```

A producer self-registers with the hub on startup, reads only its declared
`consumes` inputs, and writes proposals via `POST /api/studio/{platform}`. The
Dashboard picks it up automatically. See the docs site (`documentation/`) for
the full Producer SPI.

## Changing the niche

Fashion is the worked example on `main`. To target a different vertical:

```bash
./scripts/new-niche.sh cricket     # branches niche/cricket with a full config
```

See [`niches/README.md`](niches/README.md) for the niche schema and how to add
your own.

## Pull requests

- Keep each PR focused on one logical change.
- Match the surrounding code's style. Run the linters/formatters before pushing
  (`npm run lint` / `npm run format` for Dashboard; keep Python idiomatic).
- Update docs when behavior or config changes.
- Make sure CI is green — Dashboard checks and the Python matrix must pass.
- Never include secrets or real scraped/personal data (real handles, content IDs,
  captions). Use synthetic fixtures.

## Reporting bugs and requesting features

Use the issue templates. For security issues, follow [SECURITY.md](SECURITY.md)
and report privately rather than opening a public issue.
