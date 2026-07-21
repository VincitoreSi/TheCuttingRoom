import { describe, it, expect } from "vitest";
import {
  verbOf,
  groupByRun,
  isLive,
  streamState,
  activityFacets,
  throughputBins,
  floorSummary,
  activitySummary,
  liveStageIndex,
  packetCount,
  LIVE_STAGE_WINDOW,
  ACTIVE_WINDOW,
  RUN_STALE_SEC,
  type RunGroup,
} from "./activityModel";
import { applyLogEvent } from "./agentBoard";
import type { AgentBoard, LogEvent, Jobs, Job } from "./types";

function ev(partial: Partial<LogEvent>): LogEvent {
  return {
    agent: "analysis-engine",
    level: "info",
    event: "item.stage",
    ts: 1000,
    ...partial,
  };
}

describe("verbOf — the single event-name normalizer", () => {
  it("passes dotted names through", () => {
    expect(verbOf(ev({ event: "item.done" }))).toBe("item.done");
    expect(verbOf(ev({ event: "run.start" }))).toBe("run.start");
    expect(verbOf(ev({ event: "item.error" }))).toBe("item.error");
  });
  it("normalizes underscored drift (similar-content)", () => {
    expect(verbOf(ev({ event: "run_start" }))).toBe("run.start");
    expect(verbOf(ev({ event: "run_end" }))).toBe("run.end");
    expect(verbOf(ev({ event: "clone_done" }))).toBe("item.done");
  });
  it("maps clone.done → item.done", () => {
    expect(verbOf(ev({ event: "clone.done" }))).toBe("item.done");
  });
  it("maps selftest / unknown / empty → other", () => {
    expect(verbOf(ev({ event: "selftest" }))).toBe("other");
    expect(verbOf(ev({ event: "heartbeat" }))).toBe("other");
    expect(verbOf(ev({ event: "" }))).toBe("other");
  });
});

