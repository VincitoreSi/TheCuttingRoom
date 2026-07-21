#!/usr/bin/env python3
"""tests/test_schema.py — offline schema self-test (no network, no hub, no LLM).

Runnable two ways:
  * pytest:  `pytest -q`  (collects the `test_*` functions below)
  * script:  `python -m tests.test_schema`  (exit 0 = pass, 1 = fail)

Verifies (AutoSearch/PIPELINE.md §7.5):
  1. a good candidate payload (the pages.txt-matching full-URL `handle` form) validates.
  2. a bad candidate payload (bare-username handle, out-of-range score) is REJECTED.
  3. a good/bad term-expansion output validates/rejects.
  4. a good/bad relevance-scoring output validates/rejects.
  5. `candidate_id()` is stable/deterministic for the same (platform, handle).
"""
from __future__ import annotations

import sys

from engine import schema


def _check(label: str, cond: bool, detail: str = "") -> None:
    assert cond, (label + (f"  {detail}" if detail else ""))


def test_candidate_schema() -> None:
    good = {
        "candidate_id": "cand_deadbeef01",
        "handle": "https://www.instagram.com/some.creator",
        "platform": "instagram",
        "source_term": "finance",
        "discovered_via": "guest_hydration",
        "followers": 5000,
        "median_plays": 3200.0,
        "sample_reels": ["https://www.instagram.com/reel/abc123/"],
        "relevance": {"score": 0.72, "reasons": ["followers>=min", "median_plays>=min"]},
    }
    errs = schema.validate_candidate(good)
    _check("good candidate validates clean", not errs, "; ".join(errs))

    bad = {
        "candidate_id": None,
        "handle": "some.creator",  # bare username — NOT the pages.txt-matching full URL
        "relevance": {"score": 4.5, "reasons": "not-a-list"},
    }
    errs = schema.validate_candidate(bad)
    _check("bad candidate (bare-username handle) REJECTED", bool(errs), f"{len(errs)} error(s)")
    has_handle_err = any("handle" in e for e in errs)
    _check("rejection specifically flags `handle`", has_handle_err)


def test_keyword_expansion_schema() -> None:
    good = {"keywords": ["finance tips", "budgeting"], "hashtags": ["#finance"], "audio_terms": ["lofi"]}
    errs = schema.validate_keyword_expansion(good)
    _check("good keyword-expansion output validates clean", not errs, "; ".join(errs))

    bad = {"keywords": "not-a-list", "extra_field": "not allowed"}  # missing required + wrong type
    errs = schema.validate_keyword_expansion(bad)
    _check("bad keyword-expansion output REJECTED", bool(errs), f"{len(errs)} error(s)")


def test_relevance_score_schema() -> None:
    good = {"scores": [{"handle": "nasa", "score": 0.9, "reasons": ["on-topic", "high engagement"]}]}
    errs = schema.validate_relevance_scores(good)
    _check("good relevance-score output validates clean", not errs, "; ".join(errs))

    bad = {"scores": [{"handle": "nasa", "score": 1.5, "reasons": "not-a-list"}]}  # score out of range
    errs = schema.validate_relevance_scores(bad)
    _check("bad relevance-score output REJECTED", bool(errs), f"{len(errs)} error(s)")


def test_candidate_id_stable() -> None:
    a = schema.candidate_id("instagram", "https://www.instagram.com/nasa")
    b = schema.candidate_id("instagram", "https://www.instagram.com/nasa")
    c = schema.candidate_id("instagram", "https://www.instagram.com/other")
    _check("candidate_id is deterministic for the same input", a == b, f"{a} == {b}")
    _check("candidate_id differs for a different handle", a != c, f"{a} != {c}")
    _check("candidate_id has the hub's cand_ prefix", a.startswith("cand_"), a)


def test_to_pages_handle() -> None:
    _check("to_pages_handle normalizes bare username",
           schema.to_pages_handle("nasa") == "https://www.instagram.com/nasa")
    _check("to_pages_handle strips a leading @",
           schema.to_pages_handle("@nasa") == "https://www.instagram.com/nasa")


_TESTS = (
    test_candidate_schema,
    test_keyword_expansion_schema,
    test_relevance_score_schema,
    test_candidate_id_stable,
    test_to_pages_handle,
)


def main() -> int:
    """Legacy CLI entry point (kept so `python -m tests.test_schema` still works)."""
    failures = []
    for fn in _TESTS:
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
