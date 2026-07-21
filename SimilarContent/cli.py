#!/usr/bin/env python3
"""cli.py — similar-content's entry point.

    uv run cli.py propose --platform instagram [--count 5] [--top 15] [--topic "..."] [--dry-run]
    uv run cli.py render  --platform instagram --file <name.md> [--force] [--dry-run]
    uv run cli.py render  --platform instagram --all-approved [--limit N]
    uv run cli.py status  [--platform instagram]
    uv run cli.py register

The two halves of the producer:

  `propose`  reads the corpus + blueprints and writes clone recipes into the human gate
             (CLAUDE.md "Method" 1-4 + 7). Free — no image key, no API calls that cost.
  `render`   turns ONE approved recipe into an actual reel. Paid, and human-triggered only.

`render` is what the hub launches when you press Render on an approved card in the Studio
(POST /api/studio/{p}/{file}/render -> this CLI). It is never part of the one-click
pipeline: each frame is a paid image-API call, so rendering only ever happens when a human
asks for it.

Start with `--dry-run`. It parses the recipe, allocates the frame holds and prints every
composed prompt without making a single API call — which is where most mistakes are
visible and where they cost nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import AGENT_NAME, KIND                              # noqa: E402
from engine.circuit import CircuitTripped                        # noqa: E402
from engine.hub import ConfigConflict, HubClient, HubError       # noqa: E402
from engine.logsetup import setup_logging                        # noqa: E402
from engine.nanobanana import ImageError                         # noqa: E402
from engine.propose import (                                     # noqa: E402
    BACKFILL_ORDERS, EASE_THRESHOLD, ProposeError, automation_threshold, build_recipe,
    diagnose_ease, rank_targets, recipe_filename, restore_origin, score_targets,
)
from engine.recipe import RecipeError                            # noqa: E402
from engine.render import already_rendered, render_item          # noqa: E402
from engine.stitch import StitchError, ffmpeg_available          # noqa: E402

DEFAULTS = {
    "top_n": 5,                   # how many recipes one `propose` run publishes
    "prefer_blueprint": True,     # schema-2 blueprint is the source of truth when present
    "ease_threshold": EASE_THRESHOLD,   # the ease gate; see engine/propose.py::score_ease
    "ease_restore_to": None,      # where a lowered threshold came from (D6)
    "ease_auto_restore": False,   # opt-in: let a run put the threshold back (never lower it)
    "backfill_order": "virality",  # order of the remainder when too few clear the gate
    "image_provider": "nano_banana",
    "aspect_ratio": "9:16",       # reels; the canvas is derived from this, not set separately
    "video_fit": "auto",          # crop near-9:16 frames, letterbox far-off ones
    "max_frames_per_clone": 12,
    "frame_min_hold_s": 0.6,
    "video_fps": 30,
    "caption_model": "gemini-2.5-flash",
    "caption_temperature": 0.8,
    "image_retries": 3,
    "pace_seconds": 2.0,
}


def _load_dotenv(path: Path) -> dict:
    """Read a gitignored .env for LOCAL use only. Values never leave this process."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _image_config() -> dict:
    return json.loads((HERE / "image_config.json").read_text(encoding="utf-8"))


def _resolve_api_key(provider_cfg: dict, dotenv: dict) -> str | None:
    env_var = provider_cfg.get("api_key_env")
    if not env_var:
        return None
    return os.environ.get(env_var) or dotenv.get(env_var)


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


