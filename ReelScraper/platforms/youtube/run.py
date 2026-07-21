#!/usr/bin/env python3
"""platforms/youtube/run.py — youtube driver on the shared core (same CLI as every platform)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.runner import run_cli
from normalize import load_records
if __name__ == "__main__":
    run_cli("youtube", Path(__file__).parent, load_records)
