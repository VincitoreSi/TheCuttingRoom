import { describe, expect, it } from "vitest";
import { mediaGateMinScore, orderedTiers, tierThreshold } from "./mediaGate";
import type { Tier } from "./types";

const TIERS: Tier[] = [
  { label: "Viral", min_score: 85 },
  { label: "High", min_score: 70 },
  { label: "Above Average", min_score: 50 },
  { label: "Normal", min_score: 0 },
];

describe("tierThreshold", () => {
  it("reads the configured cutoff for a label", () => {
    expect(tierThreshold(TIERS, "Viral")).toBe(85);
    expect(tierThreshold(TIERS, "High")).toBe(70);
  });
  it("is null for an unknown or missing label", () => {
    expect(tierThreshold(TIERS, "nope")).toBeNull();
    expect(tierThreshold(TIERS, null)).toBeNull();
  });
});

describe("mediaGateMinScore", () => {
  it("maps min_tier to the tier's score", () => {
    expect(mediaGateMinScore(TIERS, { min_tier: "High" })).toBe(70);
  });
  it("lets an explicit min_score override the tier", () => {
    expect(mediaGateMinScore(TIERS, { min_tier: "Viral", min_score: 60 })).toBe(60);
  });
  it("is null with no filter", () => {
    expect(mediaGateMinScore(TIERS, undefined)).toBeNull();
    expect(mediaGateMinScore(TIERS, {})).toBeNull();
  });
});

describe("orderedTiers", () => {
  it("orders highest tier first regardless of input order", () => {
    const shuffled: Tier[] = [TIERS[3], TIERS[1], TIERS[0], TIERS[2]];
    expect(orderedTiers(shuffled).map((t) => t.label)).toEqual([
      "Viral",
      "High",
      "Above Average",
      "Normal",
    ]);
  });
});
