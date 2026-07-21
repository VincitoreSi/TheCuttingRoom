// lib/activityModel.ts — the ONE shared log-stream data brain (pure, React-free).
//
// This module folds the flat agent `log` SSE stream into per-run seams and the
// derived facts both PipelineBoard (band A) and the Activity view consume. It is
// the union of Activity's grouping foundation and Board's Board-specific
// selectors — built once so neither view re-implements event parsing.
//
// Three facts drive everything here (all documented inline where they bite):
//   (a) Event names DRIFT per agent — analysis-engine emits dotted
//       `run.start / item.done`, similar-content emits underscored
//       `run_start / clone_done`, template-selftest emits `selftest`. The drift
//       is normalized in exactly ONE place: `verbOf`. No other function parses
//       raw `event` strings.
//   (b) `LogEvent.ts` is unix SECONDS with a fractional part. Every function
//       takes `nowSec` (NOT nowMs) and compares in seconds. Kept numeric; never
//       bucketed to a day-string. Components pass `useNow()/1000`.
//   (c) `data.stage` ∈ the workflow_stages vocabulary, already tone-mapped by
//       `statusTone("agent-item", …)`. Counts by `content_id` use the SAME
//       DONE_STAGES/FAIL_STAGES sets as `agentBoard.ts` so the floor and the
//       Agent Desk can never disagree.
//
// Platform filtering is the CALLER's job for the platform-agnostic primitives
// (`groupByRun` / `streamState` are the global floor). The Board-specific
// selectors (`activitySummary` / `liveStageIndex` / `packetCount`) take a
// `platform` and filter internally, matching `useAgentBoard`'s reducer.

import type { LogEvent, Jobs, Stage } from "./types";
import type { SeamState } from "../components/Seam";
import { agentState, latestStageJob } from "./jobs";

// Mirror agentBoard.ts's terminal-stage sets EXACTLY (agentBoard.ts:59–60), so
// per-`content_id` done/failed counts here match the Agent Desk board 1:1.
const DONE_STAGES = new Set(["Done", "Proposed", "Approved"]);
const FAIL_STAGES = new Set(["Failed", "Rejected"]);

// ---------------------------------------------------------------------------
// Liveness windows — NAMED exports, intentionally DISTINCT scales (not a bug).
// They mean different things and are measured against different clocks:
//   LIVE_STAGE_WINDOW — Board: is *this node's stage* live right now → drives
//                       liveStageIndex / the traveling packet. Tightest window.
//   ACTIVE_WINDOW     — Board: run/agent "active" tiles in band A.
//   RUN_STALE_SEC     — Activity: does a run's *Seam* still animate (isLive).
//                       Widest — a run can be "working" but quiet a while.
// All in SECONDS.
// ---------------------------------------------------------------------------
export const LIVE_STAGE_WINDOW = 8;
export const ACTIVE_WINDOW = 20;
export const RUN_STALE_SEC = 45;

const SYNTHETIC_RUN = "∅";

export type Verb =
  "run.start" | "run.end" | "item.start" | "item.stage" | "item.done" | "item.error" | "other";

/**
 * Canonicalize the per-agent event-name drift into one vocabulary. This is the
 * SINGLE normalizer for the whole surface — Board's packetCount/liveStageIndex
 * and Activity's grouping all route through it, never their own regex.
 * (Exact code carried from activity.md §2.) `selftest`, run-less pings and any
 * unknown name collapse to `"other"`.
 */
export function verbOf(ev: LogEvent): Verb {
  const e = (ev.event ?? "").toLowerCase().replace(/_/g, ".");
  if (e === "run.start") return "run.start";
  if (e === "run.end") return "run.end";
  if (e === "item.start") return "item.start";
  if (e === "item.stage") return "item.stage";
  if (e === "item.done" || e === "clone.done") return "item.done";
  if (e === "item.error") return "item.error";
  return "other"; // selftest, run-less pings, unknown
}

