"""Secret status must describe now, not the last time an agent happened to register.

The bug: paste a Gemini key into `./init`, watch it verify against Google, open the Agent
Desk — "SECRET MISSING". The endpoint replayed `present` from `producers/registry.json`,
which is whatever the agent self-reported when it last registered. AnalysisEngine had
registered before the key existed and had no reason to run since, so the snapshot said
false forever. At the same moment the Board's readiness check, reading the .env directly,
said the key was there. Two code paths, one question, opposite answers.

Presence is never a value: these tests assert on booleans, and the key text must not appear
in any response.
"""
import json

import pytest

from api import app as appmod


SENTINEL = "test-key-value-must-never-be-returned"


@pytest.fixture
def registered(hub):
    """An agent in the registry that declares one required secret, self-reported ABSENT —
    exactly the state ./init leaves behind when the agent has not run since."""
    reg = hub.root / "producers" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({
        "analysis-engine": {
            "name": "analysis-engine", "kind": "analyzer",
            "secrets": [{"name": "gemini_api_key", "env_var": "GEMINI_API_KEY",
                         "required": True, "present": False}],
        }
    }), encoding="utf-8")
    return hub


def _status(hub):
    r = hub.get("/api/config/agent/analysis-engine/secrets/status")
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_a_key_written_after_registration_is_seen(registered, monkeypatch, tmp_path):
    """The reported bug, end to end."""
    agent_dir = tmp_path / "AnalysisEngine"
    agent_dir.mkdir()
    monkeypatch.setattr(appmod, "_agent_env_dir", lambda a, m=None: agent_dir)

    assert _status(registered)["present"] is False, "no .env yet"

    (agent_dir / ".env").write_text(f"GEMINI_API_KEY={SENTINEL}\n", encoding="utf-8")
    assert _status(registered)["present"] is True


def test_the_value_is_never_returned(registered, monkeypatch, tmp_path):
    agent_dir = tmp_path / "AnalysisEngine"
    agent_dir.mkdir()
    (agent_dir / ".env").write_text(f"GEMINI_API_KEY={SENTINEL}\n", encoding="utf-8")
    monkeypatch.setattr(appmod, "_agent_env_dir", lambda a, m=None: agent_dir)

    body = registered.get("/api/config/agent/analysis-engine/secrets/status").text
    assert SENTINEL not in body
    assert "present" in body


def test_an_empty_placeholder_is_not_a_key(registered, monkeypatch, tmp_path):
    """.env.example ships `GEMINI_API_KEY=` — present as a line, absent as a key."""
    agent_dir = tmp_path / "AnalysisEngine"
    agent_dir.mkdir()
    (agent_dir / ".env").write_text("# GEMINI_API_KEY=commented\nGEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(appmod, "_agent_env_dir", lambda a, m=None: agent_dir)

    assert _status(registered)["present"] is False


def test_the_hub_environment_also_counts(registered, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_agent_env_dir", lambda a, m=None: tmp_path / "nope")
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL)
    assert _status(registered)["present"] is True


def test_a_live_miss_never_overrules_the_agents_own_yes(hub, monkeypatch, tmp_path):
    """The agent can see sources the hub cannot — AutoSearch accepts a session.txt, and
    AnalysisEngine accepts GOOGLE_API_KEY while its manifest names only GEMINI_API_KEY.
    Reporting a working agent as broken would be worse than the bug being fixed."""
    reg = hub.root / "producers" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({
        "analysis-engine": {
            "name": "analysis-engine",
            "secrets": [{"name": "gemini_api_key", "env_var": "GEMINI_API_KEY",
                         "required": True, "present": True}],
        }
    }), encoding="utf-8")
    monkeypatch.setattr(appmod, "_agent_env_dir", lambda a, m=None: tmp_path / "empty")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert _status(hub)["present"] is True


def test_unknown_agent_still_404s(hub):
    assert hub.get("/api/config/agent/nobody/secrets/status").status_code == 404


# ---- directory resolution -------------------------------------------------------------

def test_known_agents_resolve_to_their_sibling_directory():
    assert appmod._agent_env_dir("analysis-engine").name == "AnalysisEngine"
    assert appmod._agent_env_dir("auto-search").name == "AutoSearch"
    assert appmod._agent_env_dir("similar-content").name == "SimilarContent"


def test_a_manifest_dir_wins_for_a_producer_outside_the_map():
    d = appmod._agent_env_dir("some-producer", {"dir": "SimilarContent"})
    assert d is not None and d.name == "SimilarContent"


def test_a_traversing_manifest_dir_is_refused():
    """Same containment rule the render launcher applies: a direct sibling, nothing else."""
    assert appmod._agent_env_dir("evil", {"dir": "../../etc"}) is None
    assert appmod._agent_env_dir("evil", {"dir": "/etc"}) is None


def test_an_unknown_agent_with_no_dir_cannot_be_resolved():
    assert appmod._agent_env_dir("mystery-agent") is None
    assert appmod._secret_present("SOME_KEY", None) is None
