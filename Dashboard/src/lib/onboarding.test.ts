import { describe, expect, it } from "vitest";
import { deriveOnboarding } from "./onboarding";
import type { Job, PlatformSummary } from "./types";

const job = (stage: string, status: Job["status"]): Job => ({
  platform: "instagram",
  stage: stage as Job["stage"],
  status,
  started: 0,
  ended: status === "done" || status === "error" ? 1 : null,
  rc: status === "done" ? 0 : status === "error" ? 1 : null,
  tail: "",
});

const summary = (over: Partial<PlatformSummary> = {}): PlatformSummary => ({
  platform: "instagram",
  has_data: false,
  scraped: false,
  watchlist: 0,
  scraped_items: 0,
  readiness: {},
  items: 0,
  creators: 0,
  viral: 0,
  media_ready: 0,
  ...over,
});

describe("deriveOnboarding", () => {
  it("fresh install: step 1 (add handle) is active, nothing complete", () => {
    const ob = deriveOnboarding({ pagesCount: 0, summary: undefined, jobs: [] });
    expect(ob.activeIndex).toBe(0);
    expect(ob.steps[0].key).toBe("handle");
    expect(ob.steps.every((s) => !s.done)).toBe(true);
    expect(ob.complete).toBe(false);
  });

  it("handle added but no corpus: step 2 (scrape) is active", () => {
    const ob = deriveOnboarding({ pagesCount: 1, summary: summary(), jobs: [] });
    expect(ob.steps[0].done).toBe(true);
    expect(ob.activeIndex).toBe(1);
    expect(ob.steps[1].key).toBe("scrape");
    expect(ob.complete).toBe(false);
  });

  it("scrape job done via the live ledger checks scrape off before a corpus exists", () => {
    const ob = deriveOnboarding({
      pagesCount: 1,
      summary: summary(),
      jobs: [job("scrape", "done")],
    });
    expect(ob.steps[1].done).toBe(true); // scrape
    expect(ob.steps[2].done).toBe(false); // analyze
    expect(ob.activeIndex).toBe(2);
  });

  it("raw scrape output on disk checks scrape off with an empty job ledger", () => {
    // The ledger is in-memory: restart the hub and a finished scrape leaves no trace in
    // it. The checklist used to walk the user back to "Run Scrape" and re-pull reels
    // that were already sitting on disk. `scraped` comes off the filesystem instead.
    const ob = deriveOnboarding({
      pagesCount: 1,
      summary: summary({ scraped: true }),
      jobs: [],
    });
    expect(ob.steps[1].done).toBe(true); // scrape
    expect(ob.steps[2].done).toBe(false); // analyze — the real next move
    expect(ob.activeIndex).toBe(2);
  });

  it("a FAILED scrape job does not check the step off", () => {
    const ob = deriveOnboarding({
      pagesCount: 1,
      summary: summary(),
      jobs: [job("scrape", "error")],
    });
    expect(ob.steps[1].done).toBe(false);
    expect(ob.activeIndex).toBe(1);
  });

  it("a scored corpus (items > 0) completes scrape AND analyze durably", () => {
    const ob = deriveOnboarding({
      pagesCount: 2,
      summary: summary({ has_data: true, items: 40, creators: 3 }),
      jobs: [],
    });
    expect(ob.steps.every((s) => s.done)).toBe(true);
    expect(ob.complete).toBe(true);
    expect(ob.activeIndex).toBe(ob.steps.length);
  });

  it("corpus present but handle removed still flags the missing handle first", () => {
    // defensive: durable corpus signal shouldn't hide that pages.txt is empty
    const ob = deriveOnboarding({
      pagesCount: 0,
      summary: summary({ items: 10 }),
      jobs: [],
    });
    expect(ob.steps[0].done).toBe(false);
    expect(ob.activeIndex).toBe(0);
    expect(ob.complete).toBe(false);
  });
});
