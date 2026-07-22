#!/usr/bin/env python3
"""cli.py — AnalysisEngine entry point.

Commands:
  uv run cli.py run <platform> [filters...]   analyze the pending (+ reference) queue for a platform
  uv run cli.py once <content_id> [--platform] analyze / re-analyze a single clip by content_id
  uv run cli.py status                          hub health + analyzed counts + secret status + config

Prime directive (companion D1): read work and write results ONLY through the hub API
(`BACKEND_API`, default http://127.0.0.1:8787). Never scrape. Never open another project's
files. On startup verify the hub is up; if it is down, stop and tell the operator to run
`uv run cli.py start` in ReelScraper.

Runtime honesty: a live analysis needs GEMINI_API_KEY set AND local media downloaded
(`uv run download_media.py <platform>` in ReelScraper). `status` works without either and
reports exactly what is missing.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from engine import AGENT_NAME
from engine.analyze import Analyzer
from engine.circuit import CircuitBreaker, CircuitTripped
from engine.evaluate import Judge
from engine.gemini import GeminiClient, GeminiError
from engine.hub import HubClient, HubError
from engine.logsetup import setup_logging
from engine import memory

log = logging.getLogger("ae.cli")

ROOT = Path(__file__).resolve().parent
WORK_DIR = ROOT / "work"

GEMINI_ENV_VARS = ["GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY"]

# The tunable knobs (JSON Schema of defaults). Registered as the agent's config_schema so the
# Dashboard can render a form; overridden at run start by GET /api/config/agent/analysis-engine.
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "default": "gemini-2.5-pro"},
        "judge_model": {"type": "string", "default": "gemini-2.5-pro"},
        "score_threshold": {"type": "integer", "default": 85},
        "max_refine_passes": {"type": "integer", "default": 3},
        "max_repair_passes": {"type": "integer", "default": 2},
        "temperature": {"type": "number", "default": 0.4},
        "max_output_tokens": {"type": "integer", "default": 64000},
        "default_limit": {"type": "integer", "default": 10},
        # The duration veto, and the closest thing to an "easy to remake" signal that exists
        # BEFORE a blueprint does. Ease is scored downstream in SimilarContent, but 65 of its
        # 100 points come from shot count and static-camera fraction — both read out of the
        # schema-2 blueprint, i.e. out of the very thing this stage is deciding whether to pay
        # for. Duration is the one ease input available beforehand, and the ease heuristic
        # already treats it as a veto rather than a term: at or over EASE_LONG_S (30s) a clip
        # "is never 'easy' at any setting". So refusing to blueprint those costs nothing that
        # could ever have cleared the gate. 0 disables the veto.
        "max_duration_s": {"type": "number", "default": 30.0,
                           "description": "Skip clips longer than this many seconds (0 = no "
                                          "limit). Mirrors SimilarContent's EASE_LONG_S: a "
                                          "clip at or over it can never score as easy to "
                                          "remake, so a blueprint for one is spend that the "
                                          "ease gate will never use."},
    },
}
DEFAULTS = {k: v["default"] for k, v in CONFIG_SCHEMA["properties"].items()}


# ---- .env loader (no dependency) ----------------------------------------------------
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


def _gemini_key() -> str | None:
    for name in GEMINI_ENV_VARS:
        val = os.environ.get(name)
        if val:
            return val
    return None


def _secret_present() -> bool:
    return _gemini_key() is not None


def _manifest() -> dict:
    return {
        "name": AGENT_NAME,
        "kind": "analyzer",
        "consumes": ["analysis_queue", "reference_queue", "media", "corpus", "insights"],
        "human_gate": False,
        "needs_reference": False,
        "produces": "analysis_blueprint",
        "output_status": None,
        "workflow_stages": ["Queued", "Analyzing", "Self-eval", "Done"],
        "config_schema": CONFIG_SCHEMA,
        "secrets": [
            {"name": "gemini_api_key", "env_var": "GEMINI_API_KEY",
             "required": True, "present": _secret_present()},
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
    """Shared startup: verify hub, self-register, fetch config, resolve secrets."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.base = os.environ.get("BACKEND_API", "http://127.0.0.1:8787")
        self.hub = HubClient(self.base)
        self.cfg = dict(DEFAULTS)

    def start(self) -> "Bootstrap":
        if not self.hub.health_ok():
            log.error("hub is not reachable", extra={"backend": self.base})
            print(
                f"\nERROR: the hub at {self.base} is not reachable.\n"
                "Start it in ReelScraper:  uv run cli.py start\n",
                file=sys.stderr,
            )
            raise SystemExit(2)
        _refuse_foreign_hub(self.hub, self.base)
        # Register (idempotent) so config + secret-status endpoints resolve for this agent.
        try:
            self.hub.register_producer(_manifest())
        except HubError as e:
            log.warning("producer registration failed (continuing)", extra={"err": str(e)})
        # Layer hub-stored config over defaults.
        try:
            stored = self.hub.get_agent_config(AGENT_NAME)
            merged = dict(DEFAULTS)
            merged.update(stored.get("defaults") or {})
            merged.update(stored.get("config") or {})
            self.cfg = merged
        except HubError as e:
            log.warning("config fetch failed; using defaults", extra={"err": str(e)})
        log.info("bootstrap ready", extra={"backend": self.base, "gemini_key": _secret_present()})
        return self


