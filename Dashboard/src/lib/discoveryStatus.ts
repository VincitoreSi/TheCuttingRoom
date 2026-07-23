import type { AgentRosterEntry, LogEvent } from "./types";

/* discoveryStatus — pure reducers for the Discover view's "last run" strip and readiness
   indicator. The SSE / query plumbing lives in hooks; everything that can actually be
   wrong (ordering, a run with no terminal reason, guest-only vs burner) is decided here and
   unit-tested. Mirrors scrapeProgress.ts / renderProgress.ts.

   The events these read are the ones AutoSearch now emits (AutoSearch/cli.py): `run.end`
   carries `data.reason ∈ {ok, guest_only_no_search, no_candidates_passed_gates}` plus the
   counts, and `run.start` carries `data.surface ∈ {guest-only, burner}`. */

const AUTO_SEARCH = "auto-search";

export type Tone = "sage" | "amber" | "danger" | "neutral";

export interface LastRunSummary {
  runId: string | null;
  ts: number | null;
  proposed: number;
  reason: string;
  surface: string; // "guest-only" | "burner" | "unknown"
  headline: string;
  detail: string;
  tone: Tone;
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** The human sentence for a terminal run reason. Pure + total, so a new reason string
    degrades to a plain proposed-count line rather than a blank strip. */
export function reasonMessage(
  reason: string,
  proposed: number,
): { headline: string; detail: string; tone: Tone } {
  const plural = proposed === 1 ? "candidate" : "candidates";
  switch (reason) {
    case "guest_only_no_search":
      return {
        headline: `${proposed} proposed — guest-only mode`,
        detail:
          "Instagram search is login-gated, so guest-only discovery finds little or nothing. " +
          "Add a burner session (IG_SESSIONID or AutoSearch/session.txt) and turn guest mode off " +
          "to actually search.",
        tone: "amber",
      };
    case "no_candidates_passed_gates":
      return {
        headline: `${proposed} proposed — nobody cleared the gates`,
        detail:
          "Creators were found and hydrated, but none met the follower / median-play minimums. " +
          "Loosen those thresholds on the auto-search config, or widen the seed keywords.",
        tone: "amber",
      };
    case "ok":
      return {
        headline: `${proposed} ${plural} proposed`,
        detail: "The last run pinned new creators to the review queue below.",
        tone: "sage",
      };
    default:
      return {
        headline: `${proposed} ${plural} proposed`,
        detail: "",
        tone: "neutral",
      };
  }
}

/** Fold the agent-log tail into the most recent auto-search run for one platform.

    Returns null when no run.end has been seen yet (a fresh install, or a run still in
    flight) — the signal to the caller to show a "hasn't finished a run yet" placeholder
    rather than a stale or empty strip. */
export function lastDiscoveryRun(
  events: readonly LogEvent[] | undefined,
  platform: string,
): LastRunSummary | null {
  if (!platform || !events?.length) return null;

  let end: LogEvent | null = null;
  for (const ev of events) {
    if (!ev || ev.agent !== AUTO_SEARCH || ev.event !== "run.end") continue;
    if (ev.platform && ev.platform !== platform) continue;
    if (!end || num(ev.ts) >= num(end.ts)) end = ev;
  }
  if (!end) return null;

  const data = (end.data ?? {}) as Record<string, unknown>;
  const proposed = num(data.proposed);
  const reason = typeof data.reason === "string" ? data.reason : "";

  // surface comes from the matching run.start (same run_id) when it is still in the ring.
  let surface = "unknown";
  for (const ev of events) {
    if (ev?.agent !== AUTO_SEARCH || ev.event !== "run.start") continue;
    if (ev.run_id && ev.run_id === end.run_id) {
      const s = (ev.data ?? {}) as Record<string, unknown>;
      if (typeof s.surface === "string") surface = s.surface;
      break;
    }
  }

  const { headline, detail, tone } = reasonMessage(reason, proposed);
  return {
    runId: end.run_id ?? null,
    ts: typeof end.ts === "number" ? end.ts : null,
    proposed,
    reason,
    surface,
    headline,
    detail,
    tone,
  };
}

export interface DiscoveryReadiness {
  mode: "guest-only" | "burner";
  igPresent: boolean;
  geminiPresent: boolean;
  /** true only when a burner session is present AND guest mode is off — the ONLY state in
      which AutoSearch actually touches Instagram's search surface. */
  searchEnabled: boolean;
}

/** What enables (or doesn't) a real search, read off the auto-search agent config +
    secret status. Gemini is deliberately NOT part of `searchEnabled`: it only gates
    optional term expansion, never search itself. */
export function discoveryReadiness(
  cfg: Record<string, unknown> | undefined,
  secrets: AgentRosterEntry["secrets"] | undefined,
): DiscoveryReadiness {
  const guestOnly = (cfg?.guest_only ?? true) !== false; // default true (see cli CONFIG_SCHEMA)
  const igPresent = (secrets ?? []).some(
    (s) => s.env_var === "IG_SESSIONID" && s.present === true,
  );
  const geminiPresent = (secrets ?? []).some(
    (s) => s.env_var === "GEMINI_API_KEY" && s.present === true,
  );
  const searchEnabled = igPresent && !guestOnly;
  return {
    mode: searchEnabled ? "burner" : "guest-only",
    igPresent,
    geminiPresent,
    searchEnabled,
  };
}
