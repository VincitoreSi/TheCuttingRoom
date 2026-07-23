# Niches

The pipeline is **niche-agnostic**: it scrapes creators in one *niche*, scores every post for
virality, and turns the winners into blueprints. Which niche it targets is defined by a single
human-editable file per niche, in the repo's `niches/` directory:

```
niches/
  fashion.yaml     # the canonical example — Fashion is the default on `main`
  football.yaml
  cricket.yaml
  travel.yaml
```

Each `*.yaml` is the **one source of truth** for a niche. `scripts/new-niche.sh` reads it and
writes it into the pipeline's real per-platform config on a dedicated git branch — so every
niche becomes "the same full pipeline, in its own branch."

!!! tip "Fashion is the default"
    `main` ships with **Fashion** as the worked example. Other niches are applied onto their own
    `niche/<niche>` branch, leaving `main` untouched.

## The `*.yaml` schema

A niche file has a display `niche` name and a `platforms` block for the three fixed platforms
(`instagram`, `x`, `youtube`). Everything inside a platform block is niche-tunable:

```yaml
niche: Fashion                       # display name, written into every niche_config.json

platforms:
  instagram:
    reels_per_creator: 100           # per-creator scrape limit
                                     # (x: posts_per_creator, youtube: shorts_per_creator)
    discovery:                       # instagram-only keyword discovery (OFF by default)
      keywords: [fashion, ootd, ...] # relevance keywords for auto-discovery — the ONLY
                                     # discovery field the live AutoSearch agent reads
      seeds: []                      # LEGACY: read only by the superseded offline discover.py
    weights:                         # the 4 virality signals (auto-normalized to sum to 1)
      reach_multiplier: 0.35         #   plays / followers
      outlier_score:    0.25         #   plays / that creator's median plays
      engagement_rate:  0.25         #   (likes+comments+shares+saves) / followers
      velocity:         0.15         #   plays / days since posting
    tiers:                           # score → label buckets (0-100)
      - { label: Viral,         min_score: 85 }
      - { label: High,          min_score: 70 }
      - { label: Above Average, min_score: 50 }
      - { label: Normal,        min_score: 0 }
    seed_pages:                      # EXAMPLE handles → starter pages.txt (replace them!)
      - "@example.fashion.brand"
  x:      { ... same knobs ... }
  youtube:{ ... same knobs ... }
```

