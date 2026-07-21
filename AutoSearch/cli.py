#!/usr/bin/env python3
"""cli.py — AutoSearch entry point (AutoSearch/PIPELINE.md, the discovery agent).

Commands:
  uv run cli.py run <platform>        manual/exhaustive discovery pass (bypasses the weekly
                                       plan; still respects caps/pacing/breaker/kill-switch)
  uv run cli.py beat <platform>       one bounded, idempotent heartbeat tick (§2b) — mostly
                                       no-ops; the hub's opt-in scheduler calls this
  uv run cli.py synthetic <platform>  fabricate N candidates (no network, no LLM) and
                                       drive the full event + POST path, for verification
  uv run cli.py smoke                 guest bootstrap (assert no sessionid) + one
                                       web_profile_info hydration of a known public handle
  uv run cli.py status                hub health + secret status + guest-only banner + config

Prime directive (§0): read work and write results ONLY through the hub API (`BACKEND_API`,
default http://127.0.0.1:8787). Never import ReelScraper or any sibling's code. Never write
into another project's directory — the hub appends approved handles to pages.txt, never us.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from engine import AGENT_NAME
from engine import ig
from engine import plan as planlib
from engine import score as scorelib
from engine import search as searchlib
from engine import schema as schemalib
from engine.circuit import CircuitBreaker, CircuitTripped
from engine.hub import HubClient, HubError
from engine.limits import BREAKER_COOLDOWN_SECONDS
from engine.logsetup import setup_logging

log = logging.getLogger("as.cli")

ROOT = Path(__file__).resolve().parent

GUEST_ONLY_BANNER = (
    "No burner session supplied — running GUEST-ONLY. Login-gated surfaces (topsearch, "
    "discover/chaining) are SKIPPED. Discovery will be shallower."
)

WORKFLOW_STAGES = ["Queued", "Searching", "Scoring", "Proposed", "Approved", "Rejected"]

# The tunable knobs (JSON Schema of defaults) — registered as the agent's config_schema so
# the Dashboard renders a schema-driven form; overridden at run start by
# GET /api/config/agent/auto-search (AutoSearch/PIPELINE.md §6, verbatim).
CONFIG_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "weekly_search_budget": {"type": "integer", "default": 120, "minimum": 1,
            "description": "Work units (search terms+expands) per 7-day window"},
        "active_days_per_week": {"type": "integer", "default": 5, "minimum": 1, "maximum": 7,
            "description": "Active days/week; the rest are randomized rest days"},
        "active_hours": {"type": "array", "default": [9, 23],
            "description": "[startHour,endHour] local window beats may act in"},
        "heartbeat_minutes": {"type": "integer", "default": 20, "minimum": 1,
            "description": "Scheduler tick cadence"},
        "beat_action_probability": {"type": "number", "default": 0.35, "minimum": 0, "maximum": 1,
            "description": "Chance an in-window beat does work"},
        "beat_max_units": {"type": "integer", "default": 2, "minimum": 1,
            "description": "Max work units per beat"},
        "daily_search_cap": {"type": "integer", "default": 300, "minimum": 1,
            "description": "Hard ceiling: IG requests/day"},
        "per_term_limit": {"type": "integer", "default": 5, "minimum": 1,
            "description": "Candidates hydrated+scored per term"},
        "min_followers": {"type": "integer", "default": 2000, "minimum": 0},
        "min_median_plays": {"type": "integer", "default": 3000, "minimum": 0},
        "relevance_threshold": {"type": "number", "default": 0.6, "minimum": 0, "maximum": 1},
        "pacing_seconds": {"type": "number", "default": 6.0, "minimum": 0,
            "description": "Min gap between paced actions (floors in §1 win)"},
        "guest_only": {"type": "boolean", "default": True,
            "description": "true = never use the burner; guest surfaces only"},
        "discovery_enabled": {"type": "boolean", "default": False,
            "description": "Kill-switch. false = agent + hub scheduler idle"},
        "term_expansion_enabled": {"type": "boolean", "default": False,
            "description": "Spend Gemini credits to widen seed keywords into more search "
                           "terms. false (default) = keyword search only, zero API cost. "
                           "Needs GEMINI_API_KEY when enabled."},
        "model": {"type": "string", "default": "gemini-2.5-flash",
            "description": "Gemini model for term expansion (only used when "
                           "term_expansion_enabled is true)"},
    },
}
DEFAULTS = {k: v["default"] for k, v in CONFIG_SCHEMA["properties"].items()}

SYNTHETIC_PREFIX = "autosearch_synthetic_"


# ---- .env loader (no dependency, matches AnalysisEngine's convention) -----------------
def _load_dotenv() -> None:
    envfile = ROOT / ".env"
    if not envfile.exists():
        return
    for line in envfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:  # real env wins over .env
            os.environ[k] = v


def _gemini_present() -> bool:
    """Same key, same names, same order as AnalysisEngine and SimilarContent.

    Discovery deliberately does NOT need this. It gates one optional call (term expansion);
    without it — and by default even with it — AutoSearch searches the seed keywords
    verbatim. See _expand_terms.
    """
    from engine.gemini import resolve_api_key
    return bool(resolve_api_key())


def _ig_present() -> bool:
    return bool(os.environ.get("IG_SESSIONID")) or (ROOT / "session.txt").exists()


def _manifest() -> dict:
    return {
        "name": AGENT_NAME, "kind": "discovery",
        "consumes": ["config", "corpus", "insights"],
        "produces": "creator_candidates", "human_gate": True, "needs_reference": False,
        "output_status": "pending",
        "workflow_stages": WORKFLOW_STAGES,
        "config_schema": CONFIG_SCHEMA,
        "secrets": [
            # required=False, and that is the honest value: every code path here degrades
            # to keyword-only discovery without a key. Declaring it required made the
            # Dashboard demand a paid key for a stage the hub marks unconditionally ready.
            {"name": "gemini_api_key", "env_var": "GEMINI_API_KEY", "required": False,
             "present": _gemini_present()},
            {"name": "ig_sessionid", "env_var": "IG_SESSIONID", "required": False,
             "present": _ig_present()},
        ],
    }


def _refuse_foreign_hub(hub, base: str) -> None:
    """Stop if BACKEND_API is aimed at a DIFFERENT checkout's hub.

    Running two niches side by side means two clones, each with its own hub on its own
    port. The only thing joining this agent to one of them is BACKEND_API — and a .env
    copied between clones, or a stale `export BACKEND_API=` in the shell, points it at the
    other one. Every call then succeeds: work is read from that niche's corpus and written
    to that niche's studio, under this agent's name, with nothing to show anything went
    wrong. Refusing costs one request and is the only place this is detectable.

    Silent when the hub cannot say (too old to serve /api/hub, or unreachable) — a missing
    answer is not a mismatch.
    """
    other = hub.foreign_checkout()
    if not other:
        return
    print(
        f"\nERROR: {base} is a different checkout's hub.\n"
        f"  it serves:   {other}\n"
        f"  this agent:  {Path(__file__).resolve().parent}\n\n"
        "Using it would write this niche's work into that one's corpus.\n"
        "Point BACKEND_API at this checkout's hub (./init writes it into .env),\n"
        "or start this checkout's own:  cd ../ReelScraper && uv run cli.py start\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


class Bootstrap:
    """Shared startup: verify hub, self-register, fetch config."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.base = os.environ.get("BACKEND_API", "http://127.0.0.1:8787")
        self.hub = HubClient(self.base)
        self.cfg = dict(DEFAULTS)

    def start(self, require_hub: bool = True) -> "Bootstrap":
        if not self.hub.health_ok():
            log.error("hub is not reachable", extra={"backend": self.base})
            if require_hub:
                print(f"\nERROR: the hub at {self.base} is not reachable.\n"
                      "Start it in ReelScraper:  uv run cli.py start\n", file=sys.stderr)
                raise SystemExit(2)
            return self
        _refuse_foreign_hub(self.hub, self.base)
        try:
            self.hub.register_producer(_manifest())
        except HubError as e:
            log.warning("producer registration failed (continuing)", extra={"err": str(e)})
        try:
            stored = self.hub.get_agent_config(AGENT_NAME)
            merged = dict(DEFAULTS)
            merged.update(stored.get("defaults") or {})
            merged.update(stored.get("config") or {})
            self.cfg = merged
        except HubError as e:
            log.warning("config fetch failed; using defaults", extra={"err": str(e)})
        log.info("bootstrap ready", extra={"backend": self.base, "gemini_key": _gemini_present()})
        return self