export interface RunGroup {
  key: string; // `${agent}:${runId}` — stable react key
  runId: string; // real run_id, or synthetic `∅` for run-less events
  agent: string;
  platform: string | null;
  started: number; // earliest ts (sec)
  lastTs: number; // newest ts (sec) — drives liveness & sort
  ended: number | null; // ts of run.end, else null
  stage: string | null; // freshest data.stage seen (for tone)
  state: SeamState; // idle | working | done | error
  worstLevel: string; // info < warn < error rollup
  total: number; // distinct content_id
  done: number; // content_id in DONE_STAGES
  failed: number; // content_id in FAIL_STAGES
  events: LogEvent[]; // this run's events, oldest→newest
}

function levelRank(level: string | undefined): number {
  const s = (level ?? "").toLowerCase();
  if (s === "error" || s === "critical") return 3;
  if (s === "warn" || s === "warning") return 2;
  if (s === "info") return 1;
  return 0;
}

/**
 * Group the ring into runs. Run-less events (selftest, run-less pings, etc.)
 * are NEVER dropped — they bucket under a synthetic per-agent run `${agent}:∅`
 * so they still thread. Per-`content_id` counts mirror agentBoard.ts's reducer:
 * an item's final stage is derived from its item.* events, then counted against
 * the SAME DONE_STAGES/FAIL_STAGES sets. Lifecycle-derived seam state:
 *   error wins (any item.error OR an error/critical-level event);
 *   else run.end seen OR all items terminal → done;
 *   else events present → working;  else → idle.
 * Returned newest-run-first (lastTs desc).
 */
export function groupByRun(events: LogEvent[]): RunGroup[] {
  const buckets = new Map<string, LogEvent[]>();
  for (const ev of events) {
    const agent = ev.agent ?? "";
    const runId = ev.run_id ?? SYNTHETIC_RUN;
    const key = `${agent}:${runId}`;
    const arr = buckets.get(key);
    if (arr) arr.push(ev);
    else buckets.set(key, [ev]);
  }

  const groups: RunGroup[] = [];
  for (const [key, raw] of buckets) {
    // oldest→newest so item-stage transitions apply in order (ts fractional secs)
    const evs = raw.slice().sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
    const first = evs[0];
    const agent = first.agent ?? "";
    const runId = first.run_id ?? SYNTHETIC_RUN;

    let started = Infinity;
    let lastTs = -Infinity;
    let ended: number | null = null;
    let stage: string | null = null;
    let worstRank = 0;
    let worstLevel = "info";
    let hasErrorEvent = false;

    // per content_id final stage — mirrors agentBoard.applyLogEvent exactly
    // (minus the agent-`kind` branch, whose two defaults "Done"/"Proposed" are
    //  BOTH in DONE_STAGES, so the count is identical either way).
    const itemStage = new Map<string, string>();

    for (const ev of evs) {
      const ts = ev.ts ?? 0;
      if (ts < started) started = ts;
      if (ts > lastTs) lastTs = ts;
      const r = levelRank(ev.level);
      if (r > worstRank) {
        worstRank = r;
        worstLevel = ev.level ?? worstLevel;
      }
      const data = ev.data ?? {};
      if (data.stage != null) stage = String(data.stage);

      const verb = verbOf(ev);
      if (verb === "run.end") ended = ts;
      if (verb === "item.error") hasErrorEvent = true;

      const cid = ev.content_id;
      if (
        cid &&
        (verb === "item.start" ||
          verb === "item.stage" ||
          verb === "item.done" ||
          verb === "item.error")
      ) {
        let next = itemStage.get(cid) ?? "Queued";
        if (verb === "item.error") next = "Failed";
        else if (verb === "item.done") next = (data.stage as string) ?? "Done";
        else next = (data.stage as string) ?? next; // item.start / item.stage
        itemStage.set(cid, next);
      }
    }

    let done = 0;
    let failed = 0;
    for (const st of itemStage.values()) {
      if (DONE_STAGES.has(st)) done++;
      else if (FAIL_STAGES.has(st)) failed++;
    }
    const total = itemStage.size;

    const hasError = worstRank >= 3 || hasErrorEvent;
    const allTerminal = total > 0 && done + failed === total;
    let state: SeamState;
    if (hasError) state = "error";
    else if (ended !== null || allTerminal) state = "done";
    else if (evs.length > 0) state = "working";
    else state = "idle";

    groups.push({
      key,
      runId,
      agent,
      platform: first.platform ?? null,
      started: started === Infinity ? 0 : started,
      lastTs: lastTs === -Infinity ? 0 : lastTs,
      ended,
      stage,
      state,
      worstLevel,
      total,
      done,
      failed,
      events: evs,
    });
  }

  groups.sort((a, b) => b.lastTs - a.lastTs);
  return groups;
}

