import { describe, expect, it } from "vitest";
import { deriveCorpusEmpty } from "./corpusEmpty";

const at = (over: Partial<Parameters<typeof deriveCorpusEmpty>[0]> = {}) =>
  deriveCorpusEmpty({ total: 0, filtered: 0, scraped: false, watchlist: 1, ...over });

describe("deriveCorpusEmpty", () => {
  it("renders nothing when there are reels to show", () => {
    expect(at({ total: 40, filtered: 40, scraped: true })).toBeNull();
  });

  it("blames the filter only when the corpus actually has reels in it", () => {
    const e = at({ total: 40, filtered: 0, scraped: true })!;
    expect(e.title).toBe("No reels match");
    expect(e.hint).toMatch(/filter/i);
    expect(e.run).toBeUndefined(); // running a stage would not help
  });

  it("a finished scrape with no analyze points at Analyze, never back at Scrape", () => {
    // The regression this file exists for: 250 reels on disk, an empty grid, and copy
    // telling the user to run the stage they had just watched finish.
    const e = at({ scraped: true })!;
    expect(e.run).toBe("analyze");
    expect(e.hint).not.toMatch(/scrape/i);
  });

  it("a watched-but-unscraped platform points at Scrape", () => {
    expect(at({ watchlist: 2 })!.run).toBe("scrape");
  });

  it("an empty watchlist sends you to Config, not to a Scrape that cannot work", () => {
    // The hub refuses scrape with no handles, and the onboarding checklist has always
    // said adding one is step one. Offering Run here contradicted both.
    const e = at({ watchlist: 0 })!;
    expect(e.run).toBeUndefined();
    expect(e.goto).toBe("config");
    expect(e.hint).toMatch(/watchlist/i);
  });
});
