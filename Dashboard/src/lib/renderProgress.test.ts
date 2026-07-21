import { describe, expect, it } from "vitest";
import { renderProgress } from "./renderProgress";
import type { Job, Jobs, LogEvent } from "./types";

const P = "instagram";
const T0 = 1_700_000_000;

function job(file: string, over: Partial<Job> = {}, platform = P): Jobs {
  return {
    [`${platform}:render:${file}`]: {
      platform,
      stage: "render" as Job["stage"],
      status: "running",
      started: T0,
      ended: null,
      rc: null,
      tail: "",
      ...over,
    },
  };
}

function ev(
  file: string | undefined,
  frame: unknown,
  of: unknown,
  over: Partial<LogEvent> = {},
): LogEvent {
  return {
    agent: "similar-content",
    level: "info",
    event: "item.progress",
    msg: `frame ${String(frame)}/${String(of)}`,
    platform: P,
    ts: T0 + 1,
    data: { file, frame, of, stage: "Rendering" },
    ...over,
  };
}

describe("renderProgress", () => {
  it("tracks a normal 1..N progression, keeping the last frame seen", () => {
    const events = [1, 2, 3, 4, 5, 6].map((n) => ev("a.md", n, 6, { ts: T0 + n }));
    const m = renderProgress(events, job("a.md"), P);
    expect(m.get("a.md")).toEqual({ frame: 6, total: 6 });
  });

  it("reports the very first frame as soon as it arrives", () => {
    const m = renderProgress([ev("a.md", 1, 6)], job("a.md"), P);
    expect(m.get("a.md")).toEqual({ frame: 1, total: 6 });
  });

  it("keeps the newest event when frames arrive out of order", () => {
    const events = [
      ev("a.md", 4, 6, { ts: T0 + 4 }),
      ev("a.md", 2, 6, { ts: T0 + 2 }), // late delivery of an older frame
      ev("a.md", 3, 6, { ts: T0 + 3 }),
    ];
    expect(renderProgress(events, job("a.md"), P).get("a.md")).toEqual({ frame: 4, total: 6 });
  });

  it("breaks a timestamp tie by the higher frame", () => {
    const events = [ev("a.md", 5, 6, { ts: T0 + 1 }), ev("a.md", 2, 6, { ts: T0 + 1 })];
    expect(renderProgress(events, job("a.md"), P).get("a.md")).toEqual({ frame: 5, total: 6 });
  });

  it("lets a re-render restart at frame 1 instead of sticking at the old high", () => {
    const restart = T0 + 100;
    const events = [
      ev("a.md", 6, 6, { ts: T0 + 6 }), // previous pass, still in the ring buffer
      ev("a.md", 1, 6, { ts: restart + 1 }),
    ];
    const m = renderProgress(events, job("a.md", { started: restart }), P);
    expect(m.get("a.md")).toEqual({ frame: 1, total: 6 });
  });

  it("drops events stamped before the current job started", () => {
    const events = [ev("a.md", 6, 6, { ts: T0 - 50 })];
    expect(renderProgress(events, job("a.md"), P).has("a.md")).toBe(false);
  });

  it("keys separate files independently", () => {
    const jobs = { ...job("a.md"), ...job("b.md") };
    const events = [ev("a.md", 2, 6, { ts: T0 + 2 }), ev("b.md", 5, 9, { ts: T0 + 3 })];
    const m = renderProgress(events, jobs, P);
    expect(m.get("a.md")).toEqual({ frame: 2, total: 6 });
    expect(m.get("b.md")).toEqual({ frame: 5, total: 9 });
  });

  it("ignores a file that has no running job of its own", () => {
    const m = renderProgress([ev("other.md", 3, 6)], job("a.md"), P);
    expect(m.size).toBe(0);
  });

  it("ignores events from another platform", () => {
    const m = renderProgress([ev("a.md", 3, 6, { platform: "tiktok" })], job("a.md"), P);
    expect(m.size).toBe(0);
  });

  it("ignores a job belonging to another platform", () => {
    const m = renderProgress([ev("a.md", 3, 6)], job("a.md", {}, "tiktok"), P);
    expect(m.size).toBe(0);
  });

  it("ignores log events that are not item.progress", () => {
    const m = renderProgress([ev("a.md", 3, 6, { event: "item.done" })], job("a.md"), P);
    expect(m.size).toBe(0);
  });

  it.each([
    ["queued", true],
    ["running", true],
    ["done", false],
    ["error", false],
  ] as const)("job status %s -> tracked: %s", (status, tracked) => {
    const m = renderProgress([ev("a.md", 6, 6)], job("a.md", { status }), P);
    expect(m.has("a.md")).toBe(tracked);
  });

  it("clears the entry once the render completes, so 6/6 does not linger", () => {
    const events = [1, 2, 3, 4, 5, 6].map((n) => ev("a.md", n, 6, { ts: T0 + n }));
    expect(renderProgress(events, job("a.md"), P).size).toBe(1);
    const settled = job("a.md", { status: "done", ended: T0 + 7 });
    expect(renderProgress(events, settled, P).size).toBe(0);
  });

  it("never produces a zero or negative total (no divide-by-zero downstream)", () => {
    for (const of of [0, -1, -6]) {
      const m = renderProgress([ev("a.md", 3, of)], job("a.md"), P);
      expect(m.size).toBe(0);
    }
  });

  it("falls back to the previous good event when a later one has of === 0", () => {
    const events = [ev("a.md", 2, 6, { ts: T0 + 2 }), ev("a.md", 3, 0, { ts: T0 + 3 })];
    expect(renderProgress(events, job("a.md"), P).get("a.md")).toEqual({ frame: 2, total: 6 });
  });

  it("survives malformed events without throwing", () => {
    const malformed: LogEvent[] = [
      { ...ev("a.md", 1, 6), data: null },
      { ...ev("a.md", 1, 6), data: undefined },
      ev("a.md", "3", "6"), // strings, not numbers
      ev("a.md", NaN, 6),
      ev("a.md", 3, Infinity),
      ev("a.md", -2, 6),
      ev(undefined, 3, 6), // no file
      ev("", 3, 6),
      { ...ev("a.md", 1, 6), data: { file: 42, frame: 1, of: 6 } },
      { ...ev("a.md", 1, 6), ts: undefined as unknown as number },
    ];
    expect(() => renderProgress(malformed, job("a.md"), P)).not.toThrow();
    expect(renderProgress(malformed, job("a.md"), P).size).toBe(0);
  });

  it("tolerates empty / missing inputs", () => {
    expect(renderProgress([], {}, P).size).toBe(0);
    expect(renderProgress(undefined, undefined, P).size).toBe(0);
    expect(renderProgress([ev("a.md", 1, 6)], job("a.md"), "").size).toBe(0);
  });

  it("returns a good frame even when surrounded by malformed ones", () => {
    const events = [
      { ...ev("a.md", 1, 6), data: null },
      ev("a.md", 4, 6, { ts: T0 + 4 }),
      ev("a.md", "5", 6, { ts: T0 + 5 }),
    ];
    expect(renderProgress(events, job("a.md"), P).get("a.md")).toEqual({ frame: 4, total: 6 });
  });
});
