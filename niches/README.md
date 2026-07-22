# Niches

This pipeline is **niche-agnostic**. It scrapes creators in one *niche*, scores every post
for virality, and turns the winners into blueprints. Which niche it targets is defined by a
single human-editable file per niche in this directory:

```
niches/
  fashion.yaml     ← the canonical example (Fashion is the default on `main`)
  football.yaml
  cricket.yaml
  travel.yaml
```

Each `*.yaml` is the **one source of truth** for a niche. `scripts/new-niche.sh` reads it and
writes it into the pipeline's real per-platform config, on a dedicated git branch — so every
niche becomes "the same full pipeline, in its own branch."

---

## The `*.yaml` schema

A niche file has a display `niche` name and a `platforms` block for the three fixed platforms
(`instagram`, `x`, `youtube`). Everything inside a platform block is niche-tunable:

```yaml
niche: Fashion                       # display name, written into every niche_config.json

platforms:
  instagram:
    reels_per_creator: 100           # per-creator scrape limit (x: posts_per_creator,
                                     #                            youtube: shorts_per_creator)
    discovery:                       # instagram-only; keyword/seed discovery (OFF by default)
      keywords: [fashion, ootd, ...] # relevance keywords for auto-discovery
      seeds: []                      # seed profiles to expand from
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

### Which fields are niche-specific vs fixed

The script only swaps the **niche-specific** values into each platform's
`ReelScraper/platforms/<p>/niche_config.json`; the file's **structure and comment fields are
preserved**:

| Niche-specific (set from the YAML)                | Fixed / preserved (untouched by the script)                          |
| ------------------------------------------------- | -------------------------------------------------------------------- |
| `niche` display name                              | `_comment_discovery`, `_comment_virality`, `_note` comment fields    |
| `reels_/posts_/shorts_per_creator` limit          | `discovery.enabled` + mechanics (`search_terms`, `per_query`, …)     |
| `discovery.keywords`, `discovery.seeds` (IG only) | `virality.top_n`                                                     |
| `virality.weights`, `virality.tiers`              | the JSON key order / overall shape                                   |

**Tuning weights per niche** is the point: sports clips (football, cricket) are
velocity-driven and spike fast, so velocity + reach are up-weighted; travel is a slow-burn,
evergreen niche, so velocity is down-weighted and engagement/outlier carry more.

> **Handle rules for `seed_pages`** (so the scrapers accept them):
> Instagram allows dots (`@example.fashion.brand`); **X** allows no dots and max 15 chars
> (`@ex_fashion_1`); YouTube allows dots and hyphens (`@example.style.tv`). All example
> handles here are obviously fake — replace them with real creators.

---

## How `new-niche.sh` works

```
./scripts/new-niche.sh <niche>      # <niche> must match niches/<niche>.yaml
```

It:

1. **Validates** `niches/<niche>.yaml` exists (lists available niches if not).
2. **Creates + switches to a new git branch** `niche/<niche>` — and *refuses to clobber* an
   existing branch of that name (re-running while already on it re-applies in place).
3. **Writes each `ReelScraper/platforms/<p>/niche_config.json`** from the YAML, overlaying only
   the niche-specific values onto the existing file (comments/structure preserved). Uses
   `python3` + PyYAML (falls back to `uv run` if `python3` lacks PyYAML).
4. **Writes each `ReelScraper/platforms/<p>/pages.txt`** from `seed_pages`, with a header
   explaining these are examples to replace.
5. **Stages** the changed files (`git add`) and prints next steps. It does **not** commit or
   push — you review and commit.

Result: **Fashion stays the default on `main`**; each other niche lives as a full, ready-to-run
pipeline on its own `niche/<niche>` branch.

---

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

produces (on a new `niche/cricket` branch):

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
