#!/usr/bin/env python3
"""tests/smoke_hub.py — live hub round-trip smoke test (no Gemini needed).

Run:  uv run python -m tests.smoke_hub

Exercises every hub write path AnalysisEngine uses, against the running hub, with dummy-but-valid
payloads and a clearly-synthetic content_id (`test_ae_smoke`):
  * register the analysis-engine manifest, then read its config + secret status,
  * POST a schema-valid schema_version 2 blueprint and read it back (assert schema_version == 2),
  * consume the analysis + reference pending queues (empty is fine),
  * POST a lifecycle log + a self-eval to the central stores.

NOTE: the hub has no DELETE endpoint and AnalysisEngine must not touch sibling files, so the
`test_ae_smoke` blueprint + the log/eval/registration rows persist on the hub after this runs.
"""
from __future__ import annotations

import os
import sys

from engine import AGENT_NAME, schema
from engine.hub import HubClient, HubError
from tests.fixture_blueprint import good_blueprint

CID = "test_ae_smoke"
PLATFORM = "instagram"


def main() -> int:
    base = os.environ.get("BACKEND_API", "http://127.0.0.1:8787")
    hub = HubClient(base)
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + f": {label}" + (f"  {detail}" if detail else ""))
        if not cond:
            ok = False

    check("hub reachable (GET /api/platforms)", hub.health_ok(), base)

    manifest = {
        "name": AGENT_NAME, "kind": "analyzer",
        "consumes": ["analysis_queue", "reference_queue", "media"],
        "human_gate": False, "needs_reference": False, "produces": "analysis_blueprint",
        "secrets": [{"name": "gemini_api_key", "env_var": "GEMINI_API_KEY",
                     "required": True, "present": False}],
    }
    try:
        hub.register_producer(manifest)
        check("POST /api/producers/register", True)
    except HubError as e:
        check("POST /api/producers/register", False, str(e))

    try:
        cfg = hub.get_agent_config(AGENT_NAME)
        check("GET /api/config/agent/analysis-engine", isinstance(cfg, dict), str(cfg)[:80])
    except HubError as e:
        check("GET /api/config/agent/analysis-engine", False, str(e))

    try:
        secrets = hub.secrets_status(AGENT_NAME)
        gk = next((s for s in secrets if s.get("env_var") == "GEMINI_API_KEY"), None)
        check("GET secrets/status returns GEMINI_API_KEY", gk is not None, str(secrets))
    except HubError as e:
        check("GET secrets/status", False, str(e))

    # Fixture must be schema-valid before we POST it.
    bp = good_blueprint(CID)
    errs = schema.all_errors(bp)
    check("fixture blueprint is schema-valid", not errs, "; ".join(errs))

    try:
        hub.post_analysis(PLATFORM, bp)
        check(f"POST /api/analysis/{PLATFORM}", True)
    except HubError as e:
        check(f"POST /api/analysis/{PLATFORM}", False, str(e))

    got = hub.get_analysis(PLATFORM, CID)
    check(f"GET /api/analysis/{PLATFORM}/{CID} reads back", bool(got),
          "" if got else "not found")
    if got:
        check("read-back has schema_version == 2", got.get("schema_version") == 2,
              f"schema_version={got.get('schema_version')}")
        check("read-back content_id matches", got.get("content_id") == CID)

    # Queues consume without error (empty is fine).
    try:
        pend = hub.pending(PLATFORM, limit=2)
        check(f"GET /api/analysis/{PLATFORM}/pending consumes", isinstance(pend, list),
              f"{len(pend)} item(s)")
    except HubError as e:
        check("pending queue", False, str(e))
    try:
        refs = hub.reference_pending(PLATFORM)
        check(f"GET /api/reference/{PLATFORM}/pending consumes", isinstance(refs, list),
              f"{len(refs)} item(s)")
    except HubError as e:
        check("reference queue", False, str(e))

    # §10 lifecycle log + eval round-trip.
    try:
        hub.post_log(AGENT_NAME, "smoke.test", run_id="smoke-run", platform=PLATFORM,
                     content_id=CID, msg="smoke test log")
        check("POST /api/logs", True)
    except HubError as e:
        check("POST /api/logs", False, str(e))
    try:
        hub.post_eval(AGENT_NAME, "blueprint", CID, {"overall": 91.0}, verdict="accept",
                      judge="gemini-2.5-pro", platform=PLATFORM)
        check("POST /api/evals", True)
    except HubError as e:
        check("POST /api/evals", False, str(e))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    print(f"(left synthetic content_id '{CID}' on the hub — no DELETE endpoint / no sibling writes)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
