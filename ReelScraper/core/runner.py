#!/usr/bin/env python3
"""
core/runner.py — shared CLI every platform's run.py delegates to.

A platform's run.py is just:
    from core.runner import run_cli
    from normalize import load_records
    run_cli("instagram", Path(__file__).parent, load_records)

Subcommands: analyze | search | insight | insights  (identical across platforms).
"""
import sys, argparse, logging
from pathlib import Path

from core import virality
from core.logsetup import setup_logging
from core.memory import ContentMemory, SharedInsights


def run_cli(platform, here, load_records):
    here = Path(here)
    log = logging.getLogger(f"{platform}.run")

    def cmd_analyze(args):
        records = load_records()
        if not records:
            log.error("no scraped data — scrape first", extra={"platform": platform})
            sys.exit(1)
        weights, tiers, top_n = virality.load_config(here / "niche_config.json")
        rows = virality.analyze(records, weights, tiers)
        out_xlsx = here / "Virality_Analysis.xlsx"
        out_csv = here / "virality_reels.csv"
        virality.write_reports(rows, out_xlsx, out_csv, top_n)
        # machine-readable feed for the API hub / media layer / frontend
        import json as _json
        clean = [{k: v for k, v in r.items() if k not in ("_content",)} for r in rows]
        (here / "content.json").write_text(_json.dumps(clean, ensure_ascii=False), encoding="utf-8")
        mem = ContentMemory(platform)
        added = mem.upsert(rows)
        n_viral = sum(1 for r in rows if r.get("tier") == "Viral")
        creators = len({r["creator"] for r in rows})
        log.info("analyze done", extra={
            "items": len(rows), "creators": creators, "viral": n_viral,
            "weights": weights, "top_n": top_n, "indexed": mem.count(), "added": added,
            "xlsx": out_xlsx.name, "csv": out_csv.name,
        })
        # concise human summary to stdout (the requested result payload)
        print(f"DONE [{platform}]: {len(rows)} items / {creators} creators | {n_viral} Viral", flush=True)
        print(f"  -> {out_xlsx.name} (+ {out_csv.name}) | memory: {mem.count()} indexed (+{added})", flush=True)

    def cmd_search(args):
        for h in ContentMemory(platform).search(args.query, k=args.k):
            print(f"[{h['tier']}/{h['virality_score']}] {h['creator']}  plays={h['plays']}  "
                  f"{h['url']}\n    {(h['caption'] or '')[:120]}", flush=True)

    def cmd_insight(args):
        rec = SharedInsights().add(platform, args.kind, args.text,
                                   tags=[t for t in (args.tags or "").split(",") if t])
        print(f"logged shared {rec['kind']}: {rec['text']}", flush=True)

    def cmd_insights(args):
        si = SharedInsights()
        recs = si.search(args.query) if args.query else si.all()
        for r in recs:
            print(f"[{r['platform']}·{r['kind']}] {r['text']}", flush=True)
        print(f"\n({len(recs)} entries) -> {si.md}", flush=True)

    def cmd_factors(args):
        from core.corpus import Corpus
        fx = Corpus(platform).factors()
        if fx["baseline"] is None:
            print("No corpus yet — run analyze first.", flush=True); return
        print(f"baseline virality = {fx['baseline']}", flush=True)
        print("WINNERS (lift vs baseline):", flush=True)
        for f in fx["winners"]:
            print(f"  +{f['lift']:>5}  {f['feature']} = {f['bucket']}  (n={f['n']}, mean {f['mean_score']})", flush=True)
        print("DRAGS:", flush=True)
        for f in fx["losers"]:
            print(f"  {f['lift']:>6}  {f['feature']} = {f['bucket']}  (n={f['n']})", flush=True)

    def cmd_brief(args):
        from core.corpus import Corpus
        print(Corpus(platform).brief(query=args.query), flush=True)

    ap = argparse.ArgumentParser(prog=f"{platform} run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("analyze").set_defaults(fn=cmd_analyze)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("-k", type=int, default=10); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("insight"); s.add_argument("kind", choices=["finding", "negative", "method", "idea"]); s.add_argument("text"); s.add_argument("--tags"); s.set_defaults(fn=cmd_insight)
    s = sub.add_parser("insights"); s.add_argument("query", nargs="?"); s.set_defaults(fn=cmd_insights)
    sub.add_parser("factors").set_defaults(fn=cmd_factors)
    b = sub.add_parser("brief"); b.add_argument("query", nargs="?"); b.set_defaults(fn=cmd_brief)
    args = ap.parse_args()
    setup_logging(args.cmd, platform)
    args.fn(args)
