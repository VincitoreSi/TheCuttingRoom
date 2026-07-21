"""Regression tests for the shell entry points and scripts/_common.sh.

These are not hub tests, but they live here because ReelScraper's suite is the one `./health`
and CI always run, and the bugs below are the kind that only ever show up on someone else's
machine: a GNU/BSD `mktemp` difference, and a git that answers "I could not tell you" where
the caller heard "no".

Everything runs against a COPY of the entry points in tmp_path. Nothing here may touch the
developer's real tree — `./clean` deletes things for a living.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "scripts" / "_common.sh"
CLEAN = REPO / "clean"
HEALTH = REPO / "health"


def _bash(script, cwd, env=None, **kw):
    """Run a bash -c snippet and hand back the CompletedProcess."""
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], cwd=str(cwd), env=e,
                          capture_output=True, text=True, **kw)


# --------------------------------------------------------------------------- mktemp
# `mktemp -t vp-setup` is valid BSD (a bare PREFIX) and invalid GNU (a TEMPLATE needing at
# least three trailing X's). Every developer here is on macOS; every container is not. The
# shim below turns that platform difference into something testable on either.
GNU_MKTEMP_SHIM = """#!/bin/sh
# Reject a template with fewer than three trailing X's, exactly as GNU coreutils does.
for a in "$@"; do
  case "$a" in
    -*)     ;;
    *XXX*)  ;;
    *)      echo "mktemp: too few X's in template '$a'" >&2; exit 1 ;;
  esac
