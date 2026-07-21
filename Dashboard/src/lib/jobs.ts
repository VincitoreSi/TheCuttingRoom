import type { Job, Jobs, Stage } from "./types";
import type { SeamState } from "../components/Seam";

export const STAGES: Stage[] = ["scrape", "analyze", "media", "analysis-engine"];

/** All jobs for a platform, newest first (seq is the trailing key segment). */
export function jobsFor(jobs: Jobs, platform: string): Job[] {
  return Object.entries(jobs)
    .filter(([, j]) => j.platform === platform)
    .sort((a, b) => seq(b[0]) - seq(a[0]))
    .map(([, j]) => j);
}

function seq(key: string): number {
  const n = Number(key.split(":").pop());
  return Number.isFinite(n) ? n : 0;
}

/** The latest job for a given stage on a platform, if any. */
export function latestStageJob(jobs: Jobs, platform: string, stage: Stage): Job | undefined {
  return jobsFor(jobs, platform).find((j) => j.stage === stage);
}

/** The single most-recent job across a platform (drives the header seam). */
export function latestJob(jobs: Jobs, platform: string): Job | undefined {
  return jobsFor(jobs, platform)[0];
}

export function stageSeamState(job: Job | undefined): SeamState {
  if (!job) return "idle";
  if (job.status === "running" || job.status === "queued") return "working";
  if (job.status === "done") return "done";
  if (job.status === "error") return "error";
  if (job.status === "stopped") return "stopped";
  return "idle";
}

/** The last line a failed stage printed — the bit that says WHY.
    The stages are careful about this ("no scraped data — scrape first", "no Gemini key in
    env"), so the tail is worth surfacing verbatim rather than paraphrasing. Blank lines are
    skipped: a Python traceback ends with one, and the useful line is above it. */
export function failureReason(job: Job | undefined): string {
  if (!job || !job.tail) return "";
  const lines = job.tail.split("\n").filter((l) => l.trim());
  return lines.length ? lines[lines.length - 1].trim() : "";
}

export interface AgentState {
  state: SeamState;
  label: string;
  /** set only on error — the failing stage's last output line */
  detail?: string;
  stage?: Stage;
}

/** Overall agent status for a platform, for the header. */
export function agentState(jobs: Jobs, platform: string): AgentState {
  const list = jobsFor(jobs, platform);
  if (list.length === 0) return { state: "idle", label: "Idle" };
  const running = list.find((j) => j.status === "running" || j.status === "queued");
  if (running)
    return { state: "working", label: `Sewing · ${running.stage}`, stage: running.stage };
  const latest = list[0];
  if (latest.status === "error") {
    return {
      state: "error",
      label: `Snapped · ${latest.stage}`,
      detail: failureReason(latest),
      stage: latest.stage,
    };
  }
  if (latest.status === "stopped")
    return { state: "stopped", label: `Cut · ${latest.stage}`, stage: latest.stage };
  if (latest.status === "done")
    return { state: "done", label: `Knotted · ${latest.stage}`, stage: latest.stage };
  return { state: "idle", label: "Idle" };
}
