import { latestStageJob } from "./jobs";
import type { Job, Jobs, LogEvent } from "./types";

/** What a live scrape reports about itself: "4 of 12 creators". */
export interface ScrapeProgress {
  done: number;
  total: number;
}

/** The scraper's heartbeat (ReelScraper/core/hubevents.py + platforms/<p>/scrape.py).
 *
 *  MATCHED AS A LITERAL, deliberately, exactly as renderProgress does. It is tempting to
 *  route every event name through `verbOf` since that is the single normalizer for the six
 *  board verbs — but `item.progress` is not one of the six and `verbOf` collapses it to
 *  "other", indistinguishable from selftest, run.error, job_failed and job_stopped. Being
 *  outside the six is the whole reason the heartbeat is safe: the server's board reducer
 *  ignores it, so it can never rewrite lane state or item counts however chatty it gets. */
const PROGRESS_EVENT = "item.progress";
const START_EVENT = "item.start";
const DONE_EVENTS = new Set(["item.done", "item.error"]);
const SCRAPE_AGENT = "scrape";

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Fold the live agent-log tail into the scrape stage's "N of M creators".
 *
 * Pure, for the same reason `renderProgress` is: the SSE plumbing lives in `useLogStream`,
 * so everything that can actually be wrong — ordering, malformed payloads, stale entries —
 * is decided here and unit-tested.
 *
 * Returns `null` when there is nothing live to say, which is the signal to the caller to
 * keep showing the disk-derived total instead.
 *
 * Three rules, and the reason for each:
 *
 * 1. ONLY WHILE A SCRAPE JOB IS LIVE. The ring buffer holds events long after a run
 *    settles, so without this gate a finished scrape would advertise "12/12" indefinitely
 *    and a *new* run would start by showing the old one's numbers.
 *
 * 2. `total` COMES FROM `item.start`, NOT `run.start`. Both carry it. The ring is 300
 *    events across ALL agents, and `run.start` is by definition the first record of the
 *    run — so on a long run it is the first evicted, and a reducer that trusted only it
 *    would go blank on exactly the runs worth watching.
 *
 * 3. PROGRESS IS COUNTED IN CREATORS, NEVER IN REELS. The heartbeat's `data.got` is the
 *    CURRENT creator's accumulator: it resets at every creator, so a headline built from it
 *    runs BACKWARDS — "1500 reels" then "37 reels" on the next handle. The heartbeat's job
 *    here is liveness, not arithmetic; the reel total stays with the disk-derived summary,
 *    which only ever grows.
 */
export function scrapeProgress(
  events: readonly LogEvent[] | undefined,
  jobs: Jobs | undefined,
  platform: string,
): ScrapeProgress | null {
  if (!platform) return null;

  // rule 1 — is a scrape actually running for this platform, and since when?
  //
  // Through `latestStageJob`, not by matching a job key: ids are `{platform}:{stage}:{seq}`
  // (app.py), so a literal `${platform}:scrape` matches nothing and this would have returned
  // null forever — silently, since null is also the legitimate "nothing to say" answer.
  const job: Job | undefined = jobs ? latestStageJob(jobs, platform, "scrape") : undefined;
  if (!job || (job.status !== "queued" && job.status !== "running")) return null;
  const startedAt = finite(job.started) ?? 0;

  let total = 0;
  const seen = new Set<string>();
  for (const ev of events ?? []) {
    if (!ev || ev.agent !== SCRAPE_AGENT || ev.platform !== platform) continue;
    const ts = finite(ev.ts);
    if (ts !== null && startedAt && ts < startedAt) continue; // rule 1, cont.
    const data = (ev.data ?? {}) as Record<string, unknown>;

    if (ev.event === START_EVENT) {
      total = Math.max(total, finite(data.of) ?? 0); // rule 2
    } else if (DONE_EVENTS.has(ev.event ?? "")) {
      total = Math.max(total, finite(data.of) ?? 0);
      // rule 3 — dedup by creator, because a re-delivered event must not double-count and
      // item.error/item.done are mutually exclusive per creator by construction.
      if (ev.content_id) seen.add(ev.content_id);
    } else if (ev.event === PROGRESS_EVENT) {
      // Liveness only. Deliberately contributes no number to the headline.
      continue;
    }
  }

  if (!total) return null;
  return { done: Math.min(seen.size, total), total };
}
