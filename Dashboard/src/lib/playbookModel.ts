// The Playbook data brain (pure, no React). Modeled 1:1 on evalModel.ts:
// pure functions over the corpus FactorsResponse + shared Insight feed, with
// every ranking / confidence / formula decision living here so the view never
// recomputes tone or rank inline.
//
// Grounding facts that shape these selectors:
//  (a) `mean_score` and `baseline` share one 0–100 virality scale;
//      `lift = mean_score − baseline`. A factor's confidence is its sample
//      size `n` — the number the old view collected but never surfaced.
//  (b) `n` spans 15…519 in the live corpus; the SOLID/THIN cutoffs below are
//      grounded in that range, not invented.
//  (c) `Insight.ts` is unix SECONDS with a fractional part (same caveat as
//      evalModel) — kept numeric, compared in seconds, never day-bucketed.
//  (d) `/api/insights` is NOT platform-scoped; the platform filter is done
//      here client-side (`=== current || "shared"`), never faked server-side.
import type { Factor, FactorsResponse, Insight } from "./types";

export type Confidence = "solid" | "thin" | "noise";

/** Sample-size confidence cutoffs. Grounded: real n spans 15…519, so 80 sits
    near the middle (a solid read) and 25 is the floor below which a bucket
    (e.g. the n=15 "late 21-24" drag) is noise, not evidence. */
export const SOLID_N = 80;
export const THIN_N = 25;

/** The fixed feature order the formula + rings read in — one bucket per
    feature, always in this narrative order. */
export const FEATURE_ORDER = ["duration", "posting_time", "caption_length", "hashtags"];

export function confidence(n: number): Confidence {
  if (n >= SOLID_N) return "solid";
  if (n >= THIN_N) return "thin";
  return "noise";
}

/** True only for a factor we can actually rank: a positive sample and a
    non-empty bucket string. A factor with no computable bucket is excluded,
    never guessed at. */
function rankable(f: Factor): boolean {
  return f.n > 0 && typeof f.bucket === "string" && f.bucket.trim().length > 0;
}

/**
 * Rank a factor list for one column. `dir:"up"` = winners (highest lift
 * first); `dir:"down"` = drags (most-negative lift first). Drops n<=0 (and
 * bucket-less) rows, orders confident rows ahead of noise rows, then by lift
 * in the requested direction, tie-breaking on n desc (bigger sample wins a
 * tie). Pure — returns a new array.
 */
export function rankFactors(list: Factor[], dir: "up" | "down"): Factor[] {
  const sign = dir === "up" ? 1 : -1;
  return list.filter(rankable).sort((a, b) => {
    const noiseA = confidence(a.n) === "noise" ? 1 : 0;
    const noiseB = confidence(b.n) === "noise" ? 1 : 0;
    if (noiseA !== noiseB) return noiseA - noiseB; // confident first
    const byLift = (b.lift - a.lift) * sign; // up: desc, down: asc
    if (byLift !== 0) return byLift;
    return b.n - a.n; // tie-break: larger sample first
  });
}

/** Split a ranked column into the confident rows shown in the main list and
    the noise-grade rows folded into a footnote — surfaced, never dropped. */
function split(list: Factor[], dir: "up" | "down"): { rows: Factor[]; noise: Factor[] } {
  const ranked = rankFactors(list, dir);
  return {
    rows: ranked.filter((f) => confidence(f.n) !== "noise"),
    noise: ranked.filter((f) => confidence(f.n) === "noise"),
  };
}

/** Winners = factors that lift virality above baseline (f.winners). */
export function winners(f?: FactorsResponse): { rows: Factor[]; noise: Factor[] } {
  if (!f) return { rows: [], noise: [] };
  return split(f.winners, "up");
}

/** Drags = factors that pull below baseline (f.losers — the column the old
    view dropped entirely). */
export function drags(f?: FactorsResponse): { rows: Factor[]; noise: Factor[] } {
  if (!f) return { rows: [], noise: [] };
  return split(f.losers, "down");
}

export interface LadderRow {
  label: string;
  feature: string;
  bucket: string;
  lift: number;
  mean_score: number;
  n: number;
  conf: Confidence;
}

/** Every scored bucket across all features on one diverging axis — the honest
    overview that consumes the `all[]` array the old view discarded. Noise
    excluded (a 2-bar chart of noise is misleading), sorted by lift desc. */