# ---- shared helpers ---------------------------------------------------------------------
def _safe(fn, default=None):
    try:
        return fn()
    except HubError as e:
        log.debug("hub read failed (non-fatal)", extra={"err": str(e)})
        return default


def _load_niche(hub: HubClient, platform: str) -> tuple[str, list[str]]:
    cfg = _safe(lambda: hub.platform_config(platform), {}) or {}
    pcfg = cfg.get("config") or {}
    niche = pcfg.get("niche") or platform
    keywords = ((pcfg.get("discovery") or {}).get("keywords")) or []
    return niche, keywords


def _prior_trending_insight(hub: HubClient) -> str | None:
    insights = _safe(hub.list_insights, []) or []
    hits = [i for i in insights if "trending-terms" in (i.get("tags") or [])]
    return hits[-1]["text"] if hits else None


def _kill_switch_ok(hub: HubClient, run_id: str, platform: str, cfg: dict) -> bool:
    """§1.6: fail-closed on any ambiguity — checked at run start (and the caller re-checks
    between surfaces via the same `cfg` snapshot; a long-running beat is short by design)."""
    enabled = bool(cfg.get("discovery_enabled", False))
    if not enabled:
        log.info("kill-switch: discovery_enabled is false — stopping cleanly", extra={"platform": platform})
        hub.post_log(AGENT_NAME, "run.skip", run_id=run_id, platform=platform,
                     msg="discovery_enabled is false (kill-switch)")
    return enabled


