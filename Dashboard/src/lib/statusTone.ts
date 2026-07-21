// The single status→color decision for the whole app. Every card, badge, dot,
// and log knot that reads a status/stage/level/verdict string picks its color
// through this one function — no view recomputes its own tone switch anymore.
//
// Backed ONLY by the existing signature palette (§ styles/index.css) — this
// file introduces no new colors, just a shared vocabulary for the ones that
// already exist:
//   --sage    success / approved / done / accepted
//   --brass   attention / awaiting-decision / selected
//   --oxblood the "working" thread — an agent is actively doing something
//   --amber   warn / self-assessing (a softer caution than danger)
//   --danger  the one hotter red — reject / error / failed
//   --ink*    everything else (queued, draft, pending, muted/rejected)
//
// `Tone` mirrors <Badge tone> 1:1 so call sites never re-map the return value.
export type Tone = "neutral" | "brass" | "sage" | "oxblood" | "amber" | "danger";

export type StatusKind =
  /** Studio proposals + producer "recent outputs" (§2, §9.6). */
  | "proposal"
  /** AutoSearch discovery candidates (§11.4). */
  | "discovery"
  /** An agent-board item, or a run-group's own live/settled state — the full
      workflow_stages vocabulary (Queued, Analyzing, Self-eval, Generating,
      Searching, Scoring, Done, Proposed, Approved, Rejected, Failed). */
  | "agent-item"
  /** Central log event level (§10.1). */
  | "log-level"
  /** /api/pipeline job status (§9.5). */
  | "pipeline-job"
  /** A self-eval judge verdict (§10.2). */
  | "eval-verdict";

/**
 * One tone for one status, given what kind of status it is (the same string
 * — e.g. "approved" — means a different thing, and sometimes a different
 * color, depending on kind).
 */
export function statusTone(kind: StatusKind, status: string | null | undefined): Tone {
  const s = (status ?? "").trim().toLowerCase();

  switch (kind) {
    // Human gate on generated work (§9.6): proposed = brass (awaiting a
    // decision — matches the .gate--proposed rail), approved = sage,
    // rejected = muted (dimmed via .gate--rejected's opacity, not a hotter
    // color), draft/unset = plain neutral.
    case "proposal":
      if (s === "approved") return "sage";
      if (s === "rejected") return "neutral";
      if (s === "proposed") return "brass";
      return "neutral";

    // Human gate on discovered creators (§11.4) — the same tri-state as
    // proposal, so a rejected candidate and a rejected proposal read the
    // same way: muted, not alarmed.
    case "discovery":
      if (s === "approved") return "sage";
      if (s === "rejected") return "neutral";
      return "neutral"; // pending

    // A single agent-board item or a run-group's live/settled read. Any
    // stage still being worked (Analyzing, Generating, Searching, Scoring, …)
    // reads as the oxblood "working" thread — the same color Seam/PipelineBoard
    // already use for "in progress" — with Self-eval called out in amber
    // (the agent judging its own output, not the danger/failure state).
    case "agent-item":
      if (s === "failed") return "danger";
      if (s === "rejected") return "neutral";
      if (s === "approved" || s === "done") return "sage";
      if (s === "proposed") return "brass";
      if (s === "queued") return "neutral";
      if (s === "self-eval") return "amber";
      return "oxblood"; // Analyzing / Generating / Searching / Scoring / …

    // Central log levels (§10.1) — info reads as plain ink, not a color at
    // all, so it maps to the same neutral badge as "nothing special here".
    case "log-level":
      if (s === "error" || s === "critical") return "danger";
      if (s === "warn" || s === "warning") return "amber";
      if (s === "success" || s === "done") return "sage";
      return "neutral"; // info / anything unrecognized

    // A pipeline job (§9.5) — queued and running both read as "the thread is
    // moving" (matches lib/jobs.ts's stageSeamState, which already treats
    // queued and running as one "working" state).
    case "pipeline-job":
      if (s === "error") return "danger";
      if (s === "done") return "sage";
      if (s === "running" || s === "queued") return "oxblood";
      return "neutral";

    // A self-eval judge verdict (§10.2).
    case "eval-verdict":
      if (s === "accept") return "sage";
      if (s === "reject") return "danger";
      return "neutral";

    default:
      return "neutral";
  }
}