def _ytdlp_fallback(url: str | None, dest: Path) -> None:
    """Fallback ONLY: re-download a clip with yt-dlp when the hub has no local media. The
    primary, preferred path is always the hub's local media (companion D1 step 3)."""
    if not url:
        raise GeminiError("no url available for yt-dlp fallback download")
    try:
        import yt_dlp  # imported lazily — fallback path only
    except ImportError as e:  # pragma: no cover
        raise GeminiError(f"yt-dlp not available for fallback: {e}") from e
    opts = {"outtmpl": str(dest), "format": "mp4/best", "quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


# ---- per-clip analysis --------------------------------------------------------------
def _make_file_getter(gemini: GeminiClient, hub: HubClient, item: dict):
    """Return a callable get_file_uri(force_refresh) that lazily downloads the hub media and
    uploads it FRESH to the Gemini File API (re-uploading on demand for expiry/resume). Tracks
    the uploaded file + temp path so the caller can clean up. Never hardcodes/caches a URI."""
    state = {"uri": None, "file_name": None, "temp": None}

    def get_file_uri(force_refresh: bool = False) -> str:
        if state["uri"] and not force_refresh:
            return state["uri"]
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        if not state["temp"]:
            cid = item.get("content_id", "clip")
            temp = WORK_DIR / f"{cid}.mp4"
            video_url = item.get("video_url") or item.get("media_url")
            if video_url:
                try:
                    hub.download_media(video_url, str(temp))
                except HubError as e:
                    log.warning("hub media download failed; trying yt-dlp fallback",
                                extra={"content_id": cid, "err": str(e)})
                    _ytdlp_fallback(item.get("url"), temp)
            elif item.get("url"):
                # No local media on the hub — fallback re-download (yt-dlp), fallback path only.
                _ytdlp_fallback(item.get("url"), temp)
            else:
                raise GeminiError(f"queue item {cid} has no video_url — run the media stage first")
            state["temp"] = temp
        info = gemini.upload_file(str(state["temp"]))
        info = gemini.wait_active(info["name"])
        state["uri"], state["file_name"] = info["uri"], info["name"]
        return state["uri"]

    return get_file_uri, state


def analyze_one(item: dict, platform: str, cfg: dict, gemini: GeminiClient, judge: Judge,
                hub: HubClient, run_id: str, is_reference: bool) -> dict:
    cid = item["content_id"]
    hub.post_log(AGENT_NAME, "item.start", run_id=run_id, platform=platform, content_id=cid,
                 msg=f"analyzing {cid}", data={"stage": "Analyzing", "is_reference": is_reference})
    system_prompt = memory.compose_system_prompt(platform)
    analyzer = Analyzer(
        gemini,
        temperature=float(cfg["temperature"]),
        max_output_tokens=int(cfg["max_output_tokens"]),
        max_repairs=int(cfg["max_repair_passes"]),
    )
    get_file_uri, fstate = _make_file_getter(gemini, hub, item)

    try:
        blueprint, errors = analyzer.analyze(item, platform, system_prompt, get_file_uri,
                                             is_reference=is_reference)
        # Refine loop against the judge (cap max_refine_passes).
        max_passes = int(cfg["max_refine_passes"])
        hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                     msg="self-eval", data={"stage": "Self-eval"})
        evaluation = judge.evaluate(blueprint, passes=1)
        blueprint["evaluation"] = evaluation
        p = 1
        while not evaluation["accepted"] and p < max_passes:
            p += 1
            hub.post_log(AGENT_NAME, "item.stage", run_id=run_id, platform=platform, content_id=cid,
                         msg=f"refine pass {p}", data={"stage": "Analyzing"})
            log.info("refine pass", extra={"content_id": cid, "pass": p,
                                           "score": evaluation["score_0_100"]})
            blueprint = analyzer.refine_with_gaps(
                item, platform, system_prompt, get_file_uri,
                evaluation["gaps_remaining"], is_reference=is_reference)
            evaluation = judge.evaluate(blueprint, passes=p)
            blueprint["evaluation"] = evaluation

        hub.post_analysis(platform, blueprint)

        target_type = "reference_blueprint" if is_reference else "blueprint"
        scores = dict(evaluation.get("per_criterion") or {})
        scores["overall"] = evaluation["score_0_100"]
        hub.post_eval(AGENT_NAME, target_type, cid, scores,
                      verdict=evaluation["verdict"], judge=evaluation["judge_model"],
                      platform=platform)
        hub.post_log(AGENT_NAME, "item.done", run_id=run_id, platform=platform, content_id=cid,
                     msg=f"analyzed {cid} score={evaluation['score_0_100']}",
                     data={"stage": "Done", "score": evaluation["score_0_100"],
                           "accepted": evaluation["accepted"], "passes": evaluation["passes"],
                           "is_reference": is_reference})

        # Distill: turn a remaining gap into a preventive lesson for future runs.
        for gap in evaluation.get("gaps_remaining", [])[:1]:
            memory.append_pattern(f"Avoid: {gap}")

        log.info("clip done", extra={"content_id": cid, "score": evaluation["score_0_100"],
                                     "accepted": evaluation["accepted"]})
        return evaluation
    finally:
        # cleanup: delete the File API upload + temp media (companion D1 step 3).
        if fstate.get("file_name"):
            gemini.delete_file(fstate["file_name"])
        if fstate.get("temp") and Path(fstate["temp"]).exists():
            try:
                Path(fstate["temp"]).unlink()
            except OSError:
                pass


