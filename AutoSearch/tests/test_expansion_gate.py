#!/usr/bin/env python3
"""tests/test_expansion_gate.py — discovery must cost nothing unless asked.

The contract these lock down:

  * Keyword-only is the DEFAULT. `term_expansion_enabled` defaults to False, so a fresh
    install runs discovery on the operator's own seed keywords with no API call.
  * Having a key is NOT opting in. `GEMINI_API_KEY` is exported in this repo for
    AnalysisEngine and SimilarContent; if merely having it exported started billing
    discovery too, the default would be a lie. The flag is checked before the key, and this
    is the test that would fail if someone reordered them.
  * Enabled-but-keyless degrades to seeds and warns, rather than aborting a run.
  * The secret is declared OPTIONAL. It used to be `required: True` while every code path
    degraded without it, which made the Dashboard demand a paid key for a stage the hub
    marks unconditionally ready.
"""
from __future__ import annotations

import cli


NICHE = "example-niche"
SEEDS = ["seed one", "seed two", "seed one"]        # dupe on purpose


def _expand(monkeypatch, *, enabled: bool, key: bool, cfg_extra: dict | None = None):
    """Run _expand_terms with the client construction COUNTED, not raised.

    Counting matters: `_expand_terms` wraps its LLM path in `except Exception` and falls back
    to seeds, so a fake that raises is swallowed and every assertion on the return value
    passes whether or not the call was attempted. Asserting on the counter is the only way
    this test can fail when the gate is removed — verified by mutating the gate away.
    """
    built: list[dict] = []

    class _SpyClient:
        def __init__(self, **kw):
            built.append(kw)

        def expand_terms(self, *a, **k):
            # Must satisfy engine.schema.KEYWORD_EXPANSION_SCHEMA (all three keys required)
            # or _expand_terms rejects it and falls back to seeds — which would make this
            # test pass for the wrong reason.
            return {"keywords": ["llm-term"], "hashtags": [], "audio_terms": []}

    # Patch the ATTRIBUTE on the real module, not sys.modules. `_expand_terms` does
    # `from engine import gemini`, which resolves `gemini` as an attribute of the already
    # imported `engine` package — so a sys.modules entry is ignored the moment any other
    # test (test_gemini.py) has imported the real module first. That made this suite
    # order-dependent: green alone, failing after test_gemini ran.
    from engine import gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "GeminiClient", _SpyClient)
    monkeypatch.setattr(cli, "_gemini_present", lambda: key)

    cfg = {"term_expansion_enabled": enabled, **(cfg_extra or {})}
    terms = cli._expand_terms(cfg, NICHE, SEEDS, None, None)
    return terms, built


def test_default_is_keyword_only_and_spends_nothing(monkeypatch):
    """No flag at all — the out-of-the-box path."""
    monkeypatch.setattr(cli, "_gemini_present", lambda: False)
    terms = cli._expand_terms({}, NICHE, SEEDS, None, None)
    assert terms == ["seed one", "seed two"], terms


def test_a_present_key_alone_does_not_enable_spending(monkeypatch):
    """The important one. GEMINI_API_KEY is exported repo-wide for the other agents, so if
    merely having it exported started billing discovery, the documented default would be a
    lie. Asserts NO client was constructed, not merely that seeds came back."""
    terms, built = _expand(monkeypatch, enabled=False, key=True)
    assert built == [], f"constructed an LLM client despite term_expansion_enabled=False: {built}"
    assert terms == ["seed one", "seed two"], terms


def test_enabled_without_a_key_falls_back_to_seeds(monkeypatch):
    terms, built = _expand(monkeypatch, enabled=True, key=False)
    assert built == [], f"tried to build a client with no key: {built}"
    assert terms == ["seed one", "seed two"], terms


def test_opting_in_with_a_key_actually_calls_gemini(monkeypatch):
    """The other half: the switch must genuinely do something when turned on, or the whole
    feature is a no-op that nobody would notice."""
    terms, built = _expand(monkeypatch, enabled=True, key=True)
    assert len(built) == 1, f"expected exactly one client build, got {built}"
    assert terms == ["llm-term"], terms


def test_the_model_knob_reaches_the_client(monkeypatch):
    _, built = _expand(monkeypatch, enabled=True, key=True,
                       cfg_extra={"model": "gemini-2.5-pro"})
    assert built[0].get("model") == "gemini-2.5-pro", built


def test_seed_terms_dedupes_caps_and_never_returns_empty():
    assert cli._seed_terms(NICHE, ["a", "b", "a"]) == ["a", "b"]
    assert cli._seed_terms(NICHE, []) == [NICHE]
    assert len(cli._seed_terms(NICHE, [f"k{i}" for i in range(50)])) == 20


def test_the_gemini_secret_is_declared_optional():
    secrets = {s["env_var"]: s for s in cli._manifest()["secrets"]}
    assert "GEMINI_API_KEY" in secrets, list(secrets)
    assert secrets["GEMINI_API_KEY"]["required"] is False, secrets["GEMINI_API_KEY"]
    assert "ANTHROPIC_API_KEY" not in secrets, "the agent no longer uses Anthropic"


def test_config_schema_defaults_to_no_spend():
    props = cli.CONFIG_SCHEMA["properties"]
    assert props["term_expansion_enabled"]["default"] is False
    assert props["model"]["default"].startswith("gemini-"), props["model"]["default"]
