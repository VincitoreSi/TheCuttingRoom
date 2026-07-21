#!/usr/bin/env python3
"""
core/corpus.py — the ADAPTER that producer/generator agents read through.

Analyst agents WRITE the corpus (scrape -> score -> memory). Generator agents
(SimilarContent, ProposalContent, AutoContent) READ it through this one interface,
so adding a new generator never touches scraping or scoring — it just consumes a Corpus.

A Corpus(platform) exposes:
  top_viral(n)   -> the highest virality_score items (the exemplars to emulate)
  factors()      -> deterministic virality factors: which duration / posting-time /
                    caption / hashtag buckets lift virality_score above baseline
  exemplars(q,n) -> semantic/lexical recall of past content (via memory)
  insights()     -> shared transferable findings + NEGATIVE patterns
  persona()/patterns() -> this platform agent's evolving voice + learnings
  brief(q)       -> all of the above assembled into one markdown generation brief

Reads the analyzer OUTPUT (platforms/<p>/virality_reels.csv) — the stable adapter
boundary — plus memory/. Fully local, no external calls.
"""
import csv, json, statistics, time
from pathlib import Path

from core.memory import ContentMemory, SharedInsights

ROOT = Path(__file__).resolve().parents[1]
MIN_BUCKET = 12   # ignore buckets with fewer items (noise)


def _f(x):
    try: return float(x)
    except Exception: return None

def _i(x):
    v = _f(x)
    return int(v) if v is not None else None