def _print_safety_banner(burner_present: bool) -> None:
    if burner_present:
        print("\nBURNER mode: a session was supplied. CAN get the burner rate-limited/blocked/"
              "PERMANENTLY BANNED — expected and acceptable ONLY because it is disposable. "
              "NEVER a personal/valued account.\n")
    else:
        print(f"\n{GUEST_ONLY_BANNER}\n")


def _seed_terms(niche: str, seed_keywords: list[str]) -> list[str]:
    """The default, and the floor every failure path returns to: the operator's own keywords,
    deduped and capped. No network, no key, no credits."""
    return list(dict.fromkeys(seed_keywords))[:20] or [niche]


def _expand_terms(cfg: dict, niche: str, seed_keywords: list[str], factors, trending) -> list[str]:
    """Widen the seed keywords with Gemini — OFF unless the operator asks for it.

    Two independent conditions, both required, in this order:

      1. `term_expansion_enabled` (config, default False). This is the spend switch. It is
         checked FIRST and on its own, so an operator who has GEMINI_API_KEY exported for
         AnalysisEngine — which is the normal state of this repo — never quietly starts
         paying for discovery too. Opting in is a deliberate act on the agent's desk.
      2. A key actually resolving. Enabled-but-keyless is a misconfiguration worth saying out
         loud rather than silently behaving like the default.

    Every failure below falls back to the seeds rather than aborting the run: discovery with
    narrower terms is useful, discovery that dies because a model returned bad JSON is not.
    """
    if not cfg.get("term_expansion_enabled", False):
        log.info("term expansion off (default) — searching seed keywords verbatim, no API cost")
        return _seed_terms(niche, seed_keywords)
    if not _gemini_present():
        log.warning("term_expansion_enabled is true but no Gemini key resolved "
                    "(GEMINI_API_KEY / GEMINI_KEY / GOOGLE_API_KEY) — using seed keywords")
        return _seed_terms(niche, seed_keywords)
    try:
        from engine import gemini as geminilib
        from engine import memory as memorylib

        client = geminilib.GeminiClient(model=cfg.get("model", geminilib.DEFAULT_MODEL),
                                        system_prefix=memorylib.compose_system_prompt("instagram"))
        doc = client.expand_terms(niche, seed_keywords, factors=factors, trending_insight=trending)
        errs = schemalib.validate_keyword_expansion(doc)
        if errs:
            log.warning("term expansion failed schema validation; using seeds", extra={"errors": errs})
            return _seed_terms(niche, seed_keywords)
        terms = list(dict.fromkeys((doc.get("keywords") or []) + (doc.get("hashtags") or [])))
        log.info("term expansion ok", extra={"seeds": len(seed_keywords), "expanded": len(terms)})
        return terms or _seed_terms(niche, seed_keywords)
    except CircuitTripped:
        raise
    except Exception as e:
        log.warning("term expansion call failed; using seeds", extra={"err": str(e)})
        return _seed_terms(niche, seed_keywords)


