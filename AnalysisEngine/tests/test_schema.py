#!/usr/bin/env python3
"""tests/test_schema.py — offline schema self-test (no Gemini, no hub, no network).

Runnable two ways:
  * pytest:  `pytest -q`  (collects the `test_*` functions below)
  * script:  `python -m tests.test_schema`  (exit 0 = pass, 1 = fail)

Verifies the two things the run loop depends on:
  1. a known-GOOD schema_version 2 blueprint validates cleanly, and
  2. a known-BAD one (placeholder shot_prompt_sequence + missing audio_type) is REJECTED,
     with the placeholder specifically flagged (the scratch's core defect).
"""
from __future__ import annotations

import sys

from engine import schema
from tests.fixture_blueprint import bad_blueprint, good_blueprint


def test_good_blueprint_validates_clean() -> None:
    """A schema-valid, semantically-clean schema_version 2 blueprint has no errors."""
    errs = schema.all_errors(good_blueprint())
    assert errs == [], "good blueprint reported errors: " + "; ".join(errs)


def test_bad_blueprint_is_rejected() -> None:
    """The bad blueprint's placeholder + missing audio_type must both hard-fail."""
    berrs = schema.all_errors(bad_blueprint())
    assert berrs, "bad blueprint was accepted — placeholder/audio defects not caught."

    has_placeholder = any(
        "shot_prompt_sequence" in e and "placeholder" in e.lower() for e in berrs
    )
    has_audiotype = any("audio_type" in e for e in berrs)
    assert has_placeholder, "expected a placeholder shot_prompt_sequence hard-fail: " + "; ".join(berrs)
    assert has_audiotype, "expected an audio_strategy.audio_type hard-fail: " + "; ".join(berrs)


def main() -> int:
    """Legacy CLI entry point (kept so `python -m tests.test_schema` still works)."""
    failures = []
    for fn in (test_good_blueprint_validates_clean, test_bad_blueprint_is_rejected):
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except AssertionError as e:
            failures.append(fn.__name__)
            print(f"FAIL: {fn.__name__}: {e}")
    print("\nRESULT:", "ALL PASS" if not failures else "FAILURES")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