def bootstrap(platform: str | None, need_key: bool = True):
    """Verify the hub, self-register, and layer hub config over the local defaults."""
    base = os.environ.get("BACKEND_API", "http://127.0.0.1:8787")
    hub = HubClient(base)
    if not hub.health_ok():
        print(f"\nERROR: the hub at {base} is not reachable.\n"
              "Start it:  cd ../ReelScraper && uv run cli.py start\n", file=sys.stderr)
        raise SystemExit(2)
    _refuse_foreign_hub(hub, base)

    try:
        import register
        hub.register_producer(register.manifest)
    except (ImportError, HubError) as e:
        print(f"[warn] producer registration failed (continuing): {e}", file=sys.stderr)

    cfg = dict(DEFAULTS)
    try:
        cfg.update(hub.agent_config(AGENT_NAME))
    except HubError as e:
        print(f"[warn] config fetch failed; using defaults: {e}", file=sys.stderr)

    img = _image_config()
    provider = str(cfg.get("image_provider") or img.get("active") or "nano_banana")
    pcfg = (img.get("providers") or {}).get(provider) or {}
    cfg["image_provider"] = provider
    cfg["image_endpoint"] = pcfg.get("endpoint")
    cfg["image_model"] = pcfg.get("model")
    cfg["_api_key"] = _resolve_api_key(pcfg, _load_dotenv(HERE / ".env"))

    if need_key and not cfg["_api_key"]:
        env_var = pcfg.get("api_key_env") or "<none>"
        print(f"\nERROR: no API key for image provider {provider!r} "
              f"(expected {env_var} in the environment or SimilarContent/.env).\n"
              "Run with --dry-run to compose prompts without calling the API.\n",
              file=sys.stderr)
        raise SystemExit(2)
    return hub, cfg


# ---------------------------------------------------------------------------------------
# Hub config arrives as whatever produced it — a Dashboard number input, a hand-edited
# similar-content.json, or a `curl -X PUT`. The hub stores `body.config` verbatim and does
# NOT validate it against the manifest's config_schema, so these parsers are the only place
# `minimum`/`maximum`/`type` are enforced. They are deliberately strict: a knob that cannot
# be read as what it claims to be falls back to the default and SAYS SO, because the failure
# mode of guessing is a gate of 1 (where every clip is "easy" and the ranking is noise) or an
# `ease_auto_restore` of `"false"` that turns automation on.
def _warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def _as_int(value, default: int, *, name: str = "value",
            lo: int | None = None, hi: int | None = None) -> int:
    """An integer knob, clamped to the schema's range. `True` is not 1 here: a boolean in an
    integer knob is corruption, and `ease_threshold: true` silently became a gate of 1."""
    if isinstance(value, bool):
        _warn(f"{name}={value!r} is a boolean, not a number — using {default}")
        return default
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        if value not in (None, ""):
            _warn(f"{name}={value!r} is not a number — using {default}")
        return default
    if lo is not None and out < lo:
        _warn(f"{name}={value!r} is below {lo} — using {lo}")
        return lo
    if hi is not None and out > hi:
        _warn(f"{name}={value!r} is above {hi} — using {hi}")
        return hi
    return out


def _as_int_or_none(value, *, name: str = "value",
                    lo: int = 0, hi: int = 100) -> int | None:
    """An optional integer knob. Out of range reads as ABSENT rather than as a clamped value:
    `ease_restore_to` is a restore TARGET, and inventing one a human never chose is the
    opposite of the point. The next run records a fresh origin if the threshold warrants."""
    if value is None or value == "" or isinstance(value, bool):
        if isinstance(value, bool):
            _warn(f"{name}={value!r} is a boolean, not a number — ignoring it")
        return None
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        _warn(f"{name}={value!r} is not a number — ignoring it")
        return None
    if out < lo or out > hi:
        _warn(f"{name}={value!r} is outside {lo}..{hi} — ignoring it")
        return None
    return out


_TRUE = {"true", "1", "yes", "on"}


def _as_bool(value, default: bool, *, name: str = "value") -> bool:
    """A boolean knob, parsed strictly. `bool("false")` is True, and `ease_auto_restore` is
    the knob that decides whether a run may write the threshold at all — the string "false"
    from a hand-edited config must not turn automation ON."""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    got = str(value).strip().lower()
    if got in _TRUE:
        return True
    if got in {"false", "0", "no", "off"}:
        return False
    _warn(f"{name}={value!r} is not a boolean — using {default}")
    return default


