import { describe, expect, it } from "vitest";
import { plural } from "./boardCounts";

describe("plural", () => {
  it("agrees with the count", () => {
    expect(plural(0, "page")).toBe("0 pages");
    expect(plural(1, "page")).toBe("1 page");
    expect(plural(2, "page")).toBe("2 pages");
    expect(plural(250, "reel")).toBe("250 reels");
  });

  // The bug this file exists for. PlatformSummary.watchlist is typed `number`, so nothing
  // in the type system objects — but a hub process that outlived a `git pull` serves the
  // OLDER response from memory, where the field is simply absent. The Board rendered
  // "undefined pages", which points nowhere near the cause. A count that never arrived is
  // unknown, and unknown reads as an em dash everywhere else on the board.
  it("reads as unknown when the field never arrived", () => {
    expect(plural(undefined, "page")).toBe("—");
    expect(plural(null, "reel")).toBe("—");
  });

  it("reads as unknown for a non-number that slipped through the wire", () => {
    expect(plural(NaN, "page")).toBe("—");
    expect(plural(Infinity, "page")).toBe("—");
    expect(plural("3" as unknown as number, "page")).toBe("—");
  });
});
