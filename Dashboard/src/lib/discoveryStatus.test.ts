import { describe, expect, it } from "vitest";
import { discoveryReadiness, lastDiscoveryRun, reasonMessage } from "./discoveryStatus";
import type { LogEvent, SecretStatus } from "./types";

function ev(partial: Partial<LogEvent>): LogEvent {
  return {
    agent: "auto-search",
    level: "info",
    event: "run.end",
    ts: 0,
    ...partial,
  };
}

describe("reasonMessage", () => {
  it("names the guest-only symptom and its fix", () => {
    const m = reasonMessage("guest_only_no_search", 0);
    expect(m.headline).toContain("guest-only");
    expect(m.detail.toLowerCase()).toContain("burner");
    expect(m.tone).toBe("amber");
  });

  it("distinguishes a gate shutout from a search shutout", () => {
    const m = reasonMessage("no_candidates_passed_gates", 0);
    expect(m.headline.toLowerCase()).toContain("gate");
    expect(m.tone).toBe("amber");
  });

  it("reads a successful run as sage", () => {
    const m = reasonMessage("ok", 3);
    expect(m.headline).toContain("3");
    expect(m.tone).toBe("sage");
  });

  it("degrades an unknown reason to a plain count, never blank", () => {
    const m = reasonMessage("something_new", 2);
    expect(m.headline).toContain("2");
    expect(m.tone).toBe("neutral");
  });
});

describe("lastDiscoveryRun", () => {
  it("returns null before any run has ended", () => {
    expect(lastDiscoveryRun([], "instagram")).toBeNull();
    expect(lastDiscoveryRun([ev({ event: "run.start", ts: 1 })], "instagram")).toBeNull();
  });

  it("picks the newest run.end for the platform and reads its reason + counts", () => {
    const events: LogEvent[] = [
      ev({
        event: "run.end",
        ts: 10,
        run_id: "r1",
        platform: "instagram",
        data: { proposed: 0, reason: "guest_only_no_search" },
      }),
      ev({
        event: "run.end",
        ts: 20,
        run_id: "r2",
        platform: "instagram",
        data: { proposed: 2, reason: "ok" },
      }),
    ];
    const s = lastDiscoveryRun(events, "instagram");
    expect(s?.runId).toBe("r2");
    expect(s?.proposed).toBe(2);
    expect(s?.reason).toBe("ok");
    expect(s?.tone).toBe("sage");
  });

  it("ignores run.end from other platforms and other agents", () => {
    const events: LogEvent[] = [
      ev({ event: "run.end", ts: 30, platform: "youtube", data: { proposed: 5, reason: "ok" } }),
      ev({
        agent: "scrape",
        event: "run.end",
        ts: 40,
        platform: "instagram",
        data: { proposed: 9, reason: "ok" },
      }),
      ev({
        event: "run.end",
        ts: 5,
        run_id: "r0",
        platform: "instagram",
        data: { proposed: 0, reason: "guest_only_no_search" },
      }),
    ];
    const s = lastDiscoveryRun(events, "instagram");
    expect(s?.runId).toBe("r0");
    expect(s?.reason).toBe("guest_only_no_search");
  });

  it("pulls the surface off the matching run.start", () => {
    const events: LogEvent[] = [
      ev({
        event: "run.start",
        ts: 1,
        run_id: "r1",
        platform: "instagram",
        data: { surface: "guest-only" },
      }),
      ev({
        event: "run.end",
        ts: 2,
        run_id: "r1",
        platform: "instagram",
        data: { proposed: 0, reason: "guest_only_no_search" },
      }),
    ];
    expect(lastDiscoveryRun(events, "instagram")?.surface).toBe("guest-only");
  });
});

describe("discoveryReadiness", () => {
  const ig = (present: boolean): SecretStatus => ({
    name: "ig_sessionid",
    env_var: "IG_SESSIONID",
    required: false,
    present,
  });
  const gem = (present: boolean): SecretStatus => ({
    name: "gemini_api_key",
    env_var: "GEMINI_API_KEY",
    required: false,
    present,
  });

  it("defaults to guest-only with no config and no secrets", () => {
    const r = discoveryReadiness(undefined, undefined);
    expect(r.mode).toBe("guest-only");
    expect(r.searchEnabled).toBe(false);
  });

  it("stays guest-only when a session is present but guest_only is still true", () => {
    const r = discoveryReadiness({ guest_only: true }, [ig(true)]);
    expect(r.igPresent).toBe(true);
    expect(r.mode).toBe("guest-only");
    expect(r.searchEnabled).toBe(false);
  });

  it("enables search only when a session is present AND guest_only is false", () => {
    const r = discoveryReadiness({ guest_only: false }, [ig(true)]);
    expect(r.mode).toBe("burner");
    expect(r.searchEnabled).toBe(true);
  });

  it("never lets Gemini enable search", () => {
    const r = discoveryReadiness({ guest_only: false }, [ig(false), gem(true)]);
    expect(r.geminiPresent).toBe(true);
    expect(r.searchEnabled).toBe(false);
    expect(r.mode).toBe("guest-only");
  });
});
