#!/usr/bin/env python3
"""
merge_reels.py — combine all reels_raw*.json (single run + parallel workers)
into one final Reels_Data.xlsx. Deduplicates by creator (keeps the larger set).
Ordering: follows pages.txt/creators.txt if present, else by reel count (desc).
Follower counts are pulled from profiles_meta*.json when available.
"""
import json, re
from pathlib import Path
import openpyxl
from scrape import flatten

HERE = Path(__file__).parent
OUT = HERE / "Reels_Data.xlsx"

raw = {}
for f in sorted(HERE.glob("reels_raw*.json")):
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  skip {f.name}: {e}"); continue
    for k, v in d.items():
        if len(v) >= len(raw.get(k, [])):
            raw[k] = v
    print(f"  loaded {f.name}: {len(d)} creators")
print("merged unique creators:", len(raw))

# follower counts (for engagement rate / reach multiplier), if present
meta = {}
for f in sorted(HERE.glob("profiles_meta*.json")):
    try:
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            meta.setdefault(k, v)
    except Exception:
        pass

# ordering
order = list(raw.keys())
listfile = HERE / "pages.txt" if (HERE / "pages.txt").exists() else HERE / "creators.txt"
if listfile.exists():
    pref = []
    for line in listfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", line)
        h = m.group(1) if m else line.lstrip("@").strip("/")
        if h in raw and h not in pref:
            pref.append(h)
    order = pref + [c for c in raw if c not in pref]
else:
    order = sorted(raw, key=lambda c: -len(raw[c]))

all_rows, summary = [], []
for c in order:
    fol = (meta.get(c) or {}).get("followers")
    for m in raw[c]:
        all_rows.append(flatten(m, c, fol))
    summary.append((c, len(raw[c])))

out = openpyxl.Workbook()
sh = out.active; sh.title = "Reels"
cols = list(all_rows[0].keys()) if all_rows else []
if cols:
    sh.append(cols)
    for r in all_rows:
        sh.append([r.get(c) for c in cols])
s2 = out.create_sheet("Summary")
s2.append(["creator", "reels_scraped"])
for c, n in summary:
    s2.append([c, n])
s2.append(["TOTAL", sum(n for _, n in summary)])
out.save(OUT)

print(f"DONE: {len(all_rows)} reels across {len(summary)} creators -> {OUT.name}")
for c, n in summary:
    print(f"  {c}: {n}")
