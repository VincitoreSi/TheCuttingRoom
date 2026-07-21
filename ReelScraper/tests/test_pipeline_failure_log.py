"""A failed pipeline stage must surface on the central activity log.

Pipeline stages (scrape/analyze/media/…) are not registered agents and have no board of
their own, so before this a stage that died — e.g. `scrape` with no handles in pages.txt —
failed completely silently in the UI. `_run_job` now emits one `job_failed` lifecycle entry
(carrying the same error tail it stores on the job) so the Activity view shows what happened.
"""
import sys


def _seed_job(mod, job_id, stage="scrape"):
    mod.JOBS[job_id] = {"platform": "instagram", "stage": stage, "status": "queued",
                        "started": 0.0, "ended": None, "rc": None, "tail": ""}


def test_failed_job_is_logged_to_activity(hub):
    mod = hub.mod
    job_id = "job-fail-1"
    _seed_job(mod, job_id)
    # a real subprocess that exits non-zero with a recognisable message on stderr
    cmd = [sys.executable, "-c",
           "import sys; sys.stderr.write('no handles in pages.txt'); sys.exit(3)"]
    mod._run_job(job_id, cmd, hub.root)

    assert mod.JOBS[job_id]["status"] == "error"
    assert mod.JOBS[job_id]["rc"] == 3

    failed = [e for e in hub.get("/api/logs",
                                 params={"agent": "pipeline", "level": "error"}).json()
              if e.get("event") == "job_failed"]
    assert len(failed) == 1, failed
    e = failed[0]
    assert e["run_id"] == job_id
    assert e["platform"] == "instagram"
    assert e["data"]["stage"] == "scrape"
    assert e["data"]["rc"] == 3
    assert "no handles in pages.txt" in e["data"]["tail"]
    assert "scrape failed (rc 3)" in e["msg"]


def test_successful_job_logs_nothing_to_activity(hub):
    mod = hub.mod
    job_id = "job-ok-1"
    _seed_job(mod, job_id, stage="analyze")
    mod._run_job(job_id, [sys.executable, "-c", "print('ok')"], hub.root)

    assert mod.JOBS[job_id]["status"] == "done"
    failed = [e for e in hub.get("/api/logs").json() if e.get("event") == "job_failed"]
    assert failed == []