class Corpus:
    def __init__(self, platform):
        self.platform = platform
        self.pdir = ROOT / "platforms" / platform
        self.adir = ROOT / "analysis" / platform
        self.csv = self.pdir / "virality_reels.csv"
        self.rows = self._load()

    def _load(self):
        if not self.csv.exists():
            return []
        rows = []
        with open(self.csv, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["_score"] = _f(r.get("virality_score"))
                r["_dur"] = _f(r.get("duration_s"))
                r["_plays"] = _i(r.get("plays"))
                rows.append(r)
        return [r for r in rows if r["_score"] is not None]

    # ---- exemplars ----
    def top_viral(self, n=15):
        return sorted(self.rows, key=lambda r: -r["_score"])[:n]

    def exemplars(self, query, n=10):
        return ContentMemory(self.platform).search(query, k=n)

    # ---- video frame-by-frame analysis (written by the VideoAnalysis agent) ----
    def analysis(self, content_id):
        """The Gemini frame-by-frame analysis for one clip, or None."""
        f = self.adir / f"{content_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None

    def analyzed_exemplars(self, n=8):
        """Top-viral items that HAVE a video analysis, with the analysis attached."""
        out = []
        for r in self.top_viral(n * 3):
            a = self.analysis(r.get("content_id"))
            if a:
                out.append({**r, "_analysis": a})
            if len(out) >= n:
                break
        return out

    # ---- factors ----
    def _baseline(self):
        s = [r["_score"] for r in self.rows]
        return round(statistics.mean(s), 1) if s else None

    def _bucketize(self, feature, fn, order):
        groups = {}
        for r in self.rows:
            b = fn(r)
            if b is None:
                continue
            groups.setdefault(b, []).append(r["_score"])
        base = self._baseline() or 0
        out = []
        for b, scores in groups.items():
            if len(scores) < MIN_BUCKET:
                continue
            m = statistics.mean(scores)
            out.append({"feature": feature, "bucket": b, "n": len(scores),
                        "mean_score": round(m, 1), "lift": round(m - base, 1)})
        out.sort(key=lambda x: order.index(x["bucket"]) if x["bucket"] in order else 99)
        return out

    @staticmethod
    def _dur_bucket(r):
        d = r["_dur"]
        if d is None: return None
        return ("0-7s" if d < 7 else "7-15s" if d < 15 else "15-30s"
                if d < 30 else "30-60s" if d < 60 else "60s+")

    @staticmethod
    def _hour_bucket(r):
        p = r.get("posted") or ""
        try:
            h = time.strptime(p, "%Y-%m-%d %H:%M:%S").tm_hour
        except Exception:
            return None
        return ("night 0-6" if h < 6 else "morning 6-12" if h < 12
                else "afternoon 12-17" if h < 17 else "evening 17-21" if h < 21 else "late 21-24")

    @staticmethod
    def _caplen_bucket(r):
        n = len(r.get("caption") or "")
        return ("none/short <50" if n < 50 else "medium 50-150" if n < 150
                else "long 150-300" if n < 300 else "very long 300+")

    @staticmethod
    def _hashtag_bucket(r):
        n = (r.get("caption") or "").count("#")
        return ("0 tags" if n == 0 else "1-3 tags" if n <= 3 else "4-7 tags" if n <= 7 else "8+ tags")

    def factors(self):
        base = self._baseline()
        feats = []
        feats += self._bucketize("duration", self._dur_bucket, ["0-7s","7-15s","15-30s","30-60s","60s+"])
        feats += self._bucketize("posting_time(UTC)", self._hour_bucket,
                                 ["night 0-6","morning 6-12","afternoon 12-17","evening 17-21","late 21-24"])
        feats += self._bucketize("caption_length", self._caplen_bucket,
                                 ["none/short <50","medium 50-150","long 150-300","very long 300+"])
        feats += self._bucketize("hashtags", self._hashtag_bucket, ["0 tags","1-3 tags","4-7 tags","8+ tags"])
        winners = sorted([f for f in feats if f["lift"] > 0], key=lambda x: -x["lift"])
        losers = sorted([f for f in feats if f["lift"] < 0], key=lambda x: x["lift"])
        return {"baseline": base, "all": feats, "winners": winners, "losers": losers}

    # ---- memory text ----
    def insights(self):
        return SharedInsights().all()

    def _read(self, name):
        p = ROOT / "memory" / self.platform / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def persona(self): return self._read("persona.md")
    def patterns(self): return self._read("patterns.md")

    # ---- assembled brief ----
    def brief(self, query=None, top=12):
        fx = self.factors()
        L = [f"# Generation brief — {self.platform}", ""]
        L += [f"Corpus: {len(self.rows)} scored items · baseline virality {fx['baseline']}", ""]
        L += ["## Virality factors (lift vs baseline)"]
        for f in fx["winners"][:8]:
            L.append(f"- ✅ {f['feature']} = **{f['bucket']}** → +{f['lift']} (n={f['n']}, mean {f['mean_score']})")
        for f in fx["losers"][:5]:
            L.append(f"- ⛔ {f['feature']} = {f['bucket']} → {f['lift']} (n={f['n']})")
        L += ["", "## Top viral exemplars (emulate the structure, not the exact content)"]
        for r in self.top_viral(top):
            L.append(f"- [{r.get('tier')}/{r.get('virality_score')}] {r.get('creator')} · "
                     f"{r.get('duration_s')}s · plays {r.get('plays')} · {r.get('url')}\n"
                     f"    “{(r.get('caption') or '')[:140].strip()}”")
        analyzed = self.analyzed_exemplars(8)
        if analyzed:
            L += ["", "## Visual formulas (frame-by-frame analysis of top clips)",
                  "What the winning videos actually DO on screen — replicate these mechanics:"]
            for r in analyzed:
                a = r["_analysis"]
                # schema_version 2 (AnalysisEngine): read virality_formula; fall back to lean v1 fields.
                vf = a.get("virality_formula") or {}
                hook = vf.get("hook") or a.get("hook") or {}
                vm = a.get("video_metadata") or {}
                summary = vm.get("one_line_summary") or a.get("summary") or ""
                formula = vf.get("replicable_formula") or a.get("replicable_formula") or "—"
                first = hook.get("first_seconds") or hook.get("on_screen_text") or summary
                L.append(
                    f"- [{r.get('virality_score')}] {r.get('creator')} ({r.get('url')})\n"
                    f"    hook: {hook.get('type') or '?'} — {first}\n"
                    f"    formula: {formula}"
                )
        if query:
            L += ["", f"## Recall for “{query}”"]
            for h in self.exemplars(query, 8):
                L.append(f"- [{h['tier']}/{h['virality_score']}] {h['creator']} · {h['url']}")
        ins = self.insights()
        if ins:
            L += ["", "## Shared insights (apply findings, avoid negatives)"]
            for r in ins:
                L.append(f"- [{r['kind']}·{r['platform']}] {r['text']}")
        per = self.persona().strip()
        if per:
            L += ["", "## Voice", per]
        return "\n".join(L)
