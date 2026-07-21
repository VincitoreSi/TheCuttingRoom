#!/usr/bin/env python3
"""tests/test_smoke_hub.py — pytest wrapper around the live hub round-trip smoke test.

The full smoke logic lives in `tests/smoke_hub.py` (still runnable directly with
`python -m tests.smoke_hub`). This wrapper exposes it to pytest but SKIPS it by default:
it needs a running hub at BACKEND_API (127.0.0.1:8787). Opt in with RUN_LIVE_SMOKE=1:

    RUN_LIVE_SMOKE=1 pytest -q tests/test_smoke_hub.py
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.live_smoke
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1",
    reason="live smoke test; set RUN_LIVE_SMOKE=1 to run",
)
def test_hub_round_trip() -> None:
    from tests import smoke_hub

    assert smoke_hub.main() == 0, "hub smoke test reported failures"