done
exec "$REAL_MKTEMP" "$@"
"""


@pytest.fixture
def strict_mktemp(tmp_path):
    """A PATH whose `mktemp` refuses BSD-only templates."""
    real = shutil.which("mktemp")
    assert real, "mktemp is not on PATH"
    bin_dir = tmp_path / "strictbin"
    bin_dir.mkdir()
    shim = bin_dir / "mktemp"
    shim.write_text(GNU_MKTEMP_SHIM)
    shim.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REAL_MKTEMP": real}


def test_the_strict_mktemp_shim_actually_rejects_the_old_form(strict_mktemp, tmp_path):
    """Control. Without this, a green test below could just mean the shim does nothing."""
    r = _bash("mktemp -t vp-setup", tmp_path, strict_mktemp)
    assert r.returncode != 0
    assert "too few X's" in r.stderr


def test_spun_works_under_a_strict_mktemp(strict_mktemp, tmp_path):
    """The live bug: under GNU mktemp `spun` lost its log file and misreported the failure.

    spun is always the left operand of a `||`, which suspends errexit for its whole body, so
    the empty $log turned into `>""`, a return of 1, and four swallowed `uv sync` failures
    that the run finally blamed on `npm ci`.
    """
    r = _bash(f'set -euo pipefail; ROOT={REPO!s}; . "{COMMON}"; spun "syncing" true; echo RAN',
              tmp_path, strict_mktemp)
    assert r.returncode == 0, r.stderr
    assert "RAN" in r.stdout
    assert "too few X's" not in r.stderr


def test_no_mktemp_template_in_the_repo_is_bsd_only():
    """Belt and braces for the next one somebody adds."""
    import re
    bad = []
    for sh in [CLEAN, HEALTH, COMMON, *(REPO / "scripts").glob("*.sh"),
               REPO / "init", REPO / "demo", REPO / "stop", REPO / "docsite"]:
        for n, line in enumerate(sh.read_text().splitlines(), 1):
            for m in re.finditer(r"mktemp\s+(?:-[a-z]+\s+)*-t\s+(\S+)", line):
                if "XXX" not in m.group(1):
                    bad.append(f"{sh.name}:{n}: {line.strip()}")
    assert not bad, "GNU mktemp rejects these templates:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- git_state
BROKEN_GIT = """#!/bin/sh
echo "fatal: detected dubious ownership in repository at '$PWD'" >&2
exit 128
"""


def _sandbox(tmp_path, name):
    """A tree holding just enough of the repo for ./clean to run."""
    root = tmp_path / name
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(CLEAN, root / "clean")
    shutil.copy2(COMMON, root / "scripts" / "_common.sh")
    (root / "stop").write_text("#!/bin/sh\nexit 0\n")
    (root / "stop").chmod(0o755)
    return root


def _broken_git_path(tmp_path):
    bin_dir = tmp_path / "brokenbin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(BROKEN_GIT)
    (bin_dir / "git").chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _git_state(root, env=None):
    r = _bash(f'ROOT="{root}"; . "{root}/scripts/_common.sh"; git_state', root, env)
    return r.stdout.strip()


def test_git_state_reports_ok_for_a_real_work_tree(tmp_path):
    root = _sandbox(tmp_path, "ok")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert _git_state(root) == "ok"


def test_git_state_reports_none_for_a_tarball(tmp_path):
    """No .git at all is a SUPPORTED way to run this — an archive download. Not an error."""
    root = _sandbox(tmp_path, "tarball")
    # A sandbox under /tmp is not inside any repository, so there is genuinely no git here.
    assert _git_state(root) == "none"


def test_git_state_reports_broken_when_git_cannot_read_a_real_dot_git(tmp_path):
    """The container case: a bind mount owned by another uid. git exits 128, not 1."""
    root = _sandbox(tmp_path, "broken")
    (root / ".git").mkdir()
    assert _git_state(root, _broken_git_path(tmp_path)) == "broken"


# --------------------------------------------------------------------------- ./clean
def test_clean_refuses_to_delete_anything_when_git_cannot_answer(tmp_path):
    """The live bug, end to end.

    `git ls-files --error-unmatch` exits 1 for "not tracked" and 128 for "I could not tell
    you". `if git … 2>/dev/null` read both as "not tracked", so under a dubious-ownership
    failure every tracked file was archived and then DELETED — including
    demo-data/data/.gitkeep, the one file whose entire job is to survive ./clean.
    """
    root = _sandbox(tmp_path, "refuse")
    (root / ".git").mkdir()
    keep = root / "demo-data" / "data" / ".gitkeep"
    keep.parent.mkdir(parents=True)
    keep.write_text("")
    sched = root / "ReelScraper" / "config" / "pipeline_schedule.json"
    sched.parent.mkdir(parents=True)
    sched.write_text("{}")

    r = _bash("./clean --yes </dev/null", root, _broken_git_path(tmp_path))

    assert r.returncode != 0, "clean must fail closed when git cannot answer"
    assert keep.exists(), "clean deleted a tracked file it could not verify"
    assert sched.exists(), "clean deleted data before establishing it could read git"
    assert not (root / "backups").exists(), "clean got as far as writing an archive"
    assert "git" in r.stderr.lower()


@pytest.mark.skipif(not (shutil.which("zip") and shutil.which("unzip")),
                    reason="./clean refuses to run without zip/unzip")
def test_clean_still_wipes_generated_data_when_git_works(tmp_path):
    """The fail-closed guard must not have turned ./clean into a no-op."""
    root = _sandbox(tmp_path, "happy")
    keep = root / "demo-data" / "data" / ".gitkeep"
    keep.parent.mkdir(parents=True)
    keep.write_text("")
    junk = root / "demo-data" / "data" / "scraped.json"
    junk.write_text("[]")
    logs = root / "ReelScraper" / "logs" / "agents.jsonl"
    logs.parent.mkdir(parents=True)
    logs.write_text("{}\n")

    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(git + ["add", "demo-data/data/.gitkeep"], check=True)
    subprocess.run(git + ["commit", "-qm", "seed"], check=True)

    r = _bash("./clean --yes </dev/null", root)

    assert r.returncode == 0, r.stdout + r.stderr
    assert keep.exists(), "the tracked .gitkeep must survive"
    assert not junk.exists(), "untracked data beside it must go"
    assert not logs.exists()
    assert list((root / "backups").glob("*.zip")), "the archive is written before deleting"


# --------------------------------------------------------------------------- ./health
def test_health_fails_rather_than_skips_when_git_is_broken():
    """A textual check, deliberately: running ./health for real means running everything.

    The behaviour under test is one `case` arm, and the behaviour it replaced was
    `git rev-parse … 2>/dev/null && IS_GIT=1`, which read a git that exited 128 as "this is
    not a work tree" and downgraded four invariants — the tracked-.env check, the history
    secret scan, the demo-dataset check and the working-data ignore check — to skips while
    the run still printed HEALTHY. git_state's own behaviour is covered above; this only
    pins that health branches on it and that the broken arm is a FAILURE, not a skip.
    """
    text = HEALTH.read_text()
    assert "git rev-parse --is-inside-work-tree >/dev/null 2>&1 ) && IS_GIT=1" not in text
    assert 'case "$(git_state)" in' in text
    broken = text[text.index('case "$(git_state)" in'):]
    broken = broken[:broken.index("esac")]
    assert "broken)" in broken and "fail" in broken.split("broken)")[1].split("\n")[0]


def _health_ignore_list():
    """The paths health's 'working data ignored' invariant checks."""
    import re
    text = HEALTH.read_text()
    # Start at `for p in`, not at the `bad=0; … missed=""` line above it: that empty pair of
    # quotes shifts the pairing and the regex then returns the whitespace BETWEEN the paths.
    start = text.index("for p in", text.index('bad=0; checked=0; missed=""'))
    body = text[start:text.index("; do", start)]
    return re.findall(r'"([^"]+)"', body)