describe("groupByRun — counts by content_id at parity with agentBoard.ts", () => {
  // A run exercising every terminal set: A→Done, D→Proposed (both DONE_STAGES),
  // B→item.error (Failed), R→Rejected (both FAIL_STAGES), C left mid-flight.
  const runEvents: LogEvent[] = [
    ev({ event: "run.start", run_id: "r1", ts: 100 }),
    ev({ event: "item.start", run_id: "r1", content_id: "A", ts: 101 }),
    ev({ event: "item.done", run_id: "r1", content_id: "A", ts: 102, data: { stage: "Done" } }),
    ev({ event: "item.start", run_id: "r1", content_id: "D", ts: 103 }),
    ev({ event: "item.done", run_id: "r1", content_id: "D", ts: 104, data: { stage: "Proposed" } }),
    ev({ event: "item.start", run_id: "r1", content_id: "B", ts: 105 }),
    ev({ event: "item.error", run_id: "r1", content_id: "B", ts: 106, level: "error" }),
    ev({ event: "item.start", run_id: "r1", content_id: "R", ts: 107 }),
    ev({ event: "item.done", run_id: "r1", content_id: "R", ts: 108, data: { stage: "Rejected" } }),
    ev({
      event: "item.start",
      run_id: "r1",
      content_id: "C",
      ts: 109,
      data: { stage: "Analyzing" },
    }),
  ];

  it("counts total/done/failed by content_id", () => {
    const g = groupByRun(runEvents)[0];
    expect(g.total).toBe(5); // A D B R C
    expect(g.done).toBe(2); // A(Done) D(Proposed)
    expect(g.failed).toBe(2); // B(Failed) R(Rejected)
  });

  it("matches agentBoard.applyLogEvent counts exactly (set parity)", () => {
    // Fold the same events through the Agent Desk reducer and compare.
    let board: AgentBoard = {
      agent: "analysis-engine",
      kind: "analyzer",
      workflow_stages: ["Queued", "Analyzing", "Done"],
      runs: [],
    };
    for (const e of runEvents) board = applyLogEvent(board, e);
    const boardRun = board.runs.find((r) => r.run_id === "r1")!;
    const g = groupByRun(runEvents)[0];
    expect(g.total).toBe(boardRun.counts.total);
    expect(g.done).toBe(boardRun.counts.done);
    expect(g.failed).toBe(boardRun.counts.failed);
  });

  it("buckets run-less events under ${agent}:∅ and still threads them", () => {
    const groups = groupByRun([
      ev({ agent: "template-selftest", event: "selftest", run_id: null, ts: 200 }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe("template-selftest:∅");
    expect(groups[0].runId).toBe("∅");
  });

  it("marks a run with run.end and all-terminal items as done", () => {
    const groups = groupByRun([
      ev({ event: "run.start", run_id: "r2", ts: 300 }),
      ev({ event: "item.start", run_id: "r2", content_id: "X", ts: 301 }),
      ev({ event: "item.done", run_id: "r2", content_id: "X", ts: 302, data: { stage: "Done" } }),
      ev({ event: "run.end", run_id: "r2", ts: 303 }),
    ]);
    expect(groups[0].state).toBe("done");
    expect(groups[0].ended).toBe(303);
  });
});

describe("isLive — working & fresh, stale working stays working but not live", () => {
  const working: RunGroup = {
    key: "a:r",
    runId: "r",
    agent: "a",
    platform: null,
    started: 0,
    lastTs: 1000,
    ended: null,
    stage: null,
    state: "working",
    worstLevel: "info",
    total: 0,
    done: 0,
    failed: 0,
    events: [],
  };
  it("is live when fresh", () => {
    expect(isLive(working, 1000 + 10)).toBe(true);
  });
  it("is NOT live when stale, though still 'working'", () => {
    expect(isLive(working, 1000 + RUN_STALE_SEC + 1)).toBe(false);
    expect(working.state).toBe("working");
  });
  it("done runs are never live", () => {
    expect(isLive({ ...working, state: "done" }, 1000)).toBe(false);
  });
});

describe("streamState — precedence error > working > done > idle", () => {
  const now = 1000;
  const mk = (state: RunGroup["state"], agent: string): RunGroup => ({
    key: `${agent}:r`,
    runId: "r",
    agent,
    platform: null,
    started: now - 5,
    lastTs: now - 1,
    ended: null,
    stage: null,
    state,
    worstLevel: "info",
    total: 0,
    done: 0,
    failed: 0,
    events: [],
  });

  it("empty → idle", () => {
    expect(streamState([], now)).toEqual({ state: "idle", label: "Idle" });
  });
  it("error beats working", () => {
    expect(streamState([mk("working", "w"), mk("error", "e")], now).state).toBe("error");
  });
  it("working beats done", () => {
    expect(streamState([mk("done", "d"), mk("working", "w")], now).state).toBe("working");
  });
  it("done beats idle", () => {
    expect(streamState([mk("idle", "i"), mk("done", "d")], now).state).toBe("done");
  });
});

describe("throughputBins — fixed-length, zero-filled, seconds, right-edge=nowSec", () => {
  const now = 10_000;

  it("Activity window (300,15) → exactly 20 bins", () => {
    expect(throughputBins([], now, 300, 15)).toHaveLength(20);
  });
  it("Board window (60,2) → exactly 30 bins", () => {
    expect(throughputBins([], now, 60, 2)).toHaveLength(30);
  });
  it("rightmost bin's t === nowSec (right edge)", () => {
    const bins = throughputBins([], now, 300, 15);
    expect(bins[bins.length - 1].t).toBe(now);
  });
  it("counts in SECONDS: an event at nowSec lands in the last bin", () => {
    const bins = throughputBins([ev({ ts: now })], now, 300, 15);
    expect(bins[bins.length - 1].n).toBe(1);
  });
  it("older event lands in an earlier bin, tail stays zero-filled", () => {
    const bins = throughputBins([ev({ ts: now - 299 })], now, 300, 15);
    expect(bins[0].n).toBe(1);
    expect(bins[bins.length - 1].n).toBe(0);
    expect(bins.reduce((s, b) => s + b.n, 0)).toBe(1);
  });
  it("skips records with no usable ts and drops out-of-window events", () => {
    const bins = throughputBins(
      [ev({ ts: NaN }), ev({ ts: undefined as unknown as number }), ev({ ts: now - 999 })],
      now,
      300,
      15,
    );
    expect(bins.reduce((s, b) => s + b.n, 0)).toBe(0);
  });
});

describe("window constants — intentionally distinct scales", () => {
  it("are 8 / 20 / 45 and all different", () => {
    expect(LIVE_STAGE_WINDOW).toBe(8);
    expect(ACTIVE_WINDOW).toBe(20);
    expect(RUN_STALE_SEC).toBe(45);
    expect(new Set([LIVE_STAGE_WINDOW, ACTIVE_WINDOW, RUN_STALE_SEC]).size).toBe(3);
  });
});

describe("activityFacets — computed over the full ring", () => {
  it("collects sorted agents, levels, and run keys", () => {
    const f = activityFacets([
      ev({ agent: "b", level: "warn", run_id: "r1" }),
      ev({ agent: "a", level: "info", run_id: "r2" }),
    ]);
    expect(f.agents).toEqual(["a", "b"]);
    expect(f.levels).toEqual(["info", "warn"]);
    expect(f.runs.map((r) => r.runId).sort()).toEqual(["r1", "r2"]);
  });
});

describe("floorSummary", () => {
  it("counts active/done/errored and per-minute throughput", () => {
    const now = 1000;
    const events: LogEvent[] = [
      ev({ event: "item.start", run_id: "live", agent: "a", ts: now - 2 }),
      ev({ event: "run.start", run_id: "done", agent: "b", ts: now - 100 }),
      ev({ event: "run.end", run_id: "done", agent: "b", ts: now - 99 }),
      ev({ event: "item.error", run_id: "err", agent: "c", ts: now - 3, level: "error" }),
    ];
    const s = floorSummary(groupByRun(events), events, now);
    expect(s.active).toBe(1);
    expect(s.done).toBe(1);
    expect(s.errored).toBe(1);
    expect(s.agentsLive).toBe(1);
    expect(s.perMin).toBe(2); // only the two events within the trailing 60s
    expect(s.lastTs).toBe(now - 2);
  });
});

describe("packetCount — distinct in-flight content_id via verbOf", () => {
  it("counts started-but-not-done items", () => {
    const events: LogEvent[] = [
      ev({ event: "run.start", run_id: "r", platform: "instagram", ts: 1 }),
      ev({ event: "item.start", run_id: "r", platform: "instagram", content_id: "A", ts: 2 }),
      ev({ event: "item.start", run_id: "r", platform: "instagram", content_id: "B", ts: 3 }),
      ev({ event: "item.done", run_id: "r", platform: "instagram", content_id: "A", ts: 4 }),
    ];
    expect(packetCount(events, "instagram", "r")).toBe(1); // B still in flight
  });
  it("falls back to run.start data.limit when nothing is in flight", () => {
    const events: LogEvent[] = [
      ev({ event: "run.start", run_id: "r", platform: "instagram", ts: 1, data: { limit: 7 } }),
    ];
    expect(packetCount(events, "instagram", "r")).toBe(7);
  });
  it("returns null when there is no live run", () => {
    expect(packetCount([], "instagram", null)).toBeNull();
  });
});

describe("liveStageIndex — Job axis OR fresh-log axis", () => {
  const nodes = [
    { key: "discover", stage: "auto-search", agent: "auto-search" },
    { key: "analyze", stage: "analyze", agent: "analysis-engine" },
  ];
  const now = 1000;

  it("fires on the Job axis (running job for a node's stage)", () => {
    const jobs: Jobs = {
      "instagram:analyze:1": {
        platform: "instagram",
        stage: "analyze",
        status: "running",
        started: now - 5,
        ended: null,
        rc: null,
        tail: "",
      } as Job,
    };
    expect(liveStageIndex(nodes, jobs, [], "instagram", now)).toBe(1);
  });

  it("fires on the log axis for a log-only agent with no Job", () => {
    const events: LogEvent[] = [
      ev({ agent: "analysis-engine", event: "item.start", platform: "instagram", ts: now - 2 }),
    ];
    expect(liveStageIndex(nodes, {}, events, "instagram", now)).toBe(1);
  });

  it("ignores stale log events beyond LIVE_STAGE_WINDOW", () => {
    const events: LogEvent[] = [
      ev({
        agent: "analysis-engine",
        event: "item.start",
        platform: "instagram",
        ts: now - LIVE_STAGE_WINDOW - 1,
      }),
    ];
    expect(liveStageIndex(nodes, {}, events, "instagram", now)).toBe(-1);
  });
});

describe("activitySummary — wraps streamState, adds tiles, honors connected", () => {
  const now = 1000;
  it("reports Offline when disconnected", () => {
    const s = activitySummary([], {}, "instagram", now, false);
    expect(s.seamState).toBe("idle");
    expect(s.label).toBe("Offline");
  });
  it("reads 'Idle · ready' on a fresh, connected, empty hub", () => {
    const s = activitySummary([], {}, "instagram", now, true);
    expect(s.seamState).toBe("idle");
    expect(s.label).toBe("Idle · ready");
  });
  it("goes working with active run/agent counts on a fresh in-flight run", () => {
    const events: LogEvent[] = [
      ev({
        event: "run.start",
        run_id: "r",
        platform: "instagram",
        agent: "analysis-engine",
        ts: now - 2,
      }),
      ev({
        event: "item.start",
        run_id: "r",
        platform: "instagram",
        agent: "analysis-engine",
        content_id: "A",
        ts: now - 1,
      }),
    ];
    const s = activitySummary(events, {}, "instagram", now, true);
    expect(s.seamState).toBe("working");
    expect(s.runsActive).toBe(1);
    expect(s.agentsActive).toBe(1);
    expect(s.liveRunId).toBe("r");
    expect(s.lastEventAgo).toBe(1);
    expect(s.packetCount).toBe(1); // A in flight
  });
});
