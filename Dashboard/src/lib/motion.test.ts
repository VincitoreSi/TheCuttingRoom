import { describe, it, expect } from "vitest";
import { EASE, sectionMotion } from "./motion";

describe("sectionMotion", () => {
  it("returns initial: false in the reduced-motion branch", () => {
    const m = sectionMotion(0, true);
    expect(m.initial).toBe(false);
  });

  it("returns the entrance offset in the non-reduced branch", () => {
    const m = sectionMotion(0, false);
    expect(m.initial).toEqual({ opacity: 0, y: 8 });
  });

  it("exposes a 4-value cubic-bezier EASE tuple", () => {
    expect(EASE.length).toBe(4);
  });
});
