import { describe, expect, it } from "vitest";
import {
  CASCADE_LIMITS,
  FUNNEL_ROWS,
  REELS_PER_CREATOR,
  clampCascadeField,
  funnelProjection,
  reelsPerCreator,
  withReelsPerCreator,
} from "./cascadeFunnel";

const FULL = {
  scrape_count: 250,
  analyze_pct: 100,
  media_pct: 60,
  blueprint_pct: 20,
  propose_pct: 20,
};

describe("funnelProjection", () => {
  it("walks the defaults down the chain", () => {
    expect(funnelProjection(FULL)).toEqual({
      scrape: 250,
      analyze: 250,
      media: 150,
      blueprint: 30,
      propose: 6,
    });
  });

  it("never widens — every row is <= the row above it", () => {
    // The whole point of moving off absolute per-boundary thresholds. Because each
    // percentage is capped at 100, no configuration can make a later stage move more
    // than the stage feeding it.
    for (const s of [1, 7, 250, 4999]) {
      for (const p of [1, 33, 100]) {
        const v = funnelProjection({
          scrape_count: s,
          analyze_pct: p,
          media_pct: p,
          blueprint_pct: p,
          propose_pct: p,
        });
        const rows = FUNNEL_ROWS.map((r) => v[r.key]);
        for (let i = 1; i < rows.length; i++) expect(rows[i]).toBeLessThanOrEqual(rows[i - 1]);
      }
    }
  });

  it("passes the whole batch through at 100% everywhere", () => {
    const v = funnelProjection({
      scrape_count: 40,
      analyze_pct: 100,
      media_pct: 100,
      blueprint_pct: 100,
      propose_pct: 100,
    });
    expect(v).toEqual({ scrape: 40, analyze: 40, media: 40, blueprint: 40, propose: 40 });
  });

  it("rounds each row before feeding the next", () => {
    // 3 * 50% = 1.5 → 2, and the row below takes its percentage of 2, not of 1.5.
    const v = funnelProjection({ ...FULL, scrape_count: 3, analyze_pct: 50, media_pct: 50 });
    expect(v.analyze).toBe(2);
    expect(v.media).toBe(1);
  });

  // The bug this file exists for, in the same shape as boardCounts.test.ts. A hub started
  // before the funnel landed serves the OLDER row from memory, with no `*_pct` fields at
  // all. `undefined / 100` is NaN, and NaN spreads to every row below — the card read
  // "≤NaN clips", which points nowhere near a stale process.
  it("falls back per field when the row predates the funnel", () => {
    expect(funnelProjection({ scrape_count: 250 })).toEqual(funnelProjection(FULL));
    expect(funnelProjection({})).toEqual(funnelProjection(FULL));
    expect(funnelProjection(undefined)).toEqual(funnelProjection(FULL));
    expect(funnelProjection(null)).toEqual(funnelProjection(FULL));
  });

  it("clamps a row a hand-edited config file put out of range", () => {
    const v = funnelProjection({ ...FULL, scrape_count: 99999, analyze_pct: 0 });
    expect(v.scrape).toBe(5000);
    expect(v.analyze).toBe(50); // 1%, the floor — not 0, which would stall the chain
  });
});

describe("reelsPerCreator", () => {
  // The bug this exists for (ISSUE C): the Scrape row bound to the cascade's scrape_count,
  // whose fallback is 250 — a number the scraper never used. The real per-creator scrape
  // size is niche_config.reels_per_creator, and scrape.py's own default when it is missing
  // is 100, not 250.
  it("reads the configured reels_per_creator", () => {
    expect(reelsPerCreator(100)).toBe(100);
    expect(reelsPerCreator(42)).toBe(42);
  });

  it("falls back to 100, NOT the cascade's 250 batch default", () => {
    // a niche_config that predates the field, or an emptied input box mid-edit
    expect(reelsPerCreator(undefined)).toBe(100);
    expect(reelsPerCreator(null)).toBe(100);
    expect(reelsPerCreator("")).toBe(100);
    expect(reelsPerCreator(NaN)).toBe(100);
    expect(REELS_PER_CREATOR.fallback).toBe(100);
    expect(reelsPerCreator(undefined)).not.toBe(CASCADE_LIMITS.scrape_count.fallback);
  });

  it("clamps to its own bounds and rounds", () => {
    expect(reelsPerCreator(0)).toBe(REELS_PER_CREATOR.min);
    expect(reelsPerCreator(999999)).toBe(REELS_PER_CREATOR.max);
    expect(reelsPerCreator(99.4)).toBe(99);
  });

  it("anchors the funnel projection off the real scrape size", () => {
    // 100 reels/creator down the default percentages, NOT 250.
    const v = funnelProjection({
      scrape_count: reelsPerCreator(100),
      analyze_pct: 100,
      media_pct: 60,
      blueprint_pct: 20,
      propose_pct: 20,
    });
    expect(v.scrape).toBe(100);
    expect(v.analyze).toBe(100);
    expect(v.media).toBe(60);
  });
});

describe("withReelsPerCreator", () => {
  // The write-through: editing the Scrape row PUTs the whole niche_config back (the hub
  // overwrites the file wholesale), so the edit must set reels_per_creator without dropping
  // the weights/tiers/keywords that share the file.
  it("sets reels_per_creator on the config it will PUT", () => {
    const cfg = { niche: "linen", virality: { weights: { velocity: 0.5 } } };
    expect(withReelsPerCreator(cfg, 120)).toEqual({
      niche: "linen",
      virality: { weights: { velocity: 0.5 } },
      reels_per_creator: 120,
    });
  });

  it("clamps the value it writes and does not mutate the input", () => {
    const cfg = { reels_per_creator: 100, niche: "x" };
    const next = withReelsPerCreator(cfg, 99999);
    expect(next.reels_per_creator).toBe(REELS_PER_CREATOR.max);
    expect(cfg.reels_per_creator).toBe(100); // original untouched
  });
});

describe("clampCascadeField", () => {
  it("keeps a value that is already in range", () => {
    expect(clampCascadeField("scrape_count", 250)).toBe(250);
    expect(clampCascadeField("propose_count", 7)).toBe(7);
  });

  it("clamps to each field's own bounds", () => {
    expect(clampCascadeField("scrape_count", 0)).toBe(1);
    expect(clampCascadeField("scrape_count", 9001)).toBe(5000);
    expect(clampCascadeField("media_pct", 101)).toBe(100);
    expect(clampCascadeField("propose_count", 99)).toBe(25);
  });

  it("rounds, because every one of these fields is a whole number on the wire", () => {
    expect(clampCascadeField("analyze_pct", 66.6)).toBe(67);
  });

  it("reads a value that never arrived as the default", () => {
    for (const field of Object.keys(CASCADE_LIMITS) as (keyof typeof CASCADE_LIMITS)[]) {
      expect(clampCascadeField(field, undefined)).toBe(CASCADE_LIMITS[field].fallback);
      expect(clampCascadeField(field, NaN)).toBe(CASCADE_LIMITS[field].fallback);
      expect(clampCascadeField(field, Infinity)).toBe(CASCADE_LIMITS[field].fallback);
      expect(clampCascadeField(field, "")).toBe(CASCADE_LIMITS[field].fallback);
    }
  });
});
