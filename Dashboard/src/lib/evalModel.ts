// The eval-log data brain (pure, no React). Three facts about the live hub
// data drive every selector here:
//  (a) `overall` is NOT guaranteed on every record — SimilarContent's clone
//      eval carries only a per-criterion rubric, no whole-record summary key.
//      The old `scores.overall ?? scores.score_0_100` read silently dropped
//      it from every chart, stat, and list. `recordScore` fixes that.
//  (b) rubric keys differ by target_type (a blueprint rubric != a clone
//      rubric) — criteria are read generically, never assumed fixed.
//  (c) `ts` is unix SECONDS with a fractional part — kept numeric throughout,
//      never bucketed to a day-string (that collides/misorders same-day runs).
import type { EvalRecord } from "./types";

const META = new Set(["overall", "score_0_100"]);

export type ScoreTone = "sage" | "amber" | "danger";

/** The AA-tuned ink ramp — clears contrast on --surface-2/3 in both themes. */
export const TONE_VAR: Record<ScoreTone, string> = {
  sage: "var(--sage-ink)",
  amber: "var(--amber)",
  danger: "var(--danger)",
};

/** Every numeric per-criterion score on a record, minus the whole-record
    summary keys, humanized for display. Reads whatever keys exist rather
    than assuming one fixed rubric shape — a blueprint eval and a clone eval
    render through the exact same function. */
export function criterionEntries(e: Pick<EvalRecord, "scores">): [string, number][] {
  return Object.entries(e.scores ?? {})
    .filter(
      (entry): entry is [string, number] =>
        !META.has(entry[0]) && typeof entry[1] === "number" && Number.isFinite(entry[1]),
    )
    .map(([k, v]) => [k.replace(/_/g, " "), v]);
}

/** The ONE record→score rule for the whole app. `overall`/`score_0_100` win
    when present; otherwise fall back to the mean of the per-criterion scores
    so a record that only ever carried a rubric (the clone eval, today) still
    surfaces a number instead of vanishing. */
export function recordScore(e: EvalRecord): number | null {
  const s = e.scores?.overall ?? e.scores?.score_0_100;
  if (s != null) return Number(s);
  const entries = criterionEntries(e);
  if (!entries.length) return null;
  return entries.reduce((sum, [, v]) => sum + v, 0) / entries.length;
}

/** mid band reads amber, not brass — brass stays reserved for
    attention/awaiting-decision/index elsewhere in the app (constraint d). */
export function scoreTone(n: number): ScoreTone {
  if (n >= 85) return "sage";
  if (n >= 60) return "amber";
  return "danger";
}

export function scoreColor(n: number): string {
  return TONE_VAR[scoreTone(n)];
}

/** Slice identity: rubrics (and therefore what "criteria" even means) differ
    per target_type, so every slice-aware view groups on agent × target_type,
    not agent alone. */
export function seriesKey(e: EvalRecord): string {
  return `${e.agent} · ${e.target_type}`;
}

export interface SeriesPoint {
  t: number; // ms, numeric — never a day-string
  v: number;
  rec: EvalRecord;
}

export type Trend = "up" | "down" | "flat";
export const TREND_GLYPH: Record<Trend, string> = { up: "▲", down: "▼", flat: "–" };

export interface SeriesInfo {
  key: string;
  points: SeriesPoint[];
  mean: number;
  trend: Trend;
  n: number; // total records in the slice, scored or not
  judge: string | null;
  accepts: number;
  total: number;
}

/** One time-ordered, independently-plotted series per agent×target_type — no
    shared-row pivot, so a gap in one series never masks/collides with
    another (the fix for the day-string collide bug). Refuses to call a
    trend on fewer than 3 points — dots-without-slope is the honest read. */
