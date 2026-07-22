import { describe, expect, it } from "vitest";
import { scrapeProgress } from "./scrapeProgress";
import type { Jobs, LogEvent } from "./types";

const P = "instagram";

function jobs(status: string, started = 100): Jobs {
  return {
    "instagram:scrape:7": {
      job_id: "instagram:scrape:7",
      platform: P,
      stage: "scrape",
      status,
      started,
    },
  } as unknown as Jobs;
}

function ev(partial: Partial<LogEvent>): LogEvent {
  return {
    agent: "scrape",
    level: "info",
    event: "item.start",
    platform: P,
    ts: 200,
    ...partial,
  } as LogEvent;
}

describe("scrapeProgress", () => {
  it("says nothing when no scrape is running", () => {
    const events = [ev({ event: "item.done", content_id: "a", data: { of: 3 } })];
    expect(scrapeProgress(events, jobs("done"), P)).toBeNull();
    expect(scrapeProgress(events, undefined, P)).toBeNull();
  });

  it("counts creators done against the total", () => {
    const events = [
      ev({ event: "item.start", content_id: "a", data: { of: 3 } }),
      ev({ event: "item.done", content_id: "a", data: { of: 3 } }),
      ev({ event: "item.start", content_id: "b", data: { of: 3 } }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 1, total: 3 });
  });

  it("reads the total from item.start, so losing run.start to the ring is survivable", () => {
    // The ring is 300 events across ALL agents and run.start is the run's first record, so
    // on a long run it is the first evicted. Nothing here carries it, and it still works.
    const events = [ev({ event: "item.start", content_id: "a", data: { of: 12 } })];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 0, total: 12 });
  });

  it("ignores the heartbeat's per-creator count entirely", () => {
    // data.got resets at every creator, so a headline built from it runs BACKWARDS. It must
    // not reach the number at all — only prove the run is alive.
    const events = [
      ev({ event: "item.start", content_id: "a", data: { of: 2 } }),
      ev({ event: "item.progress", content_id: "a", data: { got: 240, of: 250 } }),
      ev({ event: "item.progress", content_id: "b", data: { got: 12, of: 250 } }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 0, total: 2 });
  });

  it("counts a failed creator as finished", () => {
    // Otherwise done/total can never reach parity on a run with one bad handle, and the card
    // sits at 2/3 forever after the run has ended.
    const events = [
      ev({ event: "item.start", content_id: "a", data: { of: 2 } }),
      ev({ event: "item.error", level: "error", content_id: "a", data: { of: 2 } }),
      ev({ event: "item.done", content_id: "b", data: { of: 2 } }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 2, total: 2 });
  });

  it("does not double-count a re-delivered event", () => {
    const events = [
      ev({ event: "item.done", content_id: "a", data: { of: 2 } }),
      ev({ event: "item.done", content_id: "a", data: { of: 2 } }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 1, total: 2 });
  });

  it("ignores events from before this run started", () => {
    const events = [
      ev({ event: "item.done", content_id: "old", ts: 50, data: { of: 9 } }),
      ev({ event: "item.start", content_id: "a", ts: 150, data: { of: 2 } }),
    ];
    expect(scrapeProgress(events, jobs("running", 100), P)).toEqual({ done: 0, total: 2 });
  });

  it("ignores other agents and other platforms", () => {
    const events = [
      ev({ event: "item.start", content_id: "a", data: { of: 2 } }),
      ev({ agent: "analysis-engine", event: "item.done", content_id: "x", data: { of: 99 } }),
      ev({ platform: "youtube", event: "item.done", content_id: "y", data: { of: 99 } }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toEqual({ done: 0, total: 2 });
  });

  it("survives malformed payloads", () => {
    const events = [
      ev({ event: "item.start", content_id: "a", data: null }),
      ev({ event: "item.done", content_id: "b", data: { of: "many" } as never }),
    ];
    expect(scrapeProgress(events, jobs("running"), P)).toBeNull(); // no usable total
  });
});
