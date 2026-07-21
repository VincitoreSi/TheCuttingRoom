#!/usr/bin/env python3
"""core/atomicio.py — write a file so that nothing can ever see it half-written.

`Path.write_text` truncates the target at `open()`, BEFORE a single byte of the new
content lands. Everything in this repo that matters is written that way, and the most
important of them is `platforms/<p>/reels_raw.json` — which is not a cache or a report,
it IS the corpus. The failure this module exists to prevent, in the order it happened:

  1. a signal (or a crash, or a full disk) lands in that window,
  2. `reels_raw.json` is left short — valid bytes, invalid JSON,
  3. the next scrape's `except Exception: raw_all = {}` reads the ruin as an EMPTY corpus,
  4. that run writes `{}` over every creator that survived step 2, and logs DONE with rc 0.

There was no error anywhere in that chain. The Stop button added in this same change makes
a signal an ordinary, expected, HUMAN-INITIATED event, so this stopped being a rare crash
window and became a routine one. A Stop button that can truncate the corpus is worse than
no Stop button, which is why this file is a prerequisite for it rather than a follow-up.

The precedent is already in the repo: `api/app.py`'s render-asset writer does
`tmp.write_bytes(raw)` then `os.replace(tmp, dest)` for exactly this reason. This module
is that pattern, named, so the remaining sites can adopt it without each re-deriving it.

Deliberately NOT fsync-ing: the threat model is a process dying (SIGTERM/SIGKILL/crash),
not the machine losing power. Once `write_text` returns, the bytes are in the kernel's page
cache and survive the process by definition, so `os.replace` — which is atomic within a
filesystem — is sufficient and does not cost a disk flush per creator on a large corpus.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

# The temp suffix is APPENDED to the whole filename, never infixed before the extension.
# This is the single sharpest edge in this module. The resume glob in the Instagram scraper
# is `reels_raw*.json`, the hub's corpus counters use `*_raw*.json`, and every normalizer
# reads `profiles_meta*.json` / `posts_raw*.json` / `shorts_raw*.json`. A "safe-looking"
# temp named `reels_raw.tmp.json` MATCHES ALL OF THOSE: it would be read back as a corpus
# shard, inflating `_scraped_count`, making `_has_raw_scrape` true off a half-written file,
# and feeding a partial dump to analyze. That fails as WRONG NUMBERS, never as an error —
# strictly worse than the corruption this module was written to fix. `reels_raw.json.part`
# matches none of them, and `.part` is likewise invisible to `glob("*.mp4")`.
PART_SUFFIX = ".part"


def part_path(path) -> Path:
    """The temp path this module writes through, for a given destination."""
    p = Path(path)
    return p.with_name(p.name + PART_SUFFIX)


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` so a reader — or a signal — sees the old file or the new one.

    Same filesystem by construction (the temp is a sibling), which is what makes the
    `os.replace` atomic; a temp in /tmp would degrade to a copy and reopen the window."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = part_path(p)
    try:
        tmp.write_text(text, encoding=encoding)
    except BaseException:
        # Clean up before re-raising. A failure here (encoding error, full disk) has already
        # created and truncated the temp, and nothing globs a `.part` — so a leftover would
        # sit next to the corpus forever, read by nothing and cleaned by nothing.
        tmp.unlink(missing_ok=True)
        raise
    replace_atomic(tmp, p)


def write_bytes_atomic(path, data: bytes) -> None:
    """Byte counterpart of `write_text_atomic`."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = part_path(p)
    try:
        tmp.write_bytes(data)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace_atomic(tmp, p)


@contextlib.contextmanager
def atomic_path(dest):
    """Yield a temp path to write, then promote it to `dest` — or clean it up on failure.

    For writers that insist on owning the file handle themselves. openpyxl's
    `Workbook.save` opens a ZipFile on the path it is given and streams the whole workbook
    into it, so the destination is truncated for the ENTIRE serialization — a far wider
    window than a json dump — and the only way to close it is to hand openpyxl a temp path.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = part_path(dest)
    try:
        yield tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace_atomic(tmp, dest)


def replace_atomic(tmp, dest) -> None:
    """Promote a temp file to its final name, cleaning the temp up if the promotion fails.

    Exposed separately for writers whose temp is not produced by this module at all — the
    hub's reference downloader hands yt-dlp a `.part` path and has to promote whatever
    yt-dlp actually left behind.

    Leaving a failed temp behind would be its own bug: `reels_raw.json.part` is skipped by
    every glob (that is the whole point of the suffix), so nothing would ever read it, and
    nothing would ever clean it up either."""
    tmp, dest = Path(tmp), Path(dest)
    try:
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