def _make_on_candidate(hub: HubClient, run_id: str, platform: str, posted_box: list):
    def on_candidate(cand: dict, record: dict) -> None:
        cid = schemalib.candidate_id(platform, cand["handle"])
        cand["candidate_id"] = cid
        errs = schemalib.validate_candidate(cand)
        if errs:
            log.error("candidate failed its own schema — dropping", extra={"errors": errs})
            return
        hub.post_log(AGENT_NAME, "item.start", run_id=run_id, platform=platform, content_id=cid,
                     msg=f"candidate @{record.get('username')}", data={"stage": "Queued"})
        hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                     data={"stage": "Searching"})
        hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                     data={"stage": "Scoring"})
        try:
            hub.post_candidate(platform, cand)
            posted_box[0] += 1
            hub.post_log(AGENT_NAME, "item.done", run_id=run_id, platform=platform, content_id=cid,
                         msg="proposed", data={"stage": "Proposed", "score": cand["relevance"]["score"]})
        except HubError as e:
            hub.post_log(AGENT_NAME, "item.error", level="error", run_id=run_id, platform=platform,
                         content_id=cid, msg=str(e), data={"stage": "Failed"})
    return on_candidate


# ---- commands -----------------------------------------------------------------------
def cmd_run(args) -> int:
    platform = args.platform
    run_id = setup_logging("run", platform)
    boot = Bootstrap(run_id).start()
    hub, cfg = boot.hub, boot.cfg

    if not _kill_switch_ok(hub, run_id, platform, cfg):
        print(f"discovery_enabled is false — nothing to do for {platform}. "
              "Enable it in the Dashboard/config to run discovery.")
        return 0

    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=platform, msg=f"run {platform}")

    niche, seed_keywords = _load_niche(hub, platform)
    factors = _safe(lambda: hub.factors(platform))
    trending = _prior_trending_insight(hub)
    terms = _expand_terms(cfg, niche, seed_keywords, factors, trending)

    guest = ig.GuestSession()
    try:
        guest.bootstrap()
    except Exception as e:
        log.error("guest bootstrap failed", extra={"err": str(e)})
    burner = None if cfg.get("guest_only", True) else ig.load_burner_session()
    _print_safety_banner(bool(burner))

    breaker = CircuitBreaker(max_strikes=3, pace_seconds=float(cfg.get("pacing_seconds", 6.0)))
    budget = searchlib.Budget(cfg)
    posted_box = [0]
    on_candidate = _make_on_candidate(hub, run_id, platform, posted_box)

    try:
        searchlib.discover_via_terms(terms, cfg, platform, guest, burner, breaker, budget,
                                     on_candidate=on_candidate)
    except CircuitTripped as e:
        log.critical("circuit breaker tripped — stopping cleanly", extra={"err": str(e)})
        hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform, msg=str(e))
    except ig.RateLimited as e:
        log.critical("rate limited — stopping cleanly", extra={"err": str(e)})
        hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform, msg=str(e))

    posted = posted_box[0]
    if posted:
        try:
            hub.post_insight(
                f"AutoSearch proposed {posted} {platform} creator candidate(s) from terms: "
                f"{', '.join(terms[:8])}.", platform="shared", kind="finding",
                tags=["trending-terms", "auto-search", platform],
            )
        except HubError:
            pass

    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform, msg=f"proposed {posted}",
                 data={"proposed": posted})
    print(f"\nDone: {posted} candidate(s) proposed for {platform}.\n")
    return 0


def cmd_beat(args) -> int:
    platform = args.platform
    run_id = setup_logging("beat", platform)
    boot = Bootstrap(run_id).start()
    hub, cfg = boot.hub, boot.cfg

    if not _kill_switch_ok(hub, run_id, platform, cfg):
        print(f"beat.skip reason=disabled platform={platform}")
        return 0

    plan = planlib.load_or_generate_plan(cfg)
    should_act, reason = planlib.gate_beat(cfg, plan)
    if not should_act:
        log.info("beat.skip", extra={"reason": reason, "platform": platform})
        print(f"beat.skip reason={reason} platform={platform}")
        return 0

    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=platform,
                 msg=f"beat {platform}", data={"mode": "beat"})

    niche, seed_keywords = _load_niche(hub, platform)
    beat_max_units = int(cfg.get("beat_max_units", 2))
    terms = (seed_keywords or [niche])[:beat_max_units]  # cheap: never an LLM call on a beat

    guest = ig.GuestSession()
    try:
        guest.bootstrap()
    except Exception as e:
        log.error("guest bootstrap failed", extra={"err": str(e)})
    burner = None if cfg.get("guest_only", True) else ig.load_burner_session()

    breaker = CircuitBreaker(max_strikes=3, pace_seconds=float(cfg.get("pacing_seconds", 6.0)))
    budget = searchlib.Budget(cfg)
    posted_box = [0]
    on_candidate = _make_on_candidate(hub, run_id, platform, posted_box)

    date_str = datetime.now().date().isoformat()
    tripped = False
    try:
        searchlib.discover_via_terms(terms, cfg, platform, guest, burner, breaker, budget,
                                     max_units=beat_max_units, on_candidate=on_candidate)
    except CircuitTripped as e:
        tripped = True
        log.critical("circuit breaker tripped during beat", extra={"err": str(e)})
        planlib.set_breaker_cooldown(date_str, time.time() + BREAKER_COOLDOWN_SECONDS)
        hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform, msg=str(e))
    except ig.RateLimited as e:
        tripped = True
        log.critical("rate limited during beat", extra={"err": str(e)})
        planlib.set_breaker_cooldown(date_str, time.time() + 30 * 60)
        hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform, msg=str(e))

    planlib.increment_ledger(date_str, n=len(terms))
    posted = posted_box[0]
    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform, msg=f"beat proposed {posted}",
                 data={"proposed": posted, "tripped": tripped})
    print(f"beat.done proposed={posted} platform={platform}")
    return 0