export function bySeries(rows: EvalRecord[]): Map<string, SeriesInfo> {
  const groups = new Map<string, EvalRecord[]>();
  for (const r of rows) {
    const k = seriesKey(r);
    const arr = groups.get(k);
    if (arr) arr.push(r);
    else groups.set(k, [r]);
  }
  const out = new Map<string, SeriesInfo>();
  for (const [key, recs] of groups) {
    const sorted = [...recs].sort((a, b) => a.ts - b.ts);
    const points: SeriesPoint[] = [];
    for (const rec of sorted) {
      const v = recordScore(rec);
      if (v != null) points.push({ t: rec.ts * 1000, v, rec });
    }
    const mean = points.length ? points.reduce((s, p) => s + p.v, 0) / points.length : 0;
    let trend: Trend = "flat";
    if (points.length >= 3) {
      const mid = Math.floor(points.length / 2);
      const firstMean = mean1(points.slice(0, mid));
      const lastMean = mean1(points.slice(points.length - mid));
      const diff = lastMean - firstMean;
      trend = diff > 0 ? "up" : diff < 0 ? "down" : "flat";
    }
    const accepts = recs.filter((r) => (r.verdict ?? "").toLowerCase() === "accept").length;
    out.set(key, {
      key,
      points,
      mean,
      trend,
      n: recs.length,
      judge: recs[recs.length - 1]?.judge ?? null,
      accepts,
      total: recs.length,
    });
  }
  return out;
}
function mean1(pts: SeriesPoint[]): number {
  return pts.length ? pts.reduce((s, p) => s + p.v, 0) / pts.length : 0;
}

export interface CriterionStat {
  mean: number;
  min: number;
  n: number;
  worstRecord: EvalRecord;
}

/** Per-criterion means across whatever rubric keys are present in the slice,
    sorted worst-first ("what's dragging us down" sits on top). Only
    meaningful within one rubric — callers scope `rows` to one slice and
    surface a note if target_types are mixed. */
export function criterionMeans(rows: EvalRecord[]): Map<string, CriterionStat> {
  const acc = new Map<string, { sum: number; min: number; n: number; worst: EvalRecord }>();
  for (const r of rows) {
    for (const [label, v] of criterionEntries(r)) {
      const cur = acc.get(label);
      if (!cur) {
        acc.set(label, { sum: v, min: v, n: 1, worst: r });
      } else {
        cur.sum += v;
        cur.n += 1;
        if (v < cur.min) {
          cur.min = v;
          cur.worst = r;
        }
      }
    }
  }
  const entries = Array.from(acc.entries()).map(
    ([label, s]) =>
      [label, { mean: s.sum / s.n, min: s.min, n: s.n, worstRecord: s.worst }] as const,
  );
  entries.sort((a, b) => a[1].mean - b[1].mean);
  return new Map(entries);
}

/** Records below `threshold`, worst first (score asc, oldest first on ties)
    — the Fix Queue's own ordering. Records with no computable score are
    excluded (never silently dropped elsewhere — callers surface them as an
    "unscored" footnote instead). */
export function fixQueue(rows: EvalRecord[], threshold: number): EvalRecord[] {
  return rows
    .map((r) => ({ r, s: recordScore(r) }))
    .filter((x): x is { r: EvalRecord; s: number } => x.s != null && x.s < threshold)
    .sort((a, b) => a.s - b.s || a.r.ts - b.r.ts)
    .map((x) => x.r);
}

export interface Facets {
  agents: string[];
  targetTypes: string[];
}

/** Facet values for the filter pickers — call this over an UNFILTERED row
    set so choosing a filter never makes the other pickers collapse to a
    single remaining option. */
export function facets(rows: EvalRecord[]): Facets {
  return {
    agents: Array.from(new Set(rows.map((r) => r.agent))).sort(),
    targetTypes: Array.from(new Set(rows.map((r) => r.target_type))).sort(),
  };
}

/** 10 fixed-width score buckets — kept for a future distribution surface;
    not rendered as its own chart today (no layout section claims one at
    N=5 live records; see EvalsView deviations note). */
export function distribution(rows: EvalRecord[]): { bucket: string; lo: number; count: number }[] {
  const buckets = Array.from({ length: 10 }, (_, i) => ({ lo: i * 10, count: 0 }));
  for (const r of rows) {
    const s = recordScore(r);
    if (s == null) continue;
    const idx = Math.min(9, Math.max(0, Math.floor(s / 10)));
    buckets[idx].count += 1;
  }
  return buckets.map((b) => ({ bucket: `${b.lo}-${b.lo + 10}`, lo: b.lo, count: b.count }));
}
