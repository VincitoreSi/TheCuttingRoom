import type { Job, Jobs, LogEvent } from "./types";

/** What one live render reports about itself: "frame 3 of 6". Shaped exactly
    like the `progress` prop RenderSwatch / RenderModal / ProgressBar already take. */
export interface FrameProgress {
  frame: number;
  total: number;
}

/** The producer's per-frame lifecycle event (SimilarContent/engine/render.py):
    data = { file, frame, of, stage } on the SSE `log` channel. */
const PROGRESS_EVENT = "item.progress";

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Fold the live agent-log tail into `studio file -> { frame, total }`.
 *
 * Pure on purpose: the SSE plumbing lives in useLogStream, so everything that
 * can actually be wrong — ordering, malformed payloads, stale entries — is
 * decided here and unit-tested.
 *
 * Three rules, and the reasons for them:
 *
 * 1. ONLY FILES WITH A LIVE JOB. An entry exists only while
 *    `${platform}:render:${file}` is queued/running. The ring buffer keeps
 *    events long after a render settles, so without this gate a finished item
 *    would advertise "frame 6/6" forever; with it, the card falls back to its
 *    normal rendered/failed state the moment the job does.
 *
 * 2. NEWEST `ts` WINS, ties broken by the higher frame. `ts` is stamped by the
 *    hub when the event is accepted, so it is the true order even when the SSE
 *    batch delivers frames out of sequence. Highest-frame-wins would also fix
 *    ordering, but it sticks: a re-render restarts at frame 1 and would be
 *    ignored in favour of the previous pass's 6. The frame tie-break only
 *    matters for events sharing a timestamp, where higher = later.
 *
 * 3. NOTHING FROM BEFORE THE JOB STARTED. Both `ts` and `job.started` are epoch
 *    seconds from the same host clock, so a re-render's fresh job cleanly
 *    discards the previous pass's events still sitting in the buffer. If the
 *    clocks ever disagreed the entry is simply dropped, and the card shows the
 *    indeterminate shimmer — the safe direction to fail.
 *
 * Malformed events (no `data`, non-numeric `frame`/`of`, `of` of 0 or less) are
 * skipped rather than surfaced, so nothing downstream can divide by zero.
 */
export function renderProgress(
  events: readonly LogEvent[] | undefined,
  jobs: Jobs | undefined,
  platform: string,
): Map<string, FrameProgress> {
  const out = new Map<string, FrameProgress>();
  if (!platform) return out;

  // rule 1 — the set of files currently rendering, with each job's start time
  const prefix = `${platform}:render:`;
  const live = new Map<string, number>();
  for (const [key, job] of Object.entries(jobs ?? {})) {
    const j = job as Job | undefined;
    if (!j || !key.startsWith(prefix)) continue;
    if (j.platform !== platform) continue;
    if (j.status !== "queued" && j.status !== "running") continue;
    const file = key.slice(prefix.length);
    // several jobs can exist for one file across re-renders; the latest start wins
    const started = finite(j.started) ?? 0;
    if (file && started >= (live.get(file) ?? -Infinity)) live.set(file, started);
  }
  if (live.size === 0) return out;

  const seen = new Map<string, { ts: number; frame: number }>();
  for (const ev of events ?? []) {
    if (!ev || ev.event !== PROGRESS_EVENT) continue;
    // an off-platform run of the same producer must not paint this platform's cards
    if (ev.platform && ev.platform !== platform) continue;

    const data = ev.data;
    if (!data || typeof data !== "object") continue;
    const file = typeof data.file === "string" ? data.file : "";
    const started = file ? live.get(file) : undefined;
    if (started === undefined) continue; // unknown file, or its job is not running

    const frame = finite(data.frame);
    const total = finite(data.of);
    if (frame === null || total === null) continue;
    if (total <= 0 || frame < 0) continue; // rule out divide-by-zero and nonsense

    const ts = finite(ev.ts) ?? 0;
    if (ts < started) continue; // rule 3 — a previous pass's leftovers

    const best = seen.get(file);
    if (best && (ts < best.ts || (ts === best.ts && frame <= best.frame))) continue; // rule 2
    seen.set(file, { ts, frame });
    out.set(file, { frame, total });
  }
  return out;
}