def cmd_synthetic(args) -> int:
    platform = args.platform
    run_id = setup_logging("synthetic", platform)
    boot = Bootstrap(run_id).start()
    hub = boot.hub

    n = args.count
    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=platform,
                 msg=f"synthetic {platform}", data={"mode": "synthetic", "count": n})

    posted = 0
    for i in range(n):
        username = f"{SYNTHETIC_PREFIX}{i:03d}"
        handle = schemalib.to_pages_handle(username)
        cid = schemalib.candidate_id(platform, handle)
        cand = {
            "candidate_id": cid, "handle": handle, "source_term": "synthetic-verification",
            "discovered_via": "synthetic", "followers": 12000 + i * 500,
            "median_plays": 4500.0 + i * 100,
            "sample_reels": [f"https://www.instagram.com/reel/synthetic{i:03d}/"],
            "relevance": {"score": 0.72, "reasons": ["synthetic fixture — no network, no LLM"]},
        }
        errs = schemalib.validate_candidate(cand)
        if errs:
            print("FAIL: synthetic candidate failed its own schema:", errs)
            return 1

        hub.post_log(AGENT_NAME, "item.start", run_id=run_id, platform=platform, content_id=cid,
                     msg=f"synthetic @{username}", data={"stage": "Queued"})
        hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                     data={"stage": "Searching"})
        hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                     data={"stage": "Scoring"})
        try:
            resp = hub.post_candidate(platform, cand)
            posted += 1
            hub.post_log(AGENT_NAME, "item.done", run_id=run_id, platform=platform, content_id=cid,
                         msg=f"proposed {username}",
                         data={"stage": "Proposed", "score": cand["relevance"]["score"]})
            print(f"  [{i + 1}/{n}] {handle} -> {resp}")
        except HubError as e:
            hub.post_log(AGENT_NAME, "item.error", level="error", run_id=run_id, platform=platform,
                         content_id=cid, msg=str(e), data={"stage": "Failed"})
            print(f"  ! {handle} failed: {e}")

    if posted:
        try:
            hub.post_insight(
                f"AutoSearch synthetic verification posted {posted} fixture candidate(s) for "
                f"{platform} (discovered_via=synthetic; not real IG data).",
                platform="shared", kind="method", tags=["auto-search", "synthetic", platform],
            )
        except HubError:
            pass

    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform, msg=f"synthetic posted {posted}",
                 data={"posted": posted})
    print(f"\nDone: {posted}/{n} synthetic candidates posted.")
    print(f"NOTE: these are left on the hub with obviously-synthetic handles ({SYNTHETIC_PREFIX}*) — "
          "the hub has no DELETE for discovery candidates, and AutoSearch must never write into "
          "ReelScraper's files directly (prime directive, §0). Reject them from the Dashboard's "
          "Discover view (or POST /api/discovery/{p}/{id}/status {\"status\":\"rejected\"}) to purge "
          "their scraped metadata (§1.7) if you want them out of the pending queue.")
    return 0