def _data_paths():
    r = _bash(f'ROOT="{REPO}"; . "{COMMON}"; data_paths_unique', REPO)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _family(path):
    """(directory, kind) — so `Reels_Data.xlsx` and `Virality_Analysis.xlsx` are one case.

    data_paths() globs `*.xlsx` / `*.csv` / `*_raw*.json` out of whatever is on the running
    machine's disk, so the exact filenames vary per developer. The ignore RULES are per
    extension, so that is the granularity worth comparing at.
    """
    d, _, base = path.rpartition("/")
    if base.endswith(".xlsx"):
        return (d, "*.xlsx")
    if base.endswith(".csv"):
        return (d, "*.csv")
    if "_raw" in base and base.endswith(".json"):
        return (d, "*_raw*.json")
    return (d, base)


def test_health_checks_every_working_data_path_is_ignored():
    """health's list is hand-written; data_paths() is what ./clean actually wipes.

    An entry in data_paths() with no counterpart in health's list is a path that ./clean
    archives and deletes but that no invariant ever confirmed was gitignored — one
    `git add -A` after a pipeline run away from publishing scraped third-party content.
    That is precisely how the exports and renders/ got published before the rules existed.
    """
    listed = _health_ignore_list()
    assert len(listed) > 20, "the health list did not parse — fix this test, not the list"

    uncovered = []
    for entry in _data_paths():
        if any(h == entry or h.startswith(entry + "/") or _family(h) == _family(entry)
               for h in listed):
            continue
        uncovered.append(entry)
    assert not uncovered, (
        "data_paths() entries with no representative in health's 'working data ignored' "
        f"list: {uncovered}")


