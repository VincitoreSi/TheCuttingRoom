import { describe, expect, it } from "vitest";
import { deriveCorpusEmpty } from "./corpusEmpty";

describe("deriveCorpusEmpty", () => {
  it("renders nothing when there are reels to show", () => {
    expect(deriveCorpusEmpty({ total: 40, filtered: 40, scraped: true })).toBeNull();
  });

  it("blames the filter only when the corpus actually has reels in it", () => {
    const e = deriveCorpusEmpty({ total: 40, filtered: 0, scraped: true })!;
    expect(e.title).toBe("No reels match");
    expect(e.hint).toMatch(/filter/i);
    expect(e.run).toBeUndefined(); // running a stage would not help
  });

  it("a finished scrape with no analyze points at Analyze, never back at Scrape", () => {
    // The regression this file exists for: 250 reels on disk, an empty grid, and copy
    // telling the user to run the stage they had just watched finish.
    const e = deriveCorpusEmpty({ total: 0, filtered: 0, scraped: true })!;
    expect(e.run).toBe("analyze");
    expect(e.hint).not.toMatch(/scrape/i);
  });

  it("a truly untouched platform points at Scrape", () => {
    const e = deriveCorpusEmpty({ total: 0, filtered: 0, scraped: false })!;
    expect(e.run).toBe("scrape");
  });
});
