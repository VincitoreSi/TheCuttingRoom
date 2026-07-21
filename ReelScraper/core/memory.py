#!/usr/bin/env python3
"""
core/memory.py — the memory layer, split exactly the way you asked:

  • PER-PLATFORM memory  (optimized separately, because X != Instagram != YouTube)
        memory/<platform>/content.db   -> searchable recall of that platform's content
        memory/<platform>/*.md         -> that agent's own patterns / persona / decisions
    Each agent keeps its own tuned memory; nothing platform-specific leaks across.

  • SHARED insights exchange  (the ONLY cross-agent channel)
        memory/shared/insights.jsonl   -> structured log every agent appends to
        memory/shared/INSIGHTS.md      -> human-readable render
    Carries only what TRANSFERS: core method notes, interesting findings, and
    NEGATIVE patterns (what didn't work) so other agents don't repeat mistakes.

Recall uses SQLite FTS5 (built in, zero deps) for lexical search today; a semantic
upgrade (sqlite-vec + a local embedder) can slot in behind the same search() API
later without changing callers. Fully local, free, private.
"""
import json, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEM = ROOT / "memory"

INSIGHT_KINDS = ("finding", "negative", "method", "idea")


def _fts_available(con):
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        con.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


class ContentMemory:
    """Per-platform searchable store of scored content (for 'find past reels like X')."""

    def __init__(self, platform):
        self.platform = platform
        self.dir = MEM / platform
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = self.dir / "content.db"
        self.con = sqlite3.connect(self.db)
        self.fts = _fts_available(self.con)
        if self.fts:
            self.con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS content USING fts5("
                "content_id UNINDEXED, creator, url UNINDEXED, posted_iso UNINDEXED, "
                "virality_score UNINDEXED, tier UNINDEXED, plays UNINDEXED, caption)")
        else:
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS content(content_id TEXT PRIMARY KEY, creator TEXT, "
                "url TEXT, posted_iso TEXT, virality_score REAL, tier TEXT, plays INTEGER, caption TEXT)")
        self.con.commit()

    def upsert(self, rows):
        """rows: analyzed dicts from core.virality.analyze(). Dedup by content_id."""
        n = 0
        for r in rows:
            cid = r.get("content_id")
            if not cid:
                continue
            self.con.execute("DELETE FROM content WHERE content_id=?", (cid,))
            self.con.execute(
                "INSERT INTO content(content_id,creator,url,posted_iso,virality_score,tier,plays,caption) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (cid, r.get("creator"), r.get("url"), r.get("posted_iso"),
                 r.get("virality_score"), r.get("tier"), r.get("plays"), r.get("caption") or ""))
            n += 1
        self.con.commit()
        return n

    def search(self, query, k=10):
        if self.fts:
            q = " OR ".join(w for w in query.replace('"', " ").split() if w) or query
            try:
                cur = self.con.execute(
                    "SELECT creator,url,posted_iso,virality_score,tier,plays,caption "
                    "FROM content WHERE content MATCH ? ORDER BY rank LIMIT ?", (q, k))
            except sqlite3.OperationalError:
                cur = self.con.execute(
                    "SELECT creator,url,posted_iso,virality_score,tier,plays,caption "
                    "FROM content WHERE caption LIKE ? LIMIT ?", (f"%{query}%", k))
        else:
            cur = self.con.execute(
                "SELECT creator,url,posted_iso,virality_score,tier,plays,caption "
                "FROM content WHERE caption LIKE ? OR creator LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", k))
        cols = ["creator", "url", "posted_iso", "virality_score", "tier", "plays", "caption"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM content").fetchone()[0]


class SharedInsights:
    """The cross-agent exchange: transferable findings + negative patterns + method notes."""

    def __init__(self):
        self.dir = MEM / "shared"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "insights.jsonl"
        self.md = self.dir / "INSIGHTS.md"

    def add(self, platform, kind, text, tags=None, ts=None):
        assert kind in INSIGHT_KINDS, f"kind must be one of {INSIGHT_KINDS}"
        rec = {"ts": ts or int(time.time()), "platform": platform, "kind": kind,
               "text": text.strip(), "tags": tags or []}
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._render()
        return rec

    def all(self):
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
        return out

    def search(self, query, k=20):
        q = query.lower()
        hits = [r for r in self.all() if q in r["text"].lower()
                or any(q in t.lower() for t in r.get("tags", []))]
        return hits[-k:]

    def _render(self):
        recs = self.all()
        by_kind = {}
        for r in recs:
            by_kind.setdefault(r["kind"], []).append(r)
        titles = {"method": "🧭 Core method / use-case (applies to all platforms)",
                  "finding": "💡 Interesting findings (may transfer)",
                  "negative": "⛔ Negative patterns — what did NOT work",
                  "idea": "🧪 Open ideas / hypotheses to test"}
        lines = ["# Shared insights exchange",
                 "",
                 "_Cross-platform learnings written by the platform agents. Read this at the "
                 "start of a run; append transferable findings and dead-ends at the end._",
                 f"_{len(recs)} entries._", ""]
        for kind in ("method", "finding", "negative", "idea"):
            rs = by_kind.get(kind) or []
            if not rs:
                continue
            lines.append(f"## {titles[kind]}")
            for r in rs:
                when = time.strftime("%Y-%m-%d", time.gmtime(r["ts"]))
                tags = f"  _{', '.join(r['tags'])}_" if r.get("tags") else ""
                lines.append(f"- **[{r['platform']}·{when}]** {r['text']}{tags}")
            lines.append("")
        self.md.write_text("\n".join(lines), encoding="utf-8")
