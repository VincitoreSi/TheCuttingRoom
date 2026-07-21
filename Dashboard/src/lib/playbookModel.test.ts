import { describe, it, expect } from "vitest";
import {
  confidence,
  rankFactors,
  winners,
  drags,
  ladder,
  featureBest,
  formula,
  fieldExtent,
  partitionInsights,
  relativeTime,
  SOLID_N,
  THIN_N,
} from "./playbookModel";
import type { Factor, FactorsResponse, Insight } from "./types";

function f(partial: Partial<Factor>): Factor {
  return { feature: "duration", bucket: "48-60s", n: 100, mean_score: 55, lift: 5, ...partial };
}

describe("confidence", () => {
  it("is solid at and above SOLID_N", () => {
    expect(confidence(SOLID_N)).toBe("solid");
    expect(confidence(519)).toBe("solid");
  });
  it("is thin between THIN_N and SOLID_N", () => {
    expect(confidence(THIN_N)).toBe("thin");
    expect(confidence(SOLID_N - 1)).toBe("thin");
  });
  it("is noise below THIN_N", () => {
    expect(confidence(THIN_N - 1)).toBe("noise");
    expect(confidence(15)).toBe("noise");
    expect(confidence(0)).toBe("noise");
  });
});

describe("rankFactors", () => {
  it("drops n<=0 rows", () => {
    const out = rankFactors(
      [f({ n: 0, bucket: "a" }), f({ n: -5, bucket: "b" }), f({ n: 90, bucket: "c" })],
      "up",
    );
    expect(out).toHaveLength(1);
    expect(out[0].bucket).toBe("c");
  });
  it("drops rows with no computable bucket string", () => {
    const out = rankFactors(
      [f({ n: 90, bucket: "" }), f({ n: 90, bucket: "   " }), f({ n: 90, bucket: "ok" })],
      "up",
    );
    expect(out).toHaveLength(1);
    expect(out[0].bucket).toBe("ok");
  });
  it("orders confident rows before noise rows (up)", () => {
    // noise row has a bigger lift but must still sort after the confident one
    const out = rankFactors(
      [f({ n: 15, lift: 20, bucket: "noisy-big" }), f({ n: 100, lift: 3, bucket: "solid-small" })],
      "up",
    );
    expect(out.map((r) => r.bucket)).toEqual(["solid-small", "noisy-big"]);
  });
  it("sorts by lift desc for up, asc for down", () => {
    const list = [f({ n: 100, lift: 2, bucket: "lo" }), f({ n: 100, lift: 8, bucket: "hi" })];
    expect(rankFactors(list, "up").map((r) => r.bucket)).toEqual(["hi", "lo"]);
    expect(rankFactors(list, "down").map((r) => r.bucket)).toEqual(["lo", "hi"]);
  });
  it("tie-breaks equal lift on n desc", () => {
    const list = [f({ n: 90, lift: 5, bucket: "small" }), f({ n: 300, lift: 5, bucket: "big" })];
    expect(rankFactors(list, "up").map((r) => r.bucket)).toEqual(["big", "small"]);
  });
});

describe("winners / drags", () => {
  const resp: FactorsResponse = {
    baseline: 50,
    all: [],
    winners: [f({ n: 100, lift: 6, bucket: "w-solid" }), f({ n: 15, lift: 9, bucket: "w-noise" })],
    losers: [f({ n: 200, lift: -8, bucket: "l-solid" }), f({ n: 20, lift: -3, bucket: "l-noise" })],
  };
  it("splits winners into confident rows and noise footnote", () => {
    const w = winners(resp);
    expect(w.rows.map((r) => r.bucket)).toEqual(["w-solid"]);
    expect(w.noise.map((r) => r.bucket)).toEqual(["w-noise"]);
  });
  it("splits drags from f.losers, most-negative confident first", () => {
    const d = drags(resp);
    expect(d.rows.map((r) => r.bucket)).toEqual(["l-solid"]);
    expect(d.noise.map((r) => r.bucket)).toEqual(["l-noise"]);
  });
  it("returns empty structure for undefined", () => {
    expect(winners(undefined)).toEqual({ rows: [], noise: [] });
    expect(drags(undefined)).toEqual({ rows: [], noise: [] });
  });
});

