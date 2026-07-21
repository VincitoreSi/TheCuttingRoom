#!/usr/bin/env bash
#
# scripts/deploy-dashboard.sh — publish Dashboard/dist into the hub's frontend/dist.
#
# Run from Dashboard/ (npm run deploy). Honours $BACKEND_DIR, default ../ReelScraper.
#
# WHY THIS IS NOT `rm -rf dist && cp -r`
# --------------------------------------
# The hub serves frontend/dist directly off disk, and rebuilding is something you do WHILE
# it is running. Deleting the directory and then copying a few MB back into it leaves a
# window — seconds, and far longer on a cold cache or a slow disk — in which every request
# for the dashboard 404s. Anyone with the tab open watches it go blank and stay blank until
# the copy finishes, with nothing to indicate that anything is happening.
#
# Copying to a sibling and renaming closes that window to a single rename() syscall. The
# directory is either entirely the old build or entirely the new one, never half of each —
# which also rules out serving an index.html whose hashed assets have not been copied yet.
set -euo pipefail

SRC="${1:-dist}"
BACKEND="${BACKEND_DIR:-../ReelScraper}"
DEST="$BACKEND/frontend/dist"
STAGE="$BACKEND/frontend/.dist.incoming"
OLD="$BACKEND/frontend/.dist.previous"

[ -d "$SRC" ] || { echo "deploy: no build at $SRC — run 'npm run build' first" >&2; exit 1; }
[ -f "$SRC/index.html" ] || { echo "deploy: $SRC has no index.html — build looks broken" >&2; exit 1; }

mkdir -p "$BACKEND/frontend"
rm -rf "$STAGE" "$OLD"
cp -R "$SRC" "$STAGE"

# Two renames, not one: rename() only replaces a directory atomically when the target is
# empty, so the live build is moved aside first. The gap between them is a syscall wide.
[ -d "$DEST" ] && mv "$DEST" "$OLD"
mv "$STAGE" "$DEST"
rm -rf "$OLD"

echo "deploy: $(find "$DEST" -type f | wc -l | tr -d ' ') files -> ${DEST}"
