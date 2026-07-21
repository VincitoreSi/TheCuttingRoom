#!/usr/bin/env python3
"""
platforms/instagram/run.py — Instagram driver on the shared core.

  python run.py analyze                 # scraped reels -> Virality_Analysis.xlsx (+ memory)
  python run.py search "linen outfit"   # recall past reels from this platform's memory
  python run.py insight finding "short hook <2s lifts reach" --tags hook
  python run.py insights                 # show the shared cross-platform exchange

Scraping/discovery run via their own guest-safe scripts:
  python scrape.py --file pages.txt
  python discover.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # monorepo root -> `core.*`

from core.runner import run_cli
from normalize import load_records

if __name__ == "__main__":
    run_cli("instagram", Path(__file__).parent, load_records)