describe("ladder", () => {
  it("excludes noise, sorts by lift desc, labels feature · bucket", () => {
    const resp: FactorsResponse = {
      baseline: 50,
      all: [
        f({ feature: "hashtags", bucket: "3-5", n: 100, lift: 4 }),
        f({ feature: "duration", bucket: "48-60s", n: 100, lift: 9 }),
        f({ feature: "caption_length", bucket: "none", n: 10, lift: -8 }), // noise, excluded
      ],
      winners: [],
      losers: [],
    };
    const out = ladder(resp);
    expect(out.map((r) => r.label)).toEqual(["duration · 48-60s", "hashtags · 3-5"]);
  });
});

describe("featureBest & formula", () => {
  const resp: FactorsResponse = {
    baseline: 50,
    all: [],
    winners: [
      f({ feature: "duration", bucket: "48-60s", n: 100, lift: 9 }),
      f({ feature: "duration", bucket: "30-40s", n: 100, lift: 4 }),
      f({ feature: "hashtags", bucket: "3-5", n: 100, lift: 6 }),
      f({ feature: "posting_time", bucket: "afternoon", n: 15, lift: 12 }), // noise, ignored
    ],
    losers: [],
  };
  it("picks the highest-lift confident winner per feature in fixed order", () => {
    const fb = featureBest(resp);
    expect(fb.map((b) => `${b.feature}:${b.bucket}`)).toEqual(["duration:48-60s", "hashtags:3-5"]);
  });
  it("formula joins confident buckets when >=2 features", () => {
    expect(formula(resp)).toBe("48-60s · 3-5");
  });
  it("formula returns '' with fewer than 2 confident features", () => {
    const one: FactorsResponse = {
      baseline: 50,
      all: [],
      winners: [f({ feature: "duration", bucket: "48-60s", n: 100, lift: 9 })],
      losers: [],
    };
    expect(formula(one)).toBe("");
    expect(formula(undefined)).toBe("");
  });
});

describe("fieldExtent", () => {
  it("returns min/max lift over rankable all[]", () => {
    const resp: FactorsResponse = {
      baseline: 50,
      all: [f({ lift: -8, n: 100 }), f({ lift: 9, n: 100 }), f({ lift: 2, n: 0 })],
      winners: [],
      losers: [],
    };
    expect(fieldExtent(resp)).toEqual({ min: -8, max: 9 });
  });
  it("returns neutral 0,0 for empty/undefined", () => {
    expect(fieldExtent(undefined)).toEqual({ min: 0, max: 0 });
  });
});

describe("partitionInsights", () => {
  const ins: Insight[] = [
    { ts: 100, platform: "instagram", kind: "method", text: "m1", tags: [] },
    { ts: 300, platform: "shared", kind: "finding", text: "fnew", tags: [] },
    { ts: 200, platform: "shared", kind: "finding", text: "fold", tags: [] },
    { ts: 50, platform: "instagram", kind: "negative", text: "bad", tags: [] },
    { ts: 60, platform: "instagram", kind: "approved", text: "tagged-ap", tags: ["antipattern"] },
    { ts: 70, platform: "youtube", kind: "method", text: "other-platform", tags: [] },
  ];
  it("filters to current platform or shared", () => {
    const p = partitionInsights(ins, "instagram");
    const allText = [...p.methods, ...p.findings, ...p.antipatterns].map((i) => i.text);
    expect(allText).not.toContain("other-platform");
  });
  it("classifies methods, findings, antipatterns (kind + tag)", () => {
    const p = partitionInsights(ins, "instagram");
    expect(p.methods.map((i) => i.text)).toEqual(["m1"]);
    expect(p.antipatterns.map((i) => i.text).sort()).toEqual(["bad", "tagged-ap"]);
    expect(p.findings.map((i) => i.text)).toContain("fnew");
  });
  it("sorts each band ts desc", () => {
    const p = partitionInsights(ins, "instagram");
    expect(p.findings.map((i) => i.text)).toEqual(["fnew", "fold"]);
  });
  it("handles undefined input", () => {
    expect(partitionInsights(undefined, "instagram")).toEqual({
      methods: [],
      findings: [],
      antipatterns: [],
    });
  });
});

describe("relativeTime", () => {
  const now = 1_000_000;
  it("reads just now under a minute (and for future ts)", () => {
    expect(relativeTime(now - 10, now)).toBe("just now");
    expect(relativeTime(now + 100, now)).toBe("just now");
  });
  it("scales minutes / hours / days / weeks", () => {
    expect(relativeTime(now - 120, now)).toBe("2m ago");
    expect(relativeTime(now - 3 * 3600, now)).toBe("3h ago");
    expect(relativeTime(now - 2 * 86400, now)).toBe("2d ago");
    expect(relativeTime(now - 14 * 86400, now)).toBe("2w ago");
  });
});