/**
 * Is this run's Seam animated? working AND fresh. Stale working runs (no
 * run.end, gone quiet) keep `state === "working"` but return false here, so
 * they render with a STILL seam — no forever-animation of a dead run. The
 * caller additionally ANDs `!reduced && visible`. Seconds throughout.
 */
export function isLive(g: RunGroup, nowSec: number, staleSec = RUN_STALE_SEC): boolean {
  return g.state === "working" && nowSec - g.lastTs < staleSec;
}

/**
 * Whole-stream seam + label for the live indicator (S5): error > working >
 * done > idle. This is the feed for the Header center-slot, Board band A, and
 * Activity §A header — one selector, three surfaces. Platform-agnostic; the
 * caller filters upstream if it wants a per-platform view.
 */
export function streamState(
  groups: RunGroup[],
  nowSec: number,
): { state: SeamState; label: string } {
  if (groups.length === 0) return { state: "idle", label: "Idle" };
  const byRecent = groups.slice().sort((a, b) => b.lastTs - a.lastTs);

  const errored = byRecent.find((g) => g.state === "error" && nowSec - g.lastTs < RUN_STALE_SEC);
  if (errored) return { state: "error", label: `Snapped · ${errored.agent}` };

  const live = byRecent.find((g) => isLive(g, nowSec));
  if (live) return { state: "working", label: `Sewing · ${live.agent}` };

  const done = byRecent.find((g) => g.state === "done");
  if (done) return { state: "done", label: "Knotted" };

  return { state: "idle", label: "Idle" };
}

/**
 * Facet pickers — computed over the UNFILTERED ring so choosing one filter
 * never collapses the others. `runs` come from `groupByRun` so run-less events
 * surface under their synthetic `${agent}:∅` key too.
 */
export function activityFacets(events: LogEvent[]): {
  agents: string[];
  levels: string[];
  runs: { key: string; runId: string; agent: string }[];
} {
  const agents = new Set<string>();
  const levels = new Set<string>();
  for (const ev of events) {
    if (ev.agent) agents.add(ev.agent);
    if (ev.level) levels.add(ev.level);
  }
  const runs = groupByRun(events).map((g) => ({
    key: g.key,
    runId: g.runId,
    agent: g.agent,
  }));
  return {
    agents: [...agents].sort(),
    levels: [...levels].sort(),
    runs,
  };
}

export interface ThroughputBin {
  t: number; // right-edge timestamp of the bin (sec)
  n: number; // events landing in the bin
}

/**
 * Fixed-length, zero-filled throughput — ALWAYS a valid sparkline shape (a flat
 * series when quiet, never an empty SVG). Returns exactly `windowSec / binSec`
 * bins; the rightmost bin's `t` === nowSec (right edge). Records with no usable
 * `ts` (null/undefined/NaN) are skipped, never crash. SECONDS throughout.
 * Activity calls it (300, 15) → 20 bins; Board calls it (60, 2) → 30 bins.
 */