def test_every_working_data_path_is_actually_gitignored():
    """The property itself, checked against the real repo's rules.

    Directories are probed with a file beneath them: a rule like `logs/` cannot match a
    path git has no reason to believe is a directory, which is why health's list names
    files rather than directories.
    """
    if subprocess.run(["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        pytest.skip("not a git work tree")

    committable = []
    for entry in _data_paths():
        probe = entry if "." in entry.rpartition("/")[2] else entry + "/probe.json"
        if subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", probe],
                          capture_output=True).returncode != 0:
            committable.append(probe)
    assert not committable, f"working data that git would happily commit: {committable}"


# ------------------------------------------------------------- container-mode guard
# `./cr up` pins TCR_MODE=container into ReelScraper/.env, which lives on the bind mount and
# is therefore equally visible from inside the container. Only TCR_CONTAINER=1 (set by the
# image and by every compose service) distinguishes the lanes, so the guard needs both keys.
#
# These tests matter because the guard BLOCKS. A false positive locks a maintainer out of
# their own scripts; a false negative lets ./stop report success while the container it could
# never see keeps running.
ENTRY_POINTS = {
    "init": "./cr up",
    "demo": "./cr demo",
    "stop": "./cr down",
    "clean": None,          # no ./cr verb — the guard points at ./cr shell instead
    "health": "./cr health",
    "docsite": "./cr docsite",
}


def _guard(tmp_path, name, env=None, mode="container"):
    """Invoke container_mode_guard exactly as an entry point does, against a fake .env."""
    root = tmp_path / f"guard-{name}"
    (root / "scripts").mkdir(parents=True)
    (root / "ReelScraper").mkdir()
    shutil.copy2(COMMON, root / "scripts" / "_common.sh")
    if mode is not None:
        (root / "ReelScraper" / ".env").write_text(f"HUB_PORT=8787\nTCR_MODE={mode}\n")
    alt = ENTRY_POINTS[name]
    call = f'container_mode_guard {name} "{alt}"' if alt else f"container_mode_guard {name}"
    return _bash(f'ROOT="{root}"; . "{root}/scripts/_common.sh"; {call}; echo RAN', root, env)


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_guard_blocks_every_host_lane_script_in_container_mode(tmp_path, name):
    r = _guard(tmp_path, name)
    assert r.returncode != 0, f"./{name} ran on the host against a containerized checkout"
    assert "RAN" not in r.stdout
    assert "container mode" in r.stderr
    alt = ENTRY_POINTS[name]
    # The error must name the way forward, or it is just an obstacle.
    assert (alt or "./cr shell") in r.stderr


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_guard_is_silent_inside_the_container(tmp_path, name):
    """TCR_CONTAINER=1 means we ARE the container: this is the correct lane, not a mistake."""
    r = _guard(tmp_path, name, env={"TCR_CONTAINER": "1"})
    assert r.returncode == 0 and "RAN" in r.stdout, r.stderr


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_guard_is_silent_on_a_plain_host_checkout(tmp_path, name):
    """No TCR_MODE at all — the overwhelmingly common case, including CI. Must not trip."""
    r = _guard(tmp_path, name, mode=None)
    assert r.returncode == 0 and "RAN" in r.stdout, r.stderr


def test_guard_honours_the_force_host_escape_hatch(tmp_path):
    """A stale TCR_MODE must never be able to lock someone out of their own scripts."""
    r = _guard(tmp_path, "stop", env={"TCR_FORCE_HOST": "1"})
    assert r.returncode == 0 and "RAN" in r.stdout, r.stderr


def test_guard_ignores_a_non_container_tcr_mode(tmp_path):
    r = _guard(tmp_path, "stop", mode="host")
    assert r.returncode == 0 and "RAN" in r.stdout, r.stderr


def test_every_entry_point_actually_calls_the_guard():
    """The guard is worthless in a script that forgot to call it.

    Also asserts it is called AFTER the argument loop: `--help` must keep working on a
    containerized checkout, since that is exactly when someone is trying to work out what
    to run instead.
    """
    for name, alt in ENTRY_POINTS.items():
        text = (REPO / name).read_text()
        assert "container_mode_guard" in text, f"./{name} never calls container_mode_guard"
        if alt:
            assert f'container_mode_guard {name} "{alt}"' in text, \
                f"./{name} calls the guard with the wrong ./cr equivalent"
        guard_at = text.index("container_mode_guard")
        loop_end = text.index("\ndone\n")
        assert guard_at > loop_end, \
            f"./{name} calls the guard before parsing args — --help would be blocked"
