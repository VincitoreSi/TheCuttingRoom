#!/usr/bin/env bash
# Apply the git author identity from the repo-root .env (or .env.example fallback).
# Nothing is hardcoded: swap .env values to connect a different account, then re-run this.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=".env"; [ -f "$ENV_FILE" ] || ENV_FILE=".env.example"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
: "${GIT_AUTHOR_NAME:?set GIT_AUTHOR_NAME in .env}"
: "${GIT_AUTHOR_EMAIL:?set GIT_AUTHOR_EMAIL in .env}"
git config user.name  "$GIT_AUTHOR_NAME"
git config user.email "$GIT_AUTHOR_EMAIL"
echo "git identity set → $(git config user.name) <$(git config user.email)>  (from $ENV_FILE)"

# ── GitHub URLs ───────────────────────────────────────────────────────────────
# Files that must name the repo owner cannot read .env (GitHub renders CI badges,
# issue-template contact links and the docs site_url statically), so they ship the
# literal token GITHUB_USER and are rewritten here — once, from the same .env.
: "${GITHUB_USER:?set GITHUB_USER in .env}"
if [ "$GITHUB_USER" = "your-github-username" ]; then
  echo "GITHUB_USER is still the sample value — edit .env, then re-run. Skipping URL rewrite." >&2
  exit 0
fi

TARGETS="README.md CHANGELOG.md .github/ISSUE_TEMPLATE/config.yml documentation/mkdocs.yml"
rewrote=0
for f in $TARGETS; do
  [ -f "$f" ] || continue
  grep -q 'GITHUB_USER' "$f" || continue
  # BSD sed (macOS) and GNU sed (Linux) disagree on -i; write via a temp file instead.
  sed "s|GITHUB_USER|$GITHUB_USER|g" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  echo "  rewrote GITHUB_USER → $GITHUB_USER in $f"
  rewrote=1
done
[ "$rewrote" -eq 1 ] || echo "  no GITHUB_USER placeholders left to rewrite"