def _canned_ig_transport(req, timeout=30):
    """CI-safe fake transport for `smoke` — no network. Installed only when
    AUTOSEARCH_FAKE_IG is set (see engine.ig.install_fake_transport)."""
    import json as _json

    class _FakeResp:
        def __init__(self, body: bytes, headers: dict | None = None):
            self._body = body
            self.status = 200
            self.headers = headers or {}

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    url = req.full_url
    if url == "https://www.instagram.com/":
        return _FakeResp(b"<html></html>", headers={"Set-Cookie": "csrftoken=fakecsrftoken123; Path=/"})
    if "web_profile_info" in url:
        payload = _json.dumps({"data": {"user": {
            "username": "nasa", "id": "999999", "full_name": "NASA",
            "biography": "Explore the universe and discover our home planet.",
            "category_name": "Government Organization",
            "edge_followed_by": {"count": 12345678}, "edge_follow": {"count": 42},
            "edge_owner_to_timeline_media": {"count": 4000},
            "is_verified": True, "is_private": False, "is_business_account": True,
            "external_url": "",
        }}}).encode("utf-8")
        return _FakeResp(payload)
    return _FakeResp(b"{}")


def cmd_smoke(args) -> int:
    setup_logging("smoke")
    fake = bool(os.environ.get("AUTOSEARCH_FAKE_IG"))
    if fake:
        ig.install_fake_transport(_canned_ig_transport)
        print("(AUTOSEARCH_FAKE_IG=1 — using a canned fake transport; no network touched)")

    session = ig.GuestSession()
    try:
        session.bootstrap()
        assert "sessionid" not in session.cookie, "guest session unexpectedly carries a sessionid!"
        print(f"PASS: guest bootstrap OK (no sessionid attached). "
              f"csrf={'present' if session.csrf else 'absent'}")

        handle = args.handle or "nasa"
        profile = ig.web_profile_info(handle, session=session)
        if profile:
            print(f"PASS: hydrated @{handle}: followers={profile.get('followers')} "
                  f"category={profile.get('category')!r} private={profile.get('is_private')}")
        else:
            print(f"(could not hydrate @{handle} — network/rate-limit; the guest-only safety "
                  f"assertion above already passed, which is what `smoke` verifies)")
        return 0
    finally:
        if fake:
            ig.reset_transport()


def cmd_status(args) -> int:
    run_id = setup_logging("status")
    boot = Bootstrap(run_id).start(require_hub=False)
    hub = boot.hub

    print(f"\nAutoSearch status  (hub: {boot.base})")
    print("=" * 60)
    if not hub.health_ok():
        print(f"HUB UNREACHABLE at {boot.base} — start it in ReelScraper: uv run cli.py start\n")
        return 2
    print("Hub: reachable")
    for p in hub.platforms():
        print(f"  {p.get('platform', '?'):<10} items={p.get('items', 0)}")

    print("\nSecret status (env-var NAME only, never values):")
    try:
        for s in hub.secrets_status(AGENT_NAME):
            mark = "present" if s.get("present") else "ABSENT"
            req = "required" if s.get("required") else "optional"
            print(f"  {s.get('name')} (${s.get('env_var')}): {mark}  [{req}]")
    except HubError as e:
        print(f"  (secret status unavailable: {e})")
    print(f"  local resolve: GEMINI_API_KEY {'present' if _gemini_present() else 'ABSENT'} "
          f"(optional — only used when term_expansion_enabled)")
    print(f"  local resolve: IG_SESSIONID/session.txt {'present' if _ig_present() else 'ABSENT'} (burner, optional)")

    _print_safety_banner(_ig_present() and not boot.cfg.get("guest_only", True))

    print("Effective config (defaults + hub GET /api/config/agent/auto-search):")
    for k, v in boot.cfg.items():
        print(f"  {k} = {v}")

    print(f"\ndiscovery_enabled = {boot.cfg.get('discovery_enabled', False)} "
          f"({'discovery + hub scheduler ACTIVE' if boot.cfg.get('discovery_enabled', False) else 'kill-switch OFF — idle by default'})")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="auto-search", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="manual/exhaustive discovery pass for a platform")
    pr.add_argument("platform")
    pr.set_defaults(func=cmd_run)

    pb = sub.add_parser("beat", help="one bounded, idempotent heartbeat tick")
    pb.add_argument("platform")
    pb.set_defaults(func=cmd_beat)

    ps = sub.add_parser("synthetic", help="fabricate N candidates (no network, no LLM)")
    ps.add_argument("platform")
    ps.add_argument("--count", type=int, default=3)
    ps.set_defaults(func=cmd_synthetic)

    psm = sub.add_parser("smoke", help="guest bootstrap + one hydration (no burner needed)")
    psm.add_argument("--handle", default=None, help="public IG handle to hydrate (default: nasa)")
    psm.set_defaults(func=cmd_smoke)

    pst = sub.add_parser("status", help="hub health + secret status + guest-only banner + config")
    pst.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