export function ladder(f?: FactorsResponse): LadderRow[] {
  if (!f) return [];
  return f.all
    .filter((x) => rankable(x) && confidence(x.n) !== "noise")
    .map((x) => ({
      label: `${x.feature} · ${x.bucket}`,
      feature: x.feature,
      bucket: x.bucket,
      lift: x.lift,
      mean_score: x.mean_score,
      n: x.n,
      conf: confidence(x.n),
    }))
    .sort((a, b) => b.lift - a.lift);
}

export interface FeatureBest {
  feature: string;
  bucket: string;
  mean_score: number;
  lift: number;
  conf: Confidence;
}

/** The single highest-lift *confident* winner bucket per feature, in fixed
    feature order. A feature with no confident winner is omitted (never
    guessed) — so the ring row and the formula only ever show earned reads. */
export function featureBest(f?: FactorsResponse): FeatureBest[] {
  if (!f) return [];
  const out: FeatureBest[] = [];
  for (const feat of FEATURE_ORDER) {
    const cands = f.winners.filter(
      (x) => x.feature === feat && rankable(x) && confidence(x.n) !== "noise",
    );
    if (!cands.length) continue;
    const best = cands.reduce((a, b) => (b.lift > a.lift ? b : a));
    out.push({
      feature: best.feature,
      bucket: best.bucket,
      mean_score: best.mean_score,
      lift: best.lift,
      conf: confidence(best.n),
    });
  }
  return out;
}

/** The composed formula string — confident featureBest buckets joined. Returns
    "" (which the caller suppresses) rather than shipping a one-word "formula"
    when fewer than 2 features have a confident winner. */
export function formula(f?: FactorsResponse): string {
  const best = featureBest(f);
  if (best.length < 2) return "";
  return best.map((b) => b.bucket).join(" · ");
}

/** Worst→best lift across the field, for the honestly-labeled header ruler.
    Empty / unbuilt corpus reads as a neutral {0,0}. */
export function fieldExtent(f?: FactorsResponse): { min: number; max: number } {
  const lifts = (f?.all ?? []).filter(rankable).map((x) => x.lift);
  if (!lifts.length) return { min: 0, max: 0 };
  return { min: Math.min(...lifts), max: Math.max(...lifts) };
}

export interface PartitionedInsights {
  methods: Insight[];
  findings: Insight[];
  antipatterns: Insight[];
}

function isAntipattern(i: Insight): boolean {
  const k = (i.kind ?? "").toLowerCase();
  return k === "negative" || k === "antipattern" || (i.tags ?? []).includes("antipattern");
}

/** ts desc; a missing / non-finite ts sorts last (treated as -Infinity so it
    never jumps to the top of a recency-ordered band). */
function byTsDesc(a: Insight, b: Insight): number {
  const ta = Number.isFinite(a.ts) ? a.ts : -Infinity;
  const tb = Number.isFinite(b.ts) ? b.ts : -Infinity;
  return tb - ta;
}

/**
 * Filter the flat insight feed to the current platform (`=== platform ||
 * "shared"` — the client-side scope, since /api/insights has no platform
 * param) and classify into three bands: methods (core methodology),
 * antipatterns (do-not-repeat), findings (everything else). Each band sorted
 * ts desc.
 */
export function partitionInsights(
  ins: Insight[] | undefined,
  platform: string,
): PartitionedInsights {
  const scoped = (ins ?? []).filter((i) => i.platform === platform || i.platform === "shared");
  const methods: Insight[] = [];
  const findings: Insight[] = [];
  const antipatterns: Insight[] = [];
  for (const i of scoped) {
    if (isAntipattern(i)) antipatterns.push(i);
    else if ((i.kind ?? "").toLowerCase() === "method") methods.push(i);
    else findings.push(i);
  }
  methods.sort(byTsDesc);
  findings.sort(byTsDesc);
  antipatterns.sort(byTsDesc);
  return { methods, findings, antipatterns };
}

/** "3h ago" from two unix-SECOND timestamps (ts carries a fractional part).
    Future / same-instant reads as "just now"; scales m → h → d → w. */
export function relativeTime(ts: number, now: number): string {
  if (!Number.isFinite(ts)) return "";
  const diff = now - ts;
  if (diff < 60) return "just now";
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(diff / 3600);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(diff / 86400);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(diff / 604800);
  return `${w}w ago`;
}