def _ease_lifecycle(hub, args, run_id: str, targets, *, count: int, threshold: int,
                    restore_to: int | None, auto_restore: bool,
                    ad_hoc: bool = False) -> float:
    """Report what the ease gate did to this pool, and put the threshold back if allowed.

    Two channels, always the same numbers (the Dashboard must never have to parse prose back
    out of a sentence): the operator's terminal, and POST /api/logs with structured `data`.

    THE ONLY CONFIG WRITE THIS AGENT EVER MAKES lives here, and it goes through
    engine/propose.py::automation_threshold, which cannot return a value below the one in
    force, AND through a compare-and-set on ease_threshold, so it cannot overwrite a change
    made while the run was fetching blueprints. A starved gate is REPORTED and never acted
    on: the message says what to lower the threshold to, and a human does it. Returns the
    threshold actually in force afterwards.

    `ad_hoc` is set when the operator narrowed this run by hand (--count / --top / --topic).
    D4's anti-flap margin is "more than `count` candidates in the pool" — with a hand-picked
    count and a hand-picked pool that test means nothing about the corpus: `--count 2` over a
    pool with three clips above the record would restore a gate that every scheduled run at
    `top_n: 5` correctly leaves alone, and clear `ease_restore_to` permanently. So an ad-hoc
    run reports and never restores.
    """
    dry = bool(args.dry_run)

    # D6 — lowering records where it came from, so a restore has something to aim at. Only
    # the EMPTY case is filled in; see restore_origin for why an existing record is never
    # recomputed.
    origin = restore_origin(threshold, restore_to)
    if origin != restore_to:
        if dry:
            print(f"[dry-run] would record ease_restore_to={origin}")
        else:
            try:
                # Guarded for the same reason the restore is: if a human wrote a restore
                # target while this run was scoring, theirs is the one that stands.
                hub.update_agent_config(AGENT_NAME, {"ease_restore_to": origin},
                                        expect={"ease_restore_to": restore_to})
                if origin is not None:
                    print(f"[ease] ease_threshold {threshold} is below {origin} — recorded "
                          f"ease_restore_to={origin} so it can be put back.")
            except HubError as e:                   # never fatal: this is bookkeeping
                print(f"[warn] could not record ease_restore_to: {e}", file=sys.stderr)

    # A run may only ACT on a restore target that predates it. The origin recorded seconds
    # ago was fed straight back into the restore decision, so a human lowering the gate to 40
    # had it put back to 55 by the same run that noticed — two writes whose net effect was
    # "ignore the operator", with no new fact about the corpus in between, and D5's
    # prompt-first step never happened at all. Reporting still uses the origin: "the corpus
    # now supports 55" is worth saying the first time it is true, and saying it is all a run
    # is allowed to do that time.
    diag = diagnose_ease(targets, count=count, threshold=threshold, restore_to=origin,
                         auto_restore=(auto_restore and not ad_hoc
                                       and restore_to is not None))
    applied = None

    if diag.kind == "restored":
        raised = automation_threshold(threshold, restore_to)  # <- D3: max(), cannot lower
        if dry:
            print(f"[dry-run] would restore ease_threshold {threshold} -> {int(raised)}")
            diag = diag.unapplied("nothing was written (--dry-run):")
        else:
            try:
                # expect= is the other half of D3: refuse if ease_threshold moved mid-run.
                hub.update_agent_config(
                    AGENT_NAME, {"ease_threshold": int(raised), "ease_restore_to": None},
                    expect={"ease_threshold": threshold})
                for t in targets:
                    t.ease = t.ease.at(raised)   # re-gate: scores never move, verdicts do
                threshold, applied = raised, raised
            except ConfigConflict as e:
                print(f"[warn] restore abandoned — {e}", file=sys.stderr)
                diag = diag.unapplied("ease_threshold was changed in the hub while this run "
                                      "was scoring, so nothing was written:")
            except HubError as e:
                print(f"[warn] restore could not be saved, keeping {threshold}: {e}",
                      file=sys.stderr)
                diag = diag.unapplied(f"the restore could not be saved ({e}):")
    elif ad_hoc and diag.kind == "restore_ready":
        diag = diag.unapplied("this run's pool was narrowed by hand (--count/--top/--topic) "
                              "so it cannot speak for the corpus:")

    if diag.actionable:
        marker = "!!" if diag.kind == "starved" else "->"
        print(f"\n  {marker} ease gate: {diag.msg}\n")
    hub.post_log(AGENT_NAME, diag.event, run_id=run_id, platform=args.platform,
                 level=diag.level, msg=diag.msg,
                 data={**diag.data, "applied_threshold": applied, "dry_run": dry})
    return threshold


