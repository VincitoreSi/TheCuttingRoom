# `demo-data/` — where the pre-filled dataset goes

This is the **clean, shareable build**: the real scraped dataset does **not** live in this
repo. `data/` is intentionally empty here.

The dataset travels separately as **`demodataset.zip`** — real scraped content is only shared
privately, with people the owner chooses. It is **never attached to a GitHub release**: release
assets on a public repo are downloadable by anyone, which is exactly what this policy exists to
prevent.

**To request a copy**, open a
[Discussion](https://github.com/VincitoreSi/TheCuttingRoom/discussions) explaining what you want
it for. If you were given a copy of that zip:

```bash
# put demodataset.zip in the repo root (next to ./demo), then:
./demo
```

`./demo` unpacks it into `demo-data/data/`, copies that over the working tree, and opens on a
fully populated dashboard — a scored corpus, Gemini blueprints, clone recipes at the human
gate, rendered reels that play, evals, and an activity log.

**No dataset?** You don't need one to run the project:

```bash
./init                                              # clean, empty studio
# then add creator handles and let the pipeline fill it:
#   1. edit ReelScraper/platforms/instagram/pages.txt
#   2. run scrape → analyze → media → blueprints from the Board
```

## What `demodataset.zip` contains

Unzipped at the repo root, it restores:

```
demo-data/
  manifest.json    what was captured, how big, what was stripped
  data/            mirrors the repo layout; ./demo copies it into place
    ReelScraper/platforms/instagram/content.json   scored corpus
    ReelScraper/media/instagram/                   posters + the analyzed clips
    ReelScraper/analysis/instagram/                schema-2 blueprints
    ReelScraper/studio/instagram/                  proposals + gate decisions
    ReelScraper/renders/                           generated reels (mp4 + poster + record)
    ReelScraper/{evals,config,producers,discovery,logs}/
```

## Why it ships separately

The snapshot is **real**, so the demo looks like the product rather than lorem ipsum. That
means it carries real Instagram creator handles and captions, real engagement metrics, and
AI-generated reels derived from specific third-party videos. Republishing that publicly would
expose other people's content and personal data without consent and would likely breach
Instagram's terms — so the public repo ships without it, and the dataset is handed out only
privately.

Signed CDN links (`_nc_ohc`, `oh=`, `oe=`), every `.env`/`session.txt`, agent memory
databases, the raw scrape dump and the xlsx/csv exports are all stripped from the snapshot at
capture time; only local posters plus the clips that have blueprints are kept.

## Producing / refreshing `demodataset.zip`

After a real pipeline run, capture a fresh snapshot and zip it:

```bash
python3 scripts/capture-demo.py --dry-run   # what it would take, and how big
python3 scripts/capture-demo.py             # writes demo-data/data/
# then, from the repo root:
zip -r demodataset.zip demo-data/data demo-data/manifest.json
```

Keep `demodataset.zip` out of the repo (it is gitignored) — hand it to people directly.