export function throughputBins(
  events: LogEvent[],
  nowSec: number,
  windowSec = 300,
  binSec = 15,
): ThroughputBin[] {
  const bins = Math.max(1, Math.round(windowSec / binSec));
  const out: ThroughputBin[] = new Array(bins);
  for (let i = 0; i < bins; i++) {
    // bin i's right edge; last bin (i === bins-1) lands exactly on nowSec.
    out[i] = { t: nowSec - (bins - 1 - i) * binSec, n: 0 };
  }
  for (const ev of events) {
    const ts = ev.ts;
    if (ts == null || !Number.isFinite(ts)) continue; // no usable ts → skip
    const age = nowSec - ts;
    if (age < 0 || age >= windowSec) continue; // outside the window
    const fromEnd = Math.floor(age / binSec); // 0 = most-recent bin
    const idx = bins - 1 - fromEnd;
    if (idx >= 0 && idx < bins) out[idx].n++;
  }
  return out;
}

export interface FloorSummary {
  active: number; // live (working & fresh) runs
  done: number; // settled-clean runs
  errored: number; // snapped runs
  agentsLive: number; // distinct agents among live runs
  perMin: number; // events in the trailing 60s (per-minute rate)
  lastTs: number | null; // newest event ts, or null
}

/** Aggregate floor stats for the Activity pulse ribbon (§B). */
export function floorSummary(
  groups: RunGroup[],
  events: LogEvent[],
  nowSec: number,
  staleSec = RUN_STALE_SEC,
): FloorSummary {
  let active = 0;
  let done = 0;
  let errored = 0;
  const liveAgents = new Set<string>();
  for (const g of groups) {
    if (isLive(g, nowSec, staleSec)) {
      active++;
      liveAgents.add(g.agent);
    } else if (g.state === "done") done++;
    else if (g.state === "error") errored++;
  }
  let perMin = 0;
  let lastTs: number | null = null;
  for (const ev of events) {
    const ts = ev.ts;
    if (ts == null || !Number.isFinite(ts)) continue;
    if (nowSec - ts < 60) perMin++;
    if (lastTs === null || ts > lastTs) lastTs = ts;
  }
  return { active, done, errored, agentsLive: liveAgents.size, perMin, lastTs };
}

// ===========================================================================
// Board-specific selectors — layered on the primitives above, same file.
// These take a `platform` and filter internally (the platform-scoped view).
// ===========================================================================

/** Keep events on this platform, `"shared"`, or platform-less — matches
 * useAgentBoard's reducer (hooks.ts:305). */
function onPlatform(ev: LogEvent, platform: string): boolean {
  return ev.platform === platform || ev.platform === "shared" || ev.platform == null;
}

export interface ActivitySummary {
  seamState: SeamState; // idle | working | done | error
  label: string; // "Sewing · analyze" | "Knotted · media" | "Idle · ready" | "Offline"
  runsActive: number; // distinct run_id with an event in the last ACTIVE_WINDOW s
  agentsActive: number; // distinct agent   ""
  lastEventAgo: number; // seconds since newest event (Infinity if none)
  packetCount: number | null; // in-flight content count for the live run (null if none)
  liveRunId: string | null;
}

/**
 * Band-A summary for the Board. Wraps `streamState` (the log axis) and ORs-in
 * the Job axis via `agentState` (so log-only agents AND jobs both register),
 * rather than recomputing precedence. Precedence: !connected → "Offline";
 * else working (either axis) > error > done > idle. Adds the run/agent-active
 * tiles, last-event recency, and the live run's in-flight packet count.
 */
