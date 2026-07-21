#!/usr/bin/env python3
"""tests/smoke_hub.py — live hub round-trip smoke test (AutoSearch/PIPELINE.md §7.5). No
no API key and no live IG session needed — only a running hub at BACKEND_API.

Run:  uv run python -m tests.smoke_hub

Exercises every hub path AutoSearch uses, with an obviously-synthetic handle
(`autosearch_smoke_test`) so it's never mistaken for a real discovery:
  * register the auto-search manifest, then read its config + secrets status,
  * POST a schema-valid candidate and read it back via the pending queue (in_pages=false),
  * approve it -> assert appended_to_pages True + the handle is now a line in pages.txt,
  * approve it AGAIN -> assert appended_to_pages False (idempotent, no duplicate line),
  * reject a second candidate -> assert pages.txt is untouched (no mutation on reject),
  * 404 on an unknown candidate_id; 400 on an invalid status value,
  * confirm `auto-search` appears in GET /api/producers with kind="discovery" + workflow_stages.

NOTE: like AnalysisEngine's smoke_hub.py, the hub has no DELETE for discovery candidates and
AutoSearch must never touch ReelScraper's files directly — the two synthetic candidates this
leaves behind (clearly-fake handles, status now approved/rejected) persist on the hub.
"""
from __future__ import annotations

import os
import sys
import time

from cli import _manifest
from engine import AGENT_NAME, schema
from engine.hub import HubClient, HubError

PLATFORM = "instagram"
SMOKE_USERNAME = f"autosearch_smoke_test_{int(time.time())}"
SMOKE_HANDLE = schema.to_pages_handle(SMOKE_USERNAME)
REJECT_USERNAME = f"autosearch_smoke_reject_{int(time.time())}"
REJECT_HANDLE = schema.to_pages_handle(REJECT_USERNAME)


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

    try:
        hub.register_producer(_manifest())
        check("POST /api/producers/register", True)
    except HubError as e:
        check("POST /api/producers/register", False, str(e))

    try:
        producers = hub.list_producers()
        me = next((p for p in producers if p.get("name") == AGENT_NAME), None)
        check("GET /api/producers includes auto-search", me is not None)
        if me:
            check("  kind == 'discovery'", me.get("kind") == "discovery", str(me.get("kind")))
            check("  workflow_stages present", bool(me.get("workflow_stages")),
                  str(me.get("workflow_stages")))
    except HubError as e:
        check("GET /api/producers", False, str(e))

    try:
        cfg = hub.get_agent_config(AGENT_NAME)
        check("GET /api/config/agent/auto-search", isinstance(cfg, dict), str(cfg)[:80])
    except HubError as e:
        check("GET /api/config/agent/auto-search", False, str(e))

    try:
        secrets = hub.secrets_status(AGENT_NAME)
        gem_s = next((s for s in secrets if s.get("env_var") == "GEMINI_API_KEY"), None)
        check("GET secrets/status returns GEMINI_API_KEY", gem_s is not None, str(secrets))
        check("GEMINI_API_KEY is declared OPTIONAL (discovery runs keyword-only by default)",
              gem_s is not None and gem_s.get("required") is False, str(gem_s))
    except HubError as e:
        check("GET secrets/status", False, str(e))

    # ---- POST a candidate; assert pending + in_pages=false --------------------------
    candidate_id = schema.candidate_id(PLATFORM, SMOKE_HANDLE)
    candidate = {
        "candidate_id": candidate_id, "handle": SMOKE_HANDLE, "source_term": "smoke-test",
        "discovered_via": "synthetic", "followers": 9000, "median_plays": 4000.0,
        "sample_reels": [], "relevance": {"score": 0.65, "reasons": ["smoke test fixture"]},
    }
    errs = schema.validate_candidate(candidate)
    check("fixture candidate is schema-valid", not errs, "; ".join(errs))

    try:
        resp = hub.post_candidate(PLATFORM, candidate)
        check(f"POST /api/discovery/{PLATFORM}", resp.get("status") == "pending", str(resp))
    except HubError as e:
        check(f"POST /api/discovery/{PLATFORM}", False, str(e))

    try:
        pending = hub.pending_candidates(PLATFORM)
        row = next((r for r in pending if r.get("candidate_id") == candidate_id), None)
        check(f"GET /api/discovery/{PLATFORM}/pending includes it", row is not None)
        if row:
            check("  status == 'pending'", row.get("status") == "pending", str(row.get("status")))
            check("  in_pages == False (not yet approved)", row.get("in_pages") is False,
                  str(row.get("in_pages")))
    except HubError as e:
        check("GET pending", False, str(e))

    # ---- approve -> appended_to_pages True + line present ----------------------------
    try:
        r1 = hub.set_candidate_status(PLATFORM, candidate_id, "approved", note="smoke test")
        check("approve -> appended_to_pages True (first time)",
              r1.get("appended_to_pages") is True, str(r1))
    except HubError as e:
        check("approve (first)", False, str(e))

    try:
        pcfg = hub.platform_config(PLATFORM)
        pages = pcfg.get("pages") or []
        check(f"{SMOKE_HANDLE} now a line in pages.txt", SMOKE_HANDLE in pages)
    except HubError as e:
        check("GET pages.txt via /api/config", False, str(e))

    # ---- approve again -> idempotent, appended_to_pages False ------------------------
    try:
        r2 = hub.set_candidate_status(PLATFORM, candidate_id, "approved", note="smoke test again")
        check("second approve -> appended_to_pages False (idempotent, no dupe line)",
              r2.get("appended_to_pages") is False, str(r2))
    except HubError as e:
        check("approve (second, idempotent)", False, str(e))

    # ---- reject a second candidate -> no pages.txt mutation --------------------------
    reject_id = schema.candidate_id(PLATFORM, REJECT_HANDLE)
    reject_candidate = {
        "candidate_id": reject_id, "handle": REJECT_HANDLE, "source_term": "smoke-test",
        "discovered_via": "synthetic", "followers": 500, "median_plays": 10.0,
        "sample_reels": [], "relevance": {"score": 0.05, "reasons": ["smoke test — reject path"]},
    }
    try:
        hub.post_candidate(PLATFORM, reject_candidate)
        r3 = hub.set_candidate_status(PLATFORM, reject_id, "rejected", note="smoke test reject")
        check("reject -> appended_to_pages False", r3.get("appended_to_pages") is False, str(r3))
        pcfg = hub.platform_config(PLATFORM)
        pages = pcfg.get("pages") or []
        check(f"{REJECT_HANDLE} NOT in pages.txt (reject never mutates pages.txt)",
              REJECT_HANDLE not in pages)
    except HubError as e:
        check("reject path", False, str(e))

    # ---- 404 / 400 edge cases ---------------------------------------------------------
    try:
        hub.set_candidate_status(PLATFORM, "cand_doesnotexist99", "approved")
        check("unknown candidate_id -> 404", False, "did not raise")
    except HubError as e:
        check("unknown candidate_id -> 404", "404" in str(e), str(e))

    try:
        hub.set_candidate_status(PLATFORM, candidate_id, "not-a-real-status")
        check("invalid status -> 400", False, "did not raise")
    except HubError as e:
        check("invalid status -> 400", "400" in str(e), str(e))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    print(f"(left synthetic candidates on the hub: {SMOKE_HANDLE} (approved), "
          f"{REJECT_HANDLE} (rejected) — obviously-synthetic handles, no DELETE endpoint, "
          f"and AutoSearch must never write into ReelScraper's files directly)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
