import { describe, it, expect } from "vitest";
import { safeUrl } from "./url";

describe("safeUrl", () => {
  it("allows http(s), mailto, and site-relative", () => {
    expect(safeUrl("https://instagram.com/reel/x")).toBe("https://instagram.com/reel/x");
    expect(safeUrl("http://127.0.0.1:8787/media/a.jpg")).toBe("http://127.0.0.1:8787/media/a.jpg");
    expect(safeUrl("mailto:a@b.com")).toBe("mailto:a@b.com");
    expect(safeUrl("/media/instagram/123.jpg")).toBe("/media/instagram/123.jpg");
  });

  it("rejects script-bearing and other unsafe schemes", () => {
    expect(safeUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeUrl("JavaScript:alert(1)")).toBeUndefined();
    expect(safeUrl("data:text/html,<script>alert(1)</script>")).toBeUndefined();
    expect(safeUrl("vbscript:msgbox(1)")).toBeUndefined();
    expect(safeUrl("file:///etc/passwd")).toBeUndefined();
  });

  it("defeats the leading-whitespace scheme bypass", () => {
    expect(safeUrl("\tjavascript:alert(1)")).toBeUndefined();
    expect(safeUrl("\n javascript:alert(1)")).toBeUndefined();
    expect(safeUrl("  \r\njavascript:alert(1)")).toBeUndefined();
  });

  it("returns undefined for empty/nullish input", () => {
    expect(safeUrl(undefined)).toBeUndefined();
    expect(safeUrl(null)).toBeUndefined();
    expect(safeUrl("")).toBeUndefined();
  });
});