# ---- commands -----------------------------------------------------------------------
def cmd_run(args) -> int:
    platform = args.platform
    run_id = setup_logging("run", platform)
    boot = Bootstrap(run_id).start()
    hub = boot.hub
    cfg = boot.cfg

    filters = {
        "min_score": args.min_score, "tier": args.tier,
        "min_duration": args.min_duration,
        # --max-duration wins; otherwise the configured veto, and 0/None means no veto at
        # all. Applied here rather than only in the cascade's argv so it holds for a manual
        # Run from the Board too — a veto that only bites unattended is not a veto.
        "max_duration": (args.max_duration if args.max_duration is not None
                         else (cfg.get("max_duration_s", DEFAULTS["max_duration_s"]) or None)),
        "content_type": args.content_type,
        "limit": args.limit if args.limit is not None else cfg["default_limit"],
        "stale": args.stale or None, "reanalyze": args.reanalyze,
    }
    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=platform,
                 msg=f"run {platform}",
                 data={k: v for k, v in filters.items() if v is not None})

    pending = hub.pending(platform, **filters)
    refs = [] if args.no_references else hub.reference_pending(platform)
    if args.references_only:
        pending = []
    items = [(it, False) for it in pending] + [(it, True) for it in refs]

    print(f"\nQueue for {platform}: {len(pending)} clip(s) + {len(refs)} reference(s)\n")
    if not items:
        log.info("queue empty — nothing to analyze")
        print("Nothing pending. If you expected clips, run the media stage in ReelScraper:\n"
              f"  uv run download_media.py {platform}\n")
        hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform,
                     msg="queue empty", data={"analyzed": 0})
        return 0

    if not _secret_present():
        log.error("GEMINI_API_KEY absent — cannot run a live analysis")
        print("ERROR: no Gemini key in env (GEMINI_API_KEY / GEMINI_KEY / GOOGLE_API_KEY).\n"
              "Export it, then re-run. `status` reports secret presence.\n", file=sys.stderr)
        hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform,
                     msg="missing GEMINI_API_KEY")
        return 3

    gemini = GeminiClient(_gemini_key(), model=cfg["model"])
    judge_gemini = GeminiClient(_gemini_key(), model=cfg["judge_model"])
    judge = Judge(judge_gemini, memory.load_rubric(), threshold=int(cfg["score_threshold"]))
    breaker = CircuitBreaker(max_strikes=3)

    analyzed = failed = 0
    for item, is_ref in items:
        cid = item.get("content_id", "?")
        breaker.pace()
        try:
            analyze_one(item, platform, cfg, gemini, judge, hub, run_id, is_ref)
            analyzed += 1
            breaker.record_success()
        except CircuitTripped as e:
            log.critical("circuit breaker tripped — stopping", extra={"err": str(e)})
            hub.post_log(AGENT_NAME, "run.error", level="error", run_id=run_id, platform=platform,
                         msg=f"circuit tripped: {e}")
            break
        except (GeminiError, HubError) as e:
            failed += 1
            log.error("clip failed", extra={"content_id": cid, "err": str(e)})
            hub.post_log(AGENT_NAME, "item.error", level="error", run_id=run_id, platform=platform,
                         content_id=cid, msg=str(e), data={"stage": "Failed"})
            try:
                breaker.record_failure(str(e))
            except CircuitTripped as e2:
                log.critical("circuit breaker tripped — stopping", extra={"err": str(e2)})
                break

    # One transferable insight per run (companion D1 step 9).
    if analyzed:
        try:
            hub.post_insight(
                f"AnalysisEngine analyzed {analyzed} {platform} clip(s) into schema_version 2 "
                "blueprints; the self-eval judge hard-fails placeholder shot_prompt_sequence and "
                "missing audio_strategy, which keeps generation-ready prompts honest.",
                platform="shared", kind="method",
                tags=["analysis-engine", "self-eval", platform],
            )
        except HubError as e:
            log.debug("insight post failed", extra={"err": str(e)})

    print(f"\nDone: {analyzed} analyzed, {failed} failed.\n")
    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform,
                 msg=f"analyzed {analyzed}, failed {failed}",
                 data={"analyzed": analyzed, "failed": failed})
    return 0 if failed == 0 else 1


