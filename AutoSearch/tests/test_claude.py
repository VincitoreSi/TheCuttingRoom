#!/usr/bin/env python3
"""tests/test_claude.py — mocked-Claude unit test (AutoSearch/PIPELINE.md §7.6). No
ANTHROPIC_API_KEY, no network — a fake client is injected via `ClaudeClient(client=...)`.

Runnable two ways:
  * pytest:  `pytest -q`  (collects the `test_*` functions below)
  * script:  `python -m tests.test_claude`  (exit 0 = pass, 1 = fail)

Verifies:
  1. `messages.create` is called with `model=claude-opus-4-8`,
     `output_config.format.type == "json_schema"`, and NO `thinking` kwarg.
  2. 429/5xx-class errors drive the `CircuitBreaker`; 3 consecutive failures raise
     `CircuitTripped`.
"""
from __future__ import annotations

import sys

from engine.circuit import CircuitBreaker, CircuitTripped
from engine.claude import ClaudeClient, ClaudeError, DEFAULT_MODEL


def _check(label: str, cond: bool, detail: str = "") -> None:
    assert cond, (label + (f"  {detail}" if detail else ""))


# ---- fakes: no dependency on real anthropic types --------------------------------------
class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason
        self.stop_details = None


class _FakeRateLimitError(Exception):
    """Stands in for anthropic.RateLimitError / a 5xx APIStatusError — ClaudeClient treats
    ANY exception from `messages.create` as breaker-strike-worthy, so the fake need not be
    a real anthropic exception subclass."""


class _FakeMessages:
    """Records every call's kwargs; can be configured to raise N times before succeeding."""

    def __init__(self, responses=None, raise_times: int = 0):
        self.calls: list[dict] = []
        self.responses = list(responses or [])
        self.raise_times = raise_times
        self._raised = 0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raised < self.raise_times:
            self._raised += 1
            raise _FakeRateLimitError("429 Too Many Requests")
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


def test_request_shape() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
              "additionalProperties": False}
    fake_messages = _FakeMessages(responses=[_FakeResponse('{"x": "y"}')])
    client = ClaudeClient(
        model=DEFAULT_MODEL, client=_FakeClient(fake_messages),
        breaker=CircuitBreaker(max_strikes=3, pace_seconds=0.0),
    )
    out = client._call("system prompt", "user prompt", schema)
    _check("parsed JSON output round-trips", out == {"x": "y"}, str(out))

    kwargs = fake_messages.calls[0]
    _check("messages.create called with model=claude-opus-4-8",
           kwargs.get("model") == "claude-opus-4-8", str(kwargs.get("model")))
    fmt = ((kwargs.get("output_config") or {}).get("format") or {})
    _check("output_config.format.type == 'json_schema'", fmt.get("type") == "json_schema", str(fmt))
    _check("`thinking` is OMITTED from the request", "thinking" not in kwargs,
           str(list(kwargs.keys())))


def test_breaker_trips_after_3_failures() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    fake_messages = _FakeMessages(raise_times=3)
    breaker = CircuitBreaker(max_strikes=3, pace_seconds=0.0)
    client = ClaudeClient(model=DEFAULT_MODEL, client=_FakeClient(fake_messages), breaker=breaker)

    tripped = False
    claude_errors = 0
    for _ in range(3):
        try:
            client._call("s", "u", schema)
        except CircuitTripped:
            tripped = True
            break
        except ClaudeError:
            claude_errors += 1

    _check("3 consecutive 429/5xx-class failures raise CircuitTripped", tripped)
    _check("the breaker actually strikes on each failed call (strikes recorded before trip)",
           claude_errors == 2, f"got {claude_errors} ClaudeError(s) before trip")


def test_success_resets_breaker() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    fake_messages = _FakeMessages(responses=[_FakeResponse("{}"), _FakeResponse("{}")],
                                  raise_times=2)
    breaker = CircuitBreaker(max_strikes=3, pace_seconds=0.0)
    client = ClaudeClient(model=DEFAULT_MODEL, client=_FakeClient(fake_messages), breaker=breaker)

    # 2 failures (below the 3-strike trip), then a success resets strikes to 0.
    for _ in range(2):
        try:
            client._call("s", "u", schema)
        except ClaudeError:
            pass
    _check("2 failures alone do not trip the breaker", not breaker.tripped, str(breaker.strikes))
    client._call("s", "u", schema)  # success
    _check("a success resets strikes to 0", breaker.strikes == 0, str(breaker.strikes))


_TESTS = (
    test_request_shape,
    test_breaker_trips_after_3_failures,
    test_success_resets_breaker,
)


def main() -> int:
    """Legacy CLI entry point (kept so `python -m tests.test_claude` still works)."""
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
