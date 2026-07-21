#!/usr/bin/env python3
"""scripts/check-keys.py — prove the API keys on disk actually work.

A key that is *present* is not the same as a key that *works*. Pasted with a trailing
space, revoked, out of quota, from the wrong project, or valid for a different API than
the one this pipeline calls — every one of those looks identical to a correct key until an
agent finally uses it, which for the render path is after a blueprint has already been paid
for. This turns that into an immediate answer at setup time.

Each check is a READ-ONLY list/whoami call: it authenticates, costs nothing, generates no
tokens and creates no content. Where the provider exposes a model list, the models this
project is configured to use are checked against it too — an authenticated key that cannot
reach `gemini-2.5-flash-image` still cannot render a reel, and the failure is far more
legible here than mid-run.

Usage:
    python3 scripts/check-keys.py              # check everything found on disk
    python3 scripts/check-keys.py --only gemini
    python3 scripts/check-keys.py --quiet      # exit status only

Exit codes: 0 = every key found is usable (or none were found), 1 = at least one is
broken. Network failures are reported but never fail the run — being offline is not a
misconfiguration.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 15

# Where each key lives, and what this project does with it.
KEY_SOURCES = {
    "GEMINI_API_KEY": ["AnalysisEngine/.env", "SimilarContent/.env"],
    "ANTHROPIC_API_KEY": ["AutoSearch/.env"],
    "NVIDIA_API_KEY": ["SimilarContent/.env"],
    "HF_TOKEN": ["SimilarContent/.env"],
}

# Models the agents are configured to call. Auth alone is not enough — a key scoped to a
# project without image generation authenticates fine and then fails at render time.
GEMINI_MODELS = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-image"]
ANTHROPIC_MODELS = ["claude-opus-4-8"]

_C = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
G, Y, E, D, R = (("\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m") if _C
                 else ("", "", "", "", ""))


class Result:
    """OK / BAD / UNREACHABLE / ABSENT, plus a human-readable note."""

    def __init__(self, state: str, note: str = "", detail: str = ""):
        self.state, self.note, self.detail = state, note, detail

    @property
    def broken(self) -> bool:
        return self.state == "BAD"


def read_env_value(rel: str, var: str) -> str | None:
    """Pull `var` out of a KEY=VALUE .env without importing dotenv (stdlib-only house rule)."""
    path = ROOT / rel
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == var:
            # Strip quotes and whitespace: a trailing space or a copy-pasted pair of quotes
            # is one of the most common reasons a "correct" key 401s.
            return v.strip().strip("'\"")
    return None


def find_key(var: str) -> tuple[str | None, str]:
    """(value, where it came from). Environment wins — that is what a run would see."""
    if os.environ.get(var):
        return os.environ[var].strip(), "environment"
    for rel in KEY_SOURCES.get(var, []):
        val = read_env_value(rel, var)
        if val:
            return val, rel
    return None, ""


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _missing_models(available: list[str], wanted: list[str]) -> list[str]:
    """Substring match — providers version their ids (`models/gemini-2.5-pro-002`)."""
    return [w for w in wanted if not any(w in a for a in available)]


def check_gemini(key: str) -> Result:
    """models.list — free, and the response tells us what this key may actually call."""
    try:
        status, body = _get(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            {"x-goog-api-key": key},  # header, not ?key= — keeps it out of any URL log
        )
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return Result("UNREACHABLE", "could not reach Google", str(e))

    if status in (400, 401, 403):
        return Result("BAD", "rejected by Google (invalid, revoked, or wrong project)",
                      _api_message(body))
    if status == 429:
        return Result("OK", "valid, but rate-limited right now")
    if status != 200:
        return Result("BAD", f"unexpected HTTP {status}", _api_message(body))

    try:
        names = [m.get("name", "") for m in json.loads(body).get("models", [])]
    except (ValueError, AttributeError):
        return Result("OK", "valid (model list unreadable)")

    missing = _missing_models(names, GEMINI_MODELS)
    if missing:
        return Result("BAD", "valid, but cannot reach: " + ", ".join(missing),
                      "blueprints and rendering call these by name")
    return Result("OK", f"valid · {len(names)} models, all required ones present")


def check_anthropic(key: str) -> Result:
    try:
        status, body = _get("https://api.anthropic.com/v1/models?limit=100",
                            {"x-api-key": key, "anthropic-version": "2023-06-01"})
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return Result("UNREACHABLE", "could not reach Anthropic", str(e))

    if status in (401, 403):
        return Result("BAD", "rejected by Anthropic (invalid or revoked)", _api_message(body))
    if status != 200:
        return Result("BAD", f"unexpected HTTP {status}", _api_message(body))

    try:
        names = [m.get("id", "") for m in json.loads(body).get("data", [])]
    except (ValueError, AttributeError):
        return Result("OK", "valid (model list unreadable)")

    missing = _missing_models(names, ANTHROPIC_MODELS)
    if missing:
        # Not fatal: the model is configurable on the agent's desk, unlike Gemini's image
        # endpoint which the render path hardcodes.
        return Result("OK", "valid, but " + ", ".join(missing) + " is not available",
                      "pick another model on the Auto Search desk")
    return Result("OK", f"valid · {len(names)} models, {ANTHROPIC_MODELS[0]} available")


def check_nvidia(key: str) -> Result:
    """NVIDIA needs a POST, because its /v1/models is UNAUTHENTICATED.

    Measured: `GET /v1/models` returns 200 with a garbage bearer token — and with no
    Authorization header at all. Checking it would have reported every invalid key as
    valid, which is worse than not checking, so this posts instead.

    Auth is resolved before the model is, so a 401/403 means the key is bad while any other
    status means it authenticated. max_tokens=1 keeps the cost of a success negligible.
    """
    payload = json.dumps({
        "model": "meta/llama-3.1-8b-instruct",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    try:
        status, body = _post("https://integrate.api.nvidia.com/v1/chat/completions", payload,
                             {"Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"})
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return Result("UNREACHABLE", "could not reach NVIDIA", str(e))
    if status in (401, 403):
        return Result("BAD", "rejected by NVIDIA (invalid or revoked)", _api_message(body))
    return Result("OK", "valid")


def check_hf(key: str) -> Result:
    try:
        status, body = _get("https://huggingface.co/api/whoami-v2",
                            {"Authorization": f"Bearer {key}"})
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return Result("UNREACHABLE", "could not reach Hugging Face", str(e))
    if status in (401, 403):
        return Result("BAD", "rejected by Hugging Face (invalid or revoked)", _api_message(body))
    if status != 200:
        return Result("BAD", f"unexpected HTTP {status}", _api_message(body))
    return Result("OK", "valid")


def _api_message(body: bytes) -> str:
    """The provider's own error text, which usually names the real problem."""
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return body[:160].decode("utf-8", "replace").strip()
    for path in (("error", "message"), ("error", "type"), ("message",), ("detail",)):
        cur = d
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur:
            return cur[:200]
    return ""