def cmd_propose(args) -> int:
    """Rank the corpus, attach blueprints, and publish the easiest-to-make winners.

    Needs NO image-provider key: this reads blueprints and writes markdown. The paid half is
    `render`, and it only runs after a human approves what this produced.
    """
    run_id = f"sc-{time.strftime('%Y%m%dT%H%M%S')}"
    setup_logging("propose", args.platform)
    hub, cfg = bootstrap(args.platform, need_key=False)

    count = args.count or _as_int(cfg.get("top_n"), 5, name="top_n", lo=1)
    pool = args.top or max(15, count * 3)
    prefer_bp = _as_bool(cfg.get("prefer_blueprint"), True, name="prefer_blueprint")
    # Hand-narrowed runs report on the gate but never restore it — see _ease_lifecycle.
    ad_hoc = bool(args.count or args.top or args.topic)

    # ---- the ease gate's knobs, snapshotted for this run (§10.3) -------------------------
    threshold = _as_int(cfg.get("ease_threshold"), EASE_THRESHOLD, name="ease_threshold",
                        lo=0, hi=100)
    restore_to = _as_int_or_none(cfg.get("ease_restore_to"), name="ease_restore_to")
    auto_restore = _as_bool(cfg.get("ease_auto_restore"), False, name="ease_auto_restore")
    backfill_order = str(cfg.get("backfill_order") or "virality")
    if backfill_order not in BACKFILL_ORDERS:
        print(f"[warn] backfill_order={backfill_order!r} is not one of "
              f"{list(BACKFILL_ORDERS)} — using 'virality'", file=sys.stderr)
        backfill_order = "virality"

    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=args.platform,
                 msg=f"propose {count} clone recipe(s) from the top {pool}",
                 data={"topic": args.topic, "prefer_blueprint": prefer_bp,
                       "ease_threshold": threshold, "ease_restore_to": restore_to,
                       "ease_auto_restore": auto_restore,
                       "backfill_order": backfill_order})

    try:
        targets = score_targets(hub, args.platform, pool=pool, topic=args.topic,
                                prefer_blueprint=prefer_bp,
                                content_ids=getattr(args, "content_ids", None),
                                threshold=threshold)
    except (ProposeError, HubError) as e:
        print(f"\nERROR: {e}\n", file=sys.stderr)
        hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=args.platform,
                     level="error", msg=str(e))
        return 2

    if getattr(args, "content_ids", None):
        # Explicitly named exemplars: the caller's order IS the ranking, and a gate report
        # about a pool of exactly the clips they asked for would say nothing.
        picks = targets
    else:
        threshold = _ease_lifecycle(hub, args, run_id, targets, count=count,
                                    threshold=threshold, restore_to=restore_to,
                                    auto_restore=auto_restore, ad_hoc=ad_hoc)
        picks = rank_targets(targets, count, backfill_order)
    targets = picks

    published, failed = [], 0
    for rank, t in enumerate(targets, 1):
        name = recipe_filename(t, rank)
        hub.post_log(AGENT_NAME, "item.start", run_id=run_id, platform=args.platform,
                     content_id=t.content_id, msg=f"building recipe for {t.title[:60]}",
                     data={"stage": "Generating", "file": name})
        text = build_recipe(args.platform, t.row, t.blueprint, t.ease)

        if args.dry_run:
            print(f"[dry-run] would POST /api/studio/{args.platform}  {name}  "
                  f"({len(text)} chars, {t.n_shots if t.n_shots is not None else '?'} shots)")
        else:
            try:
                # No `status` in the body on purpose — see HubClient.post_studio: it lets the
                # hub preserve a human's existing decision instead of un-approving an item.
                hub.post_studio(args.platform, name, text, agent=AGENT_NAME, kind=KIND)
            except HubError as e:
                print(f"[fail] {name}: {e}", file=sys.stderr)
                hub.post_log(AGENT_NAME, "item.error", run_id=run_id, platform=args.platform,
                             level="error", content_id=t.content_id, msg=str(e),
                             data={"stage": "Failed", "file": name})
                failed += 1
                continue
            hub.post_log(AGENT_NAME, "item.done", run_id=run_id, platform=args.platform,
                         content_id=t.content_id, msg=f"proposed {name}",
                         data={"stage": "Proposed", "file": name,
                               "ease_score": t.ease.score,
                               "ease_easy": t.ease.easy,
                               "ease_threshold": t.ease.threshold,
                               "virality_score": t.virality_score})
        published.append((rank, t, name))

    print("\n  #  virality  tier          shots  dur     ease   gate  file")
    print("  " + "-" * 94)
    for rank, t, name in published:
        print(f"  {rank:<2} {str(t.row.get('virality_score')):<9} "
              f"{str(t.row.get('tier'))[:13]:<13} "
              f"{str(t.n_shots if t.n_shots is not None else '-'):<6} "
              f"{(f'{t.duration_s:.2f}' if t.duration_s else '-'):<7} "
              f"{t.ease.score:<6g} {'easy' if t.ease.easy else 'FILL':<5} {name}")
    backfilled = sum(1 for _, t, _ in published if not t.ease.easy)
    print(f"\n{len(published)} proposed, {failed} failed"
          + (f"  ({backfilled} by virality backfill, not ease — gate is "
             f"{threshold:g})" if backfilled else "")
          + ("  (dry run — nothing was written)" if args.dry_run else ""))

    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=args.platform,
                 msg=f"{len(published)} proposed, {failed} failed",
                 data={"dry_run": args.dry_run})
    return 0 if failed == 0 else 1


