import { describe, expect, it } from "vitest";
import { aspectRatioOf, indexRenders, joinRenders } from "./renderJoin";
import type { Proposal, RenderRecord } from "./types";

function proposal(file: string, updated_at?: number): Proposal {
  return { file, text: `# ${file}`, status: "approved", agent: "similar-content", updated_at };
}

function render(file: string, updated_at: number): RenderRecord {
  const rid = file.replace(/\.md$/, "");
  return {
    render_id: rid,
    platform: "instagram",
    file,
    agent: "similar-content",
    kind: "slideshow",
    updated_at,
    created_at: updated_at,
    video_url: `/renders/instagram/${rid}/reel.mp4?v=${updated_at}`,
  };
}

describe("indexRenders", () => {
  it("keys records by their studio file", () => {
    const m = indexRenders([render("a.md", 10), render("b.md", 20)]);
    expect(m.size).toBe(2);
    expect(m.get("a.md")?.render_id).toBe("a");
  });

  it("keeps the newest record when a file has more than one", () => {
    const m = indexRenders([render("a.md", 30), render("a.md", 10)]);
    expect(m.get("a.md")?.updated_at).toBe(30);
  });

  it("skips rows with no file and tolerates an empty list", () => {
    const orphan = { ...render("a.md", 1), file: "" };
    expect(indexRenders([orphan]).size).toBe(0);
    expect(indexRenders([]).size).toBe(0);
  });
});

describe("joinRenders", () => {
  it("attaches each render to its proposal by exact filename", () => {
    const rows = joinRenders([proposal("a.md")], indexRenders([render("a.md", 5)]));
    expect(rows).toHaveLength(1);
    expect(rows[0].proposal.file).toBe("a.md");
    expect(rows[0].render?.render_id).toBe("a");
  });

  it("leaves render undefined for an approved item nobody has rendered", () => {
    const rows = joinRenders([proposal("a.md")], indexRenders([]));
    expect(rows[0].render).toBeUndefined();
  });

  it("puts rendered items first, regardless of input order or timestamps", () => {
    const rows = joinRenders(
      [proposal("pending.md", 999), proposal("done.md", 1)],
      indexRenders([render("done.md", 2)]),
    );
    expect(rows.map((r) => r.proposal.file)).toEqual(["done.md", "pending.md"]);
  });

  it("orders rendered items by render updated_at, newest first", () => {
    const rows = joinRenders(
      [proposal("old.md"), proposal("new.md")],
      indexRenders([render("old.md", 100), render("new.md", 300)]),
    );
    expect(rows.map((r) => r.proposal.file)).toEqual(["new.md", "old.md"]);
  });

  it("orders un-rendered items by the proposal's own updated_at, newest first", () => {
    const rows = joinRenders([proposal("older.md", 5), proposal("newer.md", 50)], new Map());
    expect(rows.map((r) => r.proposal.file)).toEqual(["newer.md", "older.md"]);
  });

  it("treats a missing timestamp as oldest instead of dropping the row", () => {
    const rows = joinRenders([proposal("nostamp.md"), proposal("stamped.md", 7)], new Map());
    expect(rows.map((r) => r.proposal.file)).toEqual(["stamped.md", "nostamp.md"]);
  });

  it("ignores renders whose file matches no approved proposal", () => {
    const rows = joinRenders([proposal("a.md")], indexRenders([render("ghost.md", 9)]));
    expect(rows).toHaveLength(1);
    expect(rows[0].render).toBeUndefined();
  });

  it("returns an empty list when nothing is approved", () => {
    expect(joinRenders([], indexRenders([render("a.md", 1)]))).toEqual([]);
  });
});

describe("aspectRatioOf", () => {
  it("prefers the record's declared aspect_ratio", () => {
    expect(aspectRatioOf({ aspect_ratio: "9:16", width: 1000, height: 1000 })).toBe("9 / 16");
  });

  it("accepts a slash-separated ratio as well as a colon", () => {
    expect(aspectRatioOf({ aspect_ratio: "4/5" })).toBe("4 / 5");
  });

  it("falls back to pixel width/height when no ratio is declared", () => {
    expect(aspectRatioOf({ width: 1080, height: 1920 })).toBe("1080 / 1920");
  });

  it("falls back to 9:16 when the record carries neither", () => {
    expect(aspectRatioOf({})).toBe("9 / 16");
    expect(aspectRatioOf(undefined)).toBe("9 / 16");
  });

  it("never divides by zero — a zero height falls through to the default", () => {
    expect(aspectRatioOf({ width: 1080, height: 0 })).toBe("9 / 16");
    expect(aspectRatioOf({ aspect_ratio: "9:0", width: 1080, height: 1920 })).toBe("1080 / 1920");
  });

  it("ignores garbage in either field", () => {
    expect(aspectRatioOf({ aspect_ratio: "portrait" })).toBe("9 / 16");
    expect(aspectRatioOf({ aspect_ratio: "  ", width: 1080, height: 1920 })).toBe("1080 / 1920");
    expect(aspectRatioOf({ width: Number.NaN, height: 1920 })).toBe("9 / 16");
    expect(aspectRatioOf({ width: -1080, height: 1920 })).toBe("9 / 16");
    expect(aspectRatioOf({ width: null, height: null })).toBe("9 / 16");
  });
});