!!! note "Discovery: only `keywords` is live"
    The live discovery agent is **AutoSearch** (reached through the hub's Discover stage), and it
    reads **only `discovery.keywords`**. The other `discovery.*` fields (`seeds`, and any legacy
    `search_terms`/`per_query`/`min_followers`/`guest_only` mechanics) are **legacy** — read only
    by the superseded offline `platforms/instagram/discover.py`. They have **not** been removed:
    `niches/fashion.yaml` still carries `keywords` **and** `seeds`, and AutoSearch's own runtime
    knobs (caps, `guest_only`, the `discovery_enabled` kill-switch, optional Gemini term-expansion)
    live on the auto-search agent config (`GET/PUT /api/config/agent/auto-search`). Only the
    shipped `niche_config.json` has been trimmed to a **keywords-only** `discovery` block.

!!! tip "The media tier gate — `virality.media_filter`"
    `niche_config.json`'s `virality.media_filter` decides **which clips get their video downloaded
    and sent to (paid) analysis**: `min_tier` (a label from `tiers`), optional `min_score` (a
    numeric override of that tier's cutoff), and optional `max_downloads` (a cap applied *after*
    the gate). Widen `min_tier` (e.g. `High`) to analyze more; tighten it (e.g. `Viral`) to spend
    less. It is a live per-niche knob today but currently lives **only in `niche_config.json`** —
    it is not part of the `*.yaml` schema or the `new-niche.sh` generator, so a freshly generated
    niche inherits the value already present in the preserved config file.

### Niche-specific vs fixed fields

The script only swaps the **niche-specific** values into each platform's
`ReelScraper/platforms/<p>/niche_config.json`; the file's **structure and comment fields are
preserved**.

| Niche-specific (set from the YAML)                | Fixed / preserved (untouched by the script)                       |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| `niche` display name                              | `_comment_discovery`, `_comment_virality`, `_note` comment fields |
| `reels_/posts_/shorts_per_creator` limit          | `discovery.enabled` + mechanics (`search_terms`, `per_query`, …)  |
| `discovery.keywords`, `discovery.seeds` (IG only) | `virality.top_n`                                                  |
| `virality.weights`, `virality.tiers`              | the JSON key order / overall shape                                |

**Tuning weights per niche is the point.** Sports clips (football, cricket) are velocity-driven
and spike fast, so velocity + reach are up-weighted; travel is slow-burn and evergreen, so
velocity is down-weighted and engagement/outlier carry more of the score.

!!! warning "Handle rules for `seed_pages`"
    So the scrapers accept them: Instagram allows dots (`@example.fashion.brand`); **X** allows
    **no dots and max 15 chars** (`@ex_fashion_1`); YouTube allows dots and hyphens
    (`@example.style.tv`). All shipped example handles are obviously fake — replace them with
    real creators.

## How `new-niche.sh` works

```bash
./scripts/new-niche.sh <niche>       # <niche> must match niches/<niche>.yaml
```

1. **Validates** `niches/<niche>.yaml` exists (lists available niches if not).
2. **Creates + switches to a new git branch** `niche/<niche>` — and *refuses to clobber* an
   existing branch of that name (re-running while already on it re-applies in place).
3. **Writes each `niche_config.json`** from the YAML, overlaying only niche-specific values onto
   the existing file (comments/structure preserved). Uses `python3` + PyYAML, falling back to
   `uv run` if `python3` lacks PyYAML.
4. **Writes each `pages.txt`** from `seed_pages`, with a header explaining these are examples.
5. **Stages** the changes (`git add`) and prints next steps. It does **not** commit or push.

The result: **Fashion stays the default on `main`**; each other niche lives as a full,
ready-to-run pipeline on its own `niche/<niche>` branch.

## Add a brand-new niche

```bash
cp niches/fashion.yaml niches/skincare.yaml
# edit niches/skincare.yaml — set `niche:`, swap keywords, tune weights, add example seed_pages
./scripts/new-niche.sh skincare
```

## Worked example

```bash
./scripts/new-niche.sh cricket
```

produces, on a new `niche/cricket` branch:

```
ReelScraper/platforms/instagram/niche_config.json   niche="Cricket", cricket keywords, sport-tuned weights
ReelScraper/platforms/x/niche_config.json           niche="Cricket", velocity-led weights
ReelScraper/platforms/youtube/niche_config.json     niche="Cricket", reach/outlier-led weights
ReelScraper/platforms/<p>/pages.txt                 example @handles to replace with real creators
```

Then, per the printed next steps:

```bash
# 1. put REAL creator handles in each platforms/<p>/pages.txt
# 2. set up secrets:  cp .env.example .env   (X needs a burner session)
cd ReelScraper
uv run cli.py scrape  instagram
uv run cli.py analyze instagram
uv run cli.py media   instagram
uv run cli.py start        # prints HUB_URL=http://127.0.0.1:<port> (8787 if free)
```

## Running two niches at once

One clone per niche. `new-niche.sh` branches a whole pipeline, so the clean way to work on
Fashion and Fitness at the same time is two checkouts, each on its own branch:

```bash
git clone git@github.com:VincitoreSi/TheCuttingRoom.git fashion
git clone git@github.com:VincitoreSi/TheCuttingRoom.git fitness
cd fitness && ./scripts/new-niche.sh fitness && ./init
```

They cannot touch each other. Nothing in this project writes outside its own directory —
no shared cache, no file in `$HOME` — so the only thing two clones share is the loopback
network. Two things follow from that, and `./init` handles both:

**Each checkout owns a port.** The first clone takes 8787, the second 8788, and the choice
is pinned in that clone's `ReelScraper/.env` as `HUB_PORT`. It is a *pin*, not a race: the
port stays the same across restarts, so it can be bookmarked and pointed at. Set `HUB_PORT`
by hand (or `./init --port 8790`) to choose your own.

**Each checkout's agents dial their own hub.** `./init` writes `BACKEND_API` into every
component's `.env`. This is the one that matters: `cd SimilarContent && uv run cli.py
propose` resolves the hub from that file, and a `.env` copied between clones — or a stale
`export BACKEND_API=` in your shell — would post this niche's proposals into the other
niche's studio, against the other niche's corpus, with every call returning 200.

So the agents check. Each one asks `GET /api/hub` at startup and refuses to run if the hub
belongs to a different checkout:

```
ERROR: http://127.0.0.1:8788 is a different checkout's hub.
  it serves:   /Users/you/fitness/ReelScraper
  this agent:  /Users/you/fashion/AnalysisEngine
```

!!! tip "Telling the two boards apart"
    Two Dashboards look identical. The sidebar footer carries the niche name from
    `niche_config.json` above the host and port, which is the quickest way to know which
    one you are looking at.

`./stop` and `./clean` are scoped the same way — they match processes by working directory,
never by command line, so stopping one clone never touches the other's hub.