export function activitySummary(
  events: LogEvent[],
  jobs: Jobs,
  platform: string,
  nowSec: number,
  connected: boolean,
): ActivitySummary {
  const scoped = events.filter((ev) => onPlatform(ev, platform));
  const groups = groupByRun(scoped);
  const log = streamState(groups, nowSec); // log axis
  const job = agentState(jobs, platform); // Job axis (reused, not recomputed)

  let seamState: SeamState;
  let label: string;
  if (!connected) {
    seamState = "idle";
    label = "Offline";
  } else if (log.state === "working" || job.state === "working") {
    seamState = "working";
    label = job.state === "working" ? job.label : log.label;
  } else if (log.state === "error" || job.state === "error") {
    seamState = "error";
    label = job.state === "error" ? job.label : log.label;
  } else if (log.state === "done" || job.state === "done") {
    seamState = "done";
    label = job.state === "done" ? job.label : log.label;
  } else {
    seamState = "idle";
    label = scoped.length === 0 ? "Idle · ready" : "Idle";
  }

  const activeRuns = new Set<string>();
  const activeAgents = new Set<string>();
  let lastTs = -Infinity;
  for (const ev of scoped) {
    const ts = ev.ts ?? 0;
    if (ts > lastTs) lastTs = ts;
    if (nowSec - ts < ACTIVE_WINDOW) {
      if (ev.run_id) activeRuns.add(ev.run_id);
      if (ev.agent) activeAgents.add(ev.agent);
    }
  }

  // the live run = freshest working-and-fresh group with a real run_id
  let liveRunId: string | null = null;
  for (const g of groups) {
    if (isLive(g, nowSec) && g.runId !== SYNTHETIC_RUN) {
      liveRunId = g.runId;
      break; // groups are newest-first
    }
  }

  return {
    seamState,
    label,
    runsActive: activeRuns.size,
    agentsActive: activeAgents.size,
    lastEventAgo: lastTs === -Infinity ? Infinity : Math.max(0, nowSec - lastTs),
    packetCount: packetCount(events, platform, liveRunId),
    liveRunId,
  };
}

/**
 * First node index that is live EITHER by Job (`latestStageJob` running/queued
 * for the node's stage) OR by a fresh log event (an in-flight item.* / run.start
 * event within LIVE_STAGE_WINDOW that matches this node's agent). The log axis
 * closes the gap where a log-only agent (no Job) produced no Board motion.
 * `nodes` is kept generic so PipelineBoard passes its NODES array directly
 * without this module importing PipelineBoard. Returns -1 when nothing is live.
 */
export function liveStageIndex(
  nodes: { key: string; stage?: string | null; agent?: string }[],
  jobs: Jobs,
  events: LogEvent[],
  platform: string,
  nowSec: number,
): number {
  const fresh = events.filter(
    (ev) => onPlatform(ev, platform) && nowSec - (ev.ts ?? 0) < LIVE_STAGE_WINDOW,
  );
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    // Job axis
    if (node.stage) {
      const job = latestStageJob(jobs, platform, node.stage as Stage);
      if (job && (job.status === "running" || job.status === "queued")) return i;
    }
    // Log axis — an in-flight event for this node's agent (or exact stage match)
    const liveByLog = fresh.some((ev) => {
      const v = verbOf(ev);
      const inFlight = v === "item.start" || v === "item.stage" || v === "run.start";
      if (!inFlight) return false;
      if (node.agent && ev.agent === node.agent) return true;
      if (node.stage && ev.data?.stage != null && String(ev.data.stage) === node.stage) return true;
      return false;
    });
    if (liveByLog) return i;
  }
  return -1;
}

/**
 * In-flight content count for the live run: distinct `content_id` seen in an
 * item.start/item.stage event but with no LATER item.done in the same run_id
 * (i.e. currently sewing) — via `verbOf`, never a private regex. Events lacking
 * a `content_id` are ignored. If 0 are in flight but the run is live, fall back
 * to that run's `run.start` `data.limit`; if there is nothing to show, `null`.
 */
export function packetCount(
  events: LogEvent[],
  platform: string,
  liveRunId: string | null,
): number | null {
  if (!liveRunId) return null;
  const runEvents = events
    .filter((ev) => onPlatform(ev, platform) && ev.run_id === liveRunId)
    .sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
  if (runEvents.length === 0) return null;

  const inflight = new Set<string>();
  for (const ev of runEvents) {
    const cid = ev.content_id;
    const v = verbOf(ev);
    if (v === "item.done") {
      if (cid) inflight.delete(cid);
    } else if (v === "item.start" || v === "item.stage") {
      if (cid) inflight.add(cid);
    }
  }
  if (inflight.size > 0) return inflight.size;

  // 0 in flight but the run is live → advertise the run's declared limit.
  const start = runEvents.find((ev) => verbOf(ev) === "run.start");
  const limit = start?.data?.limit;
  if (typeof limit === "number") return limit;
  return null;
}