def cmd_render(args) -> int:
    run_id = f"sc-{time.strftime('%Y%m%dT%H%M%S')}"
    setup_logging("render", args.platform)

    if not args.dry_run and not ffmpeg_available():
        print("\nERROR: ffmpeg/ffprobe not found on PATH (brew install ffmpeg).\n",
              file=sys.stderr)
        return 2

    # A re-stitch makes no image calls, so it must not demand an image-provider key.
    hub, cfg = bootstrap(args.platform,
                         need_key=not (args.dry_run or args.restitch))
    if args.max_frames:
        cfg["max_frames_per_clone"] = args.max_frames

    if args.file:
        try:
            items = [hub.studio_item(args.platform, args.file)]
        except HubError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    else:
        items = hub.studio(args.platform, status="approved", agent=AGENT_NAME)
        if args.limit:
            items = items[: args.limit]
        if not items:
            print("Nothing to render — no approved similar-content items.\n"
                  "Approve one in the Dashboard's Studio view first.")
            return 0

    hub.post_log(AGENT_NAME, "run.start", run_id=run_id, platform=args.platform,
                 msg=f"render run over {len(items)} item(s)")

    ok = failed = skipped = 0
    for item in items:
        name = item.get("file")
        if item.get("status") != "approved":
            print(f"[skip] {name}: status is {item.get('status')!r}, not approved")
            skipped += 1
            continue
        if not args.dry_run and not args.force and not args.restitch and \
                already_rendered(hub, args.platform, name, item.get("updated_at")):
            print(f"[skip] {name}: already rendered (use --force to redo)")
            skipped += 1
            continue

        try:
            result = render_item(hub, cfg, item["text"], name, args.platform,
                                 run_id=run_id, dry_run=args.dry_run,
                                 reuse_frames=args.restitch)
            ok += 1
            if not args.dry_run:
                where = result.mp4
                print(f"[ok]   {name}\n       {where}  "
                      f"({result.duration_s}s, {len(result.frames)} frame(s))")
                if result.caption:
                    print(f"       caption: {result.caption['caption'][:80]}…")
        except RecipeError as e:
            print(f"[fail] {name}: {e}", file=sys.stderr)
            failed += 1
        except CircuitTripped as e:
            # Three consecutive image failures: a bad key or an exhausted quota. Stop the
            # whole run rather than burning the queue one paid failure at a time.
            print(f"\nABORTED: {e}", file=sys.stderr)
            hub.post_log(AGENT_NAME, "item.error", run_id=run_id, platform=args.platform,
                         level="error", msg=str(e),
                         data={"file": name, "stage": "Failed"})
            failed += 1
            break
        except (ImageError, StitchError, HubError) as e:
            print(f"[fail] {name}: {e}", file=sys.stderr)
            hub.post_log(AGENT_NAME, "item.error", run_id=run_id, platform=args.platform,
                         level="error", msg=str(e),
                         data={"file": name, "stage": "Failed"})
            failed += 1

    hub.post_log(AGENT_NAME, "run.end", run_id=run_id, platform=args.platform,
                 msg=f"{ok} rendered, {failed} failed, {skipped} skipped")
    print(f"\n{ok} rendered, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


def cmd_status(args) -> int:
    hub, cfg = bootstrap(args.platform, need_key=False)
    approved = hub.studio(args.platform, status="approved", agent=AGENT_NAME)
    rendered = {r.get("file") for r in hub.renders(args.platform)}
    done = sum(1 for a in approved if a["file"] in rendered)
    print(f"provider : {cfg['image_provider']}  "
          f"(key {'present' if cfg['_api_key'] else 'MISSING'})")
    print(f"ffmpeg   : {'present' if ffmpeg_available() else 'MISSING'}")
    print(f"approved : {len(approved)}   rendered: {done}")
    for a in approved:
        print(f"  [{'x' if a['file'] in rendered else ' '}] {a['file']}")
    return 0


def cmd_register(args) -> int:
    import register
    hub, _ = bootstrap(None, need_key=False)
    hub.register_producer(register.manifest)
    print(f"registered {AGENT_NAME} (renderable, dir=SimilarContent)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="similar-content")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("propose", help="rank the corpus and publish clone recipes (free)")
    p.add_argument("--platform", default="instagram")
    p.add_argument("--count", type=int,
                   help="how many recipes to publish (default: the `top_n` hub knob)")
    p.add_argument("--top", type=int,
                   help="corpus pool to consider (default: max(15, 3x count))")
    p.add_argument("--topic", help="focus on a topic via /corpus/{p}/search instead of /top")
    p.add_argument("--content-id", action="append", dest="content_ids", metavar="ID",
                   help="propose these exact exemplars, skipping ranking (repeatable). "
                        "Ranking cannot reach a mid-corpus clip, e.g. a freshly scraped creator.")
    p.add_argument("--dry-run", action="store_true",
                   help="select + build the recipes but POST nothing")
    p.set_defaults(fn=cmd_propose)

    r = sub.add_parser("render", help="render approved clone recipes into reels")
    r.add_argument("--platform", default="instagram")
    r.add_argument("--file", help="one studio .md filename (what the hub passes)")
    r.add_argument("--all-approved", action="store_true",
                   help="render every approved item that has no render yet")
    r.add_argument("--limit", type=int, help="cap --all-approved")
    r.add_argument("--max-frames", type=int, help="override max_frames_per_clone")
    r.add_argument("--force", action="store_true", help="re-render even if one exists")
    r.add_argument("--restitch", action="store_true",
                   help="re-encode the frames already on disk (free — no image calls, same "
                        "pictures, existing caption kept). Use after a stitcher change.")
    r.add_argument("--dry-run", action="store_true",
                   help="parse + compose prompts, make no API calls (free)")
    r.set_defaults(fn=cmd_render)

    s = sub.add_parser("status", help="what is approved, what is rendered")
    s.add_argument("--platform", default="instagram")
    s.set_defaults(fn=cmd_status)

    g = sub.add_parser("register", help="(re)register this producer with the hub")
    g.set_defaults(fn=cmd_register)

    args = ap.parse_args()
    if args.cmd == "render" and not args.file and not args.all_approved:
        ap.error("render needs --file <name.md> or --all-approved")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
