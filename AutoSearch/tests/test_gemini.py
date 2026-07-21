#!/usr/bin/env python3
"""tests/test_gemini.py — mocked-Gemini unit test (AutoSearch/PIPELINE.md §7.6). No key, no
network — a fake HTTP transport is injected via `GeminiClient(transport=...)`.

Runnable two ways:
  * pytest:  `pytest -q`  (collects the `test_*` functions below)
  * script:  `python -m tests.test_gemini`  (exit 0 = pass, 1 = fail)

Verifies:
  1. The request goes to `:generateContent` for the configured model, asks for JSON, and
     carries a `responseSchema` stripped of the keywords Gemini's subset rejects.
  2. The key is never placed in a header or body — it is a query parameter, and it must not
     leak into the payload.
  3. 429/5xx-class responses drive the `CircuitBreaker`; 3 consecutive failures raise
     `CircuitTripped`, and a success resets the strike count.

Replaces tests/test_claude.py. The breaker assertions are deliberately identical to that
file's: swapping providers must not have changed the failure contract the run loop relies on.
"""
from __future__ import annotations

import json
import sys

from engine.circuit import CircuitBreaker, CircuitTripped
from engine.gemini import DEFAULT_MODEL, GeminiClient, GeminiError, _to_gemini_schema


def _check(label: str, cond: bool, detail: str = "") -> None:
    assert cond, (label + (f"  {detail}" if detail else ""))


def _ok_body(obj: dict) -> str:
    """A minimal well-formed generateContent success envelope."""
    return json.dumps({
        "candidates": [{"finishReason": "STOP",
                        "content": {"parts": [{"text": json.dumps(obj)}]}}],
        "usageMetadata": {"totalTokenCount": 42},
    })


class _FakeTransport:
    """Records every (url, payload); can be configured to fail N times before succeeding."""

    def __init__(self, bodies=None, fail_times: int = 0, fail_status: int = 429):
        self.calls: list[tuple[str, dict]] = []
        self.bodies = list(bodies or [])
        self.fail_times = fail_times
        self.fail_status = fail_status
        self._failed = 0

    def __call__(self, url: str, payload: dict):
        self.calls.append((url, payload))
        if self._failed < self.fail_times:
            self._failed += 1
            return self.fail_status, '{"error": {"message": "rate limited"}}'
        return 200, (self.bodies.pop(0) if self.bodies else _ok_body({}))


SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
          "additionalProperties": False}


def test_request_shape() -> None:
    t = _FakeTransport(bodies=[_ok_body({"x": "y"})])
    client = GeminiClient(model=DEFAULT_MODEL, transport=t,
                          breaker=CircuitBreaker(max_strikes=3, pace_seconds=0.0))
    out = client._call("system prompt", "user prompt", SCHEMA)
    _check("parsed JSON output round-trips", out == {"x": "y"}, str(out))

    url, payload = t.calls[0]
    _check(f"calls generateContent for {DEFAULT_MODEL}",
           f"/models/{DEFAULT_MODEL}:generateContent" in url, url)
    gc = payload.get("generationConfig") or {}
    _check("asks for JSON back", gc.get("responseMimeType") == "application/json", str(gc))
    _check("sends a responseSchema", "responseSchema" in gc, str(list(gc)))
    _check("system_instruction carried", "system prompt" in json.dumps(payload["system_instruction"]))


def test_schema_is_stripped_for_gemini() -> None:
    """`additionalProperties` and friends make the API 400, which would silently turn every
    expansion into a fallback-to-seeds and look like the feature simply not working."""
    stripped = _to_gemini_schema(SCHEMA)
    _check("additionalProperties removed", "additionalProperties" not in stripped, str(stripped))
    _check("type survives", stripped.get("type") == "object", str(stripped))
    _check("properties survive", "x" in (stripped.get("properties") or {}), str(stripped))
    _check("required survives", stripped.get("required") == ["x"], str(stripped))

    nested = {"type": "object", "additionalProperties": False,
              "properties": {"a": {"type": "array", "minItems": 1,
                                   "items": {"type": "object", "additionalProperties": False,
                                             "properties": {"b": {"type": "string"}}}}}}
    out = _to_gemini_schema(nested)
    _check("nested additionalProperties removed", "additionalProperties" not in json.dumps(out), json.dumps(out))
    _check("nested minItems removed", "minItems" not in json.dumps(out), json.dumps(out))


def test_api_key_never_travels_in_the_payload() -> None:
    t = _FakeTransport(bodies=[_ok_body({"x": "y"})])
    client = GeminiClient(model=DEFAULT_MODEL, api_key="SEKRIT-not-a-real-key", transport=t,
                          breaker=CircuitBreaker(max_strikes=3, pace_seconds=0.0))
    client._call("s", "u", SCHEMA)
    _, payload = t.calls[0]
    _check("the key is not in the request body", "SEKRIT" not in json.dumps(payload),
           json.dumps(payload)[:200])


def test_breaker_trips_after_3_failures() -> None:
    t = _FakeTransport(fail_times=3)
    breaker = CircuitBreaker(max_strikes=3, pace_seconds=0.0)
    client = GeminiClient(model=DEFAULT_MODEL, transport=t, breaker=breaker)

    tripped = False
    gemini_errors = 0
    for _ in range(3):
        try:
            client._call("s", "u", SCHEMA)
        except CircuitTripped:
            tripped = True
            break
        except GeminiError:
            gemini_errors += 1

    _check("3 consecutive 429/5xx-class failures raise CircuitTripped", tripped)
    _check("the breaker strikes on each failed call (strikes recorded before trip)",
           gemini_errors == 2, f"got {gemini_errors} GeminiError(s) before trip")


def test_success_resets_breaker() -> None:
    t = _FakeTransport(bodies=[_ok_body({}), _ok_body({})], fail_times=2)
    breaker = CircuitBreaker(max_strikes=3, pace_seconds=0.0)
    client = GeminiClient(model=DEFAULT_MODEL, transport=t, breaker=breaker)

    for _ in range(2):
        try:
            client._call("s", "u", SCHEMA)
        except GeminiError:
            pass
    _check("2 failures alone do not trip the breaker", not breaker.tripped, str(breaker.strikes))
    client._call("s", "u", SCHEMA)
    _check("a success resets strikes to 0", breaker.strikes == 0, str(breaker.strikes))


_TESTS = (
    test_request_shape,
    test_schema_is_stripped_for_gemini,
    test_api_key_never_travels_in_the_payload,
    test_breaker_trips_after_3_failures,
    test_success_resets_breaker,
)


def main() -> int:
    """Legacy CLI entry point (kept so `python -m tests.test_gemini` still works)."""
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