def cmd_once(args) -> int:
    run_id = setup_logging("once")
    boot = Bootstrap(run_id).start()
    hub = boot.hub
    cid = args.content_id

    platforms = [args.platform] if args.platform else [
        p["platform"] for p in hub.platforms() if p.get("has_data")]
    found = None
    for p in platforms:
        for it in hub.pending(p, reanalyze=cid) + hub.reference_pending(p):
            if it.get("content_id") == cid:
                found = (it, p, bool(it.get("is_reference")))
                break
        if found:
            break
    if not found:
        print(f"content_id {cid} not found in any pending/reference queue "
              f"(searched {platforms}). It may already be analyzed — use --platform + the hub "
              f"`stale=true`/`reanalyze` filter.\n", file=sys.stderr)
        return 4
    if not _secret_present():
        print("ERROR: no Gemini key in env; cannot analyze. See `status`.\n", file=sys.stderr)
        return 3

    item, platform, is_ref = found
    gemini = GeminiClient(_gemini_key(), model=boot.cfg["model"])
    judge = Judge(GeminiClient(_gemini_key(), model=boot.cfg["judge_model"]),
                  memory.load_rubric(), threshold=int(boot.cfg["score_threshold"]))
    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=platform, content_id=cid,
                 msg=f"once {cid}")
    ev = analyze_one(item, platform, boot.cfg, gemini, judge, hub, run_id, is_ref)
    print(f"\n{cid}: score={ev['score_0_100']} accepted={ev['accepted']}\n")
    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=platform, content_id=cid,
                 msg="once done", data={"score": ev["score_0_100"]})
    return 0