CHECKS = {
    "gemini": ("GEMINI_API_KEY", check_gemini, True,
               "blueprints (AnalysisEngine), captions and image rendering (SimilarContent)"),
    "anthropic": ("ANTHROPIC_API_KEY", check_anthropic, False,
                  "creator discovery (AutoSearch)"),
    "nvidia": ("NVIDIA_API_KEY", check_nvidia, False,
               "FLUX image provider (SimilarContent) — only if you switch off nano_banana"),
    "huggingface": ("HF_TOKEN", check_hf, False, "Hugging Face image models (optional)"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the API keys on disk actually work.")
    ap.add_argument("--only", choices=sorted(CHECKS), help="check a single provider")
    ap.add_argument("--quiet", action="store_true", help="exit status only")
    args = ap.parse_args()

    names = [args.only] if args.only else list(CHECKS)
    broken = checked = 0

    if not args.quiet:
        print()
    for name in names:
        var, fn, required, purpose = CHECKS[name]
        key, origin = find_key(var)

        if not key:
            if not args.quiet:
                mark, colour = ("!", Y) if required else ("-", D)
                print(f"    {colour}{mark}{R} {var:<20} not set{D} — {purpose}{R}")
            continue

        checked += 1
        res = fn(key)
        if res.broken:
            broken += 1
        if args.quiet:
            continue

        mark, colour = {"OK": ("✓", G), "BAD": ("✗", E), "UNREACHABLE": ("?", Y)}[res.state]
        # The value itself is never printed — only where it was found.
        print(f"    {colour}{mark}{R} {var:<20} {res.note} {D}({origin}){R}")
        if res.detail:
            print(f"      {D}{res.detail}{R}")

    if not args.quiet:
        print()
        if broken:
            print(f"    {E}{broken} key(s) will not work.{R} Fix them before running the "
                  f"pipeline — an agent that starts with a bad key fails partway through a run.")
        elif checked:
            print(f"    {G}All {checked} key(s) verified against their provider.{R}")
        print()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
