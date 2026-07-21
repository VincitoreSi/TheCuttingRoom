#!/usr/bin/env bash
#
# new-niche.sh — spin up a full pipeline run for a different NICHE on its own branch.
#
#   ./scripts/new-niche.sh <niche>
#
# where <niche> matches a file in niches/<niche>.yaml. It:
#   1. validates niches/<niche>.yaml exists,
#   2. creates + switches to a new git branch  niche/<niche>  (refuses to clobber),
#   3. writes each platform's ReelScraper/platforms/<p>/niche_config.json from the YAML
#      (preserving the existing JSON structure/comment fields — only niche values change),
#   4. writes starter ReelScraper/platforms/<p>/pages.txt from the YAML's seed_pages,
#   5. stages the changes (git add) and prints next steps. It does NOT commit.
#
# The result: Fashion stays the default on main; each niche lives as a similar full
# pipeline in its own `niche/<niche>` branch.
#
set -euo pipefail

# ── locate repo root (this script lives in <root>/scripts/) ──────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd -P)"
NICHES_DIR="$REPO_ROOT/niches"

die() { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

list_niches() {
  if compgen -G "$NICHES_DIR"/*.yaml >/dev/null 2>&1; then
    for f in "$NICHES_DIR"/*.yaml; do
      printf '  - %s\n' "$(basename "$f" .yaml)"
    done
  else
    printf '  (none found in %s)\n' "$NICHES_DIR"
  fi
}

# ── argument ─────────────────────────────────────────────────────────────────────
if [[ $# -ne 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat >&2 <<EOF
usage: ./scripts/new-niche.sh <niche>

Available niches:
$(list_niches)
EOF
  exit 2
fi

NICHE="$1"
NICHE_FILE="$NICHES_DIR/$NICHE.yaml"
BRANCH="niche/$NICHE"

# ── validate niche file ──────────────────────────────────────────────────────────
if [[ ! -f "$NICHE_FILE" ]]; then
  printf 'error: no niche file at %s\n\nAvailable niches:\n' "$NICHE_FILE" >&2
  list_niches >&2
  printf '\nTo add a new one: cp niches/fashion.yaml niches/%s.yaml && edit it.\n' "$NICHE" >&2
  exit 1
fi

# ── must be inside the git repo ──────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || die "git is not installed"
git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$REPO_ROOT is not a git repository"

# ── pick a python that can import yaml (python3 preferred; else uv run) ───────────
PY=()
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
  PY=(python3)
elif command -v uv >/dev/null 2>&1; then
  # ReelScraper's uv env ships pyyaml; run the transform there.
  PY=(uv run --project "$REPO_ROOT/ReelScraper" python)
else
  die "need Python with PyYAML. Install it (e.g. 'python3 -m pip install pyyaml') or install 'uv'."
fi

# ── create/switch branch (refuse to clobber an existing one) ─────────────────────
CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [[ "$CURRENT_BRANCH" == "$BRANCH" ]]; then
  printf 'note: already on branch %s — re-applying niche files in place.\n' "$BRANCH"
elif git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
  die "branch '$BRANCH' already exists. Switch to it (git switch $BRANCH) or delete it first — refusing to clobber."
else
  git -C "$REPO_ROOT" switch -c "$BRANCH" >/dev/null 2>&1 \
    || git -C "$REPO_ROOT" checkout -b "$BRANCH" >/dev/null 2>&1 \
    || die "could not create branch '$BRANCH'"
  printf 'created + switched to branch %s\n' "$BRANCH"
fi

# ── transform: YAML → per-platform niche_config.json + pages.txt ─────────────────
# The Python overlays niche-specific values onto the EXISTING niche_config.json so all
# structural/comment fields (_comment_*, discovery mechanics, top_n, _note) are preserved.
"${PY[@]}" - "$NICHE_FILE" "$REPO_ROOT" <<'PYEOF'
import json, sys
from pathlib import Path
import yaml

niche_file = Path(sys.argv[1])
repo_root  = Path(sys.argv[2])

data = yaml.safe_load(niche_file.read_text(encoding="utf-8")) or {}
niche_name = data.get("niche") or niche_file.stem.title()
platforms  = data.get("platforms") or {}

# per-platform key that holds the per-creator limit in niche_config.json
LIMIT_KEYS = {
    "instagram": "reels_per_creator",
    "x":         "posts_per_creator",
    "youtube":   "shorts_per_creator",
}

changed = []
for platform, pconf in platforms.items():
    pdir = repo_root / "ReelScraper" / "platforms" / platform
    if not pdir.is_dir():
        print(f"  ! skipping unknown platform '{platform}' (no {pdir})", file=sys.stderr)
        continue
    pconf = pconf or {}

    # 1) niche_config.json — load existing to preserve structure, then overlay.
    cfg_path = pdir / "niche_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}

    cfg["niche"] = niche_name

    # per-creator limit (accept either the platform-specific key or a generic one)
    limit_key = LIMIT_KEYS.get(platform, "items_per_creator")
    limit_val = pconf.get(limit_key)
    if limit_val is None:
        for alt in ("reels_per_creator", "posts_per_creator", "shorts_per_creator", "items_per_creator"):
            if alt in pconf:
                limit_val = pconf[alt]; break
    if limit_val is not None:
        cfg[limit_key] = int(limit_val)

    # virality weights + tiers
    vir = cfg.setdefault("virality", {})
    if "weights" in pconf:
        vir["weights"] = {k: float(v) for k, v in pconf["weights"].items()}
    if "tiers" in pconf:
        vir["tiers"] = pconf["tiers"]

    # discovery block (instagram only in the shipped config)
    disc = pconf.get("discovery")
    if disc and isinstance(cfg.get("discovery"), dict):
        if "keywords" in disc:
            cfg["discovery"]["keywords"] = list(disc["keywords"])
        if "seeds" in disc:
            cfg["discovery"]["seeds"] = list(disc["seeds"])

    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    changed.append(str(cfg_path.relative_to(repo_root)))

    # 2) pages.txt — starter list of EXAMPLE seed handles.
    seeds = pconf.get("seed_pages") or []
    pages_path = pdir / "pages.txt"
    header = (
        f"# {niche_name} — starter creator list for {platform} (one handle/URL per line).\n"
        f"# These are EXAMPLE/placeholder handles generated from niches/{niche_file.stem}.yaml.\n"
        f"# Replace them with real creators before running a real scrape. Lines starting\n"
        f"# with '#' are ignored.\n"
    )
    body = "\n".join(str(s) for s in seeds)
    pages_path.write_text(header + (body + "\n" if body else ""), encoding="utf-8")
    changed.append(str(pages_path.relative_to(repo_root)))

print("\n".join(changed))
PYEOF

# ── stage the changed files (do NOT commit) ──────────────────────────────────────
for p in instagram x youtube; do
  for f in niche_config.json pages.txt; do
    tgt="ReelScraper/platforms/$p/$f"
    [[ -f "$REPO_ROOT/$tgt" ]] && git -C "$REPO_ROOT" add -- "$tgt" 2>/dev/null || true
  done
done

# ── summary + next steps ─────────────────────────────────────────────────────────
cat <<EOF

──────────────────────────────────────────────────────────────────────────────
  Niche "$NICHE" applied on branch  $BRANCH
──────────────────────────────────────────────────────────────────────────────
  Wrote (and staged) for instagram / x / youtube:
    • niche_config.json   ← niche name, weights, tiers, per-creator limits
                            (instagram also: discovery keywords/seeds)
    • pages.txt           ← EXAMPLE seed handles — replace with real creators

  Next steps:
    1. Edit each ReelScraper/platforms/<p>/pages.txt with REAL creator handles.
    2. Configure secrets (cp .env.example .env; X needs a burner session — see
       ReelScraper/platforms/x/scrape.py header).
    3. Run the pipeline:
         cd ReelScraper
         uv run cli.py scrape  instagram
         uv run cli.py analyze instagram
         uv run cli.py media   instagram
         uv run cli.py start        # → http://127.0.0.1:8787
    4. Review the staged changes (git diff --staged) and commit when happy.

  Changes are staged but NOT committed — nothing was pushed.
──────────────────────────────────────────────────────────────────────────────
EOF
