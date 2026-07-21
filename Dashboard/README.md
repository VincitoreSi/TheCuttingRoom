# The Cutting Room — Dashboard

The React control board for the virality pipeline. It reads and controls
everything over the hub's HTTP API (`/api/*` + SSE) — it never touches another
agent's files. In production it's served same-origin with the hub; in
development it runs on Vite and proxies to the hub.

## Stack

- React 18 + TypeScript, built with Vite 6
- Tailwind for styling, framer-motion for motion, Recharts for charts
- TanStack Query for data, Vitest for tests

## Develop

Requires **Node ≥ 20** (see `.nvmrc`). The hub must be running
(`cd ../ReelScraper && uv run cli.py start`).

```bash
npm ci
npm run dev            # http://localhost:5173
```

The dev server proxies **`/api`, `/media` and `/renders`** to the hub.

### The hub's port is not fixed

`cli.py start` prefers 8787 but **falls back to a free port** when it is busy,
printing the one it got as `HUB_URL=…`. `vite.config.ts` therefore resolves the
proxy target from the `BACKEND_API` environment variable via Vite's `loadEnv`
(with an empty prefix, so unprefixed vars are picked up), defaulting to
`http://127.0.0.1:8787`:

```bash
BACKEND_API=http://127.0.0.1:9123 npm run dev
```

This only matters in development. In production the hub serves the built
Dashboard same-origin from `ReelScraper/frontend/dist`, so there is no proxy
and no port to configure.

## Scripts

| Command          | What it does                                                                        |
| ---------------- | ----------------------------------------------------------------------------------- |
| `npm run dev`    | Vite dev server with HMR                                                            |
| `npm run build`  | Type-check (`tsc`) then production build                                            |
| `npm test`       | Run the Vitest suite                                                                |
| `npm run lint`   | ESLint over the project                                                             |
| `npm run format` | Prettier write (`format:check` to verify)                                           |
| `npm run deploy` | Build and copy `dist/` into `$BACKEND_DIR/frontend/dist` (default `../ReelScraper`) |

## Layout

- `src/views/` — one file per screen (Board home, Discovery, Corpus, Sounds,
  Studio, Producers, Activity, Evals, Playbook, Config, Agent desk).
- `src/components/` — shared components; `ui.tsx` holds the design-system
  primitives (Button, Card, Badge, SectionHead, …).
- `src/lib/` — data hooks, models, and helpers (`statusTone`, `evalModel`,
  `motion`, `url`, …).

## Design system notes

The UI uses a deliberate tailoring/measuring-tape motif with a fixed state-color
vocabulary — **oxblood** = working/running, **sage** = done/approved, **brass** =
accent, **danger** = error, **amber** = warn, **neutral** = idle. Status colors
are decided in one place (`src/lib/statusTone.ts`); use the shared primitives and
the `sectionMotion` entrance recipe rather than re-inlining variants, so the
board stays visually consistent. External/scraped URLs must pass through
`safeUrl()`.