def cmd_status(args) -> int:
    run_id = setup_logging("status")
    boot = Bootstrap(run_id).start()
    hub = boot.hub

    print(f"\nAnalysisEngine status  (hub: {boot.base})")
    print("=" * 60)
    print("Platforms:")
    for p in hub.platforms():
        print(f"  {p['platform']:<10} items={p.get('items', 0):<6} "
              f"media_ready={p.get('media_ready', 0):<5} analyzed={p.get('analyzed', 0)}")

    print("\nSecret status (env-var NAME only, never values):")
    try:
        for s in hub.secrets_status(AGENT_NAME):
            mark = "present" if s.get("present") else "ABSENT"
            req = "required" if s.get("required") else "optional"
            print(f"  {s.get('name')} (${s.get('env_var')}): {mark}  [{req}]")
    except HubError as e:
        print(f"  (secret status unavailable: {e})")
    # Local resolution cross-check
    print(f"  local resolve: GEMINI_API_KEY {'present' if _secret_present() else 'ABSENT'} "
          f"(checked {', '.join(GEMINI_ENV_VARS)})")

    print("\nEffective config (defaults + hub GET /api/config/agent/analysis-engine):")
    for k, v in boot.cfg.items():
        print(f"  {k} = {v}")
    print()
    return 0


def _install_stop_handler() -> None:
    """Turn the hub's SIGTERM into a KeyboardInterrupt so this run's cleanup actually runs.

    Unlike the scrapers — which set a flag and stop at a safe boundary — this agent has
    nothing worth saving mid-clip, but it does have two things worth RELEASING. The
    per-clip `finally` in `analyze_one` deletes the Gemini File API upload and the local
    `work/<cid>.mp4`; under the default SIGTERM disposition neither runs, so a stopped run
    leaks an uploaded video into Google's File API (it expires on their clock, not ours)
    and leaves a multi-megabyte temp behind on every stop. Raising an exception is the only
    way to reach a `finally`, and KeyboardInterrupt is the one `main` already handles — so
    a stop exits 130 through the existing path rather than inventing a second one."""
    def _handler(signum, frame):
        raise KeyboardInterrupt()

    for s in (signal.SIGTERM,):
        try:
            signal.signal(s, _handler)
        except (ValueError, OSError, AttributeError):
            # A non-main thread or an exotic platform must not stop the agent running at
            # all; it just falls back to the blunt default.
            pass


def main(argv: list[str] | None = None) -> int:
    _install_stop_handler()
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="analysis-engine", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="analyze a platform's pending + reference queue")
    pr.add_argument("platform")
    pr.add_argument("--min-score", type=float, default=None, dest="min_score")
    pr.add_argument("--tier", default=None)
    pr.add_argument("--min-duration", type=float, default=None, dest="min_duration")
    pr.add_argument("--max-duration", type=float, default=None, dest="max_duration")
    pr.add_argument("--content-type", default=None, dest="content_type")
    pr.add_argument("--limit", type=int, default=None)
    pr.add_argument("--stale", action="store_true")
    pr.add_argument("--reanalyze", default=None)
    pr.add_argument("--no-references", action="store_true", dest="no_references",
                    help="skip the reference queue")
    pr.add_argument("--references-only", action="store_true", dest="references_only",
                    help="analyze only the reference queue")
    pr.set_defaults(func=cmd_run)

    po = sub.add_parser("once", help="analyze a single content_id")
    po.add_argument("content_id")
    po.add_argument("--platform", default=None)
    po.set_defaults(func=cmd_once)

    ps = sub.add_parser("status", help="hub health + analyzed counts + secret status")
    ps.set_defaults(func=cmd_status)

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
