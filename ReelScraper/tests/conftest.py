"""Shared fixtures for the hub's API tests.

`api.app` resolves every path off a module-level `ROOT`, so the tests repoint that at a
tmp_path and rebuild the handful of derived path constants. That keeps the suite from
reading (or worse, writing) the developer's real corpus, studio and render stores.
"""
import importlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A TestClient bound to an isolated ROOT, plus the module itself for path access."""
    from fastapi.testclient import TestClient
    import api.app as appmod

    importlib.reload(appmod)
    monkeypatch.setattr(appmod, "ROOT", tmp_path)
    monkeypatch.setattr(appmod, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(appmod, "PRODUCERS_FILE", tmp_path / "producers" / "registry.json")
    monkeypatch.setattr(appmod, "LOGS_FILE", tmp_path / "logs" / "agents.jsonl")
    # Routes that read FRONTEND at call time (e.g. /favicon.ico) must see the isolated
    # tree, not the developer's real build — otherwise a test asserting "not built yet"
    # passes or fails depending on whether someone has run `npm run deploy`.
    monkeypatch.setattr(appmod, "FRONTEND", tmp_path / "frontend" / "dist")
    for sub in ("studio/instagram", "renders", "media/instagram", "producers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    # `pdir` validates the platform by checking platforms/<p> exists
    (tmp_path / "platforms" / "instagram").mkdir(parents=True, exist_ok=True)

    client = TestClient(appmod.app)
    client.mod = appmod
    client.root = tmp_path
    return client


@pytest.fixture
def approved_item(hub):
    """A studio item that has cleared the human gate — the precondition for rendering."""
    name = "2026-07-19-similar-test-clip-123456789012.md"
    hub.post("/api/studio/instagram",
             json={"filename": name, "text": "# Clone recipe — test\n",
                   "agent": "similar-content", "kind": "clone"})
    hub.post(f"/api/studio/instagram/{name}/status", json={"status": "approved"})
    return name
