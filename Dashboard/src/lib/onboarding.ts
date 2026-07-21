import type { Job, PlatformSummary } from "./types";

/* First-run onboarding, derived from real state — not a stored flag.

   A fresh `./init` install opens on an empty dashboard, and the honest first move
   is "add an Instagram handle in Config", NOT "Run Scrape" (scrape with no handles
   just fails). This models the three steps to a first corpus and reports which one
   is active, so Home can walk a new user through them and get out of the way once
   they are done. */

export type OnboardingStepKey = "handle" | "scrape" | "analyze";

export interface OnboardingStep {
  key: OnboardingStepKey;
  label: string;
  hint: string;
  done: boolean;
}

export interface Onboarding {
  steps: OnboardingStep[];
  /** index of the first not-done step, or steps.length when all are done */
  activeIndex: number;
  complete: boolean;
}

export function deriveOnboarding(args: {
  pagesCount: number;
  summary?: PlatformSummary;
  jobs: Job[];
}): Onboarding {
  const { pagesCount, summary, jobs } = args;

  const jobDone = (stage: string) =>
    jobs.some((j) => j.stage === stage && j.status === "done");
  // The scored corpus only materializes AFTER both scrape and analyze have run, so
  // `items > 0` is the durable "both done" signal that survives a hub restart. The
  // live job ledger lets us check each stage off the instant its job finishes —
  // before the next stage has run — which is what makes the checklist feel live.
  const hasCorpus = (summary?.items ?? 0) > 0;

  const steps: OnboardingStep[] = [
    {
      key: "handle",
      label: "Add an Instagram handle",
      hint: "Put at least one creator on the watchlist in Config to give the scraper something to pull.",
      done: pagesCount > 0,
    },
    {
      key: "scrape",
      label: "Run Scrape",
      hint: "Pull recent reels from your watchlisted creators (guest mode — no login).",
      done: hasCorpus || jobDone("scrape"),
    },
    {
      key: "analyze",
      label: "Run Analyze",
      hint: "Score every reel on the four virality signals and rank the winners.",
      done: hasCorpus || jobDone("analyze"),
    },
  ];

  const firstUndone = steps.findIndex((s) => !s.done);
  return {
    steps,
    activeIndex: firstUndone === -1 ? steps.length : firstUndone,
    complete: firstUndone === -1,
  };
}
