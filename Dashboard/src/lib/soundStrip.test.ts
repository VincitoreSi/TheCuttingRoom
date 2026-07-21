import { describe, expect, it } from "vitest";
import { parseAudioBlock, soundStripOf } from "./soundStrip";

/** The shape scripts/capture-demo.py::_audio_block() actually writes. */
const BLOCK = [
  "## Audio",
  "",
  "- **Sound:** Nightfall — Veldt",
  "- **Music:** An upbeat synth-pop song with a driving rhythm.",
  "- **Audio type:** trending_sound_led",
  "- **Reuse:** reuse_original",
  "- **Sound page:** https://www.instagram.com/reels/audio/1000000000000001/",
  "- **If not reusable, substitute:** Use an upbeat synth-pop track in the same mood.",
].join("\n");

const PROPOSAL = ["# An example clone recipe title", "", BLOCK, "", "## Shot list"].join("\n");

describe("parseAudioBlock", () => {
  it("reads every bullet of a full block", () => {
    const s = parseAudioBlock(BLOCK);
    expect(s.present).toBe(true);
    expect(s.title).toBe("Nightfall");
    expect(s.artist).toBe("Veldt");
    expect(s.soundLine).toBe("Nightfall — Veldt");
    expect(s.audioType).toBe("trending_sound_led");
    expect(s.reuse).toBe("reuse_original");
    expect(s.reusable).toBe(true);
    expect(s.soundPageUrl).toBe("https://www.instagram.com/reels/audio/1000000000000001/");
    expect(s.musicBrief).toMatch(/^An upbeat synth-pop song/);
    expect(s.substituteBrief).toMatch(/^Use an upbeat synth-pop track/);
  });

  it("returns an empty strip for empty, whitespace-only and nullish input", () => {
    for (const input of [null, undefined, "", "   \n  "]) {
      const s = parseAudioBlock(input);
      expect(s.present).toBe(false);
      expect(s.title).toBeNull();
      expect(s.soundLine).toBeNull();
      expect(s.reusable).toBeNull();
      expect(s.soundPageUrl).toBeNull();
    }
  });

  it("never mistakes the italic no-metadata fallback for a sound title", () => {
    // capture-demo.py emits this when no audio field resolved. It contains an
    // em dash, which a naive "line with a dash" parser would split.
    const block = [
      "## Audio",
      "",
      "- _No audio metadata captured for this clip — pick a trending reusable sound in the same mood._",
    ].join("\n");
    const s = parseAudioBlock(block);
    expect(s.present).toBe(true);
    expect(s.title).toBeNull();
    expect(s.artist).toBeNull();
    expect(s.soundLine).toBeNull();
  });

  it("omits a missing sound page rather than inventing one", () => {
    const s = parseAudioBlock("## Audio\n\n- **Sound:** Nightfall — Veldt");
    expect(s.soundPageUrl).toBeNull();
  });

  it("takes the whole value as the title when there is no artist", () => {
    const s = parseAudioBlock("## Audio\n\n- **Sound:** Nightfall");
    expect(s.title).toBe("Nightfall");
    expect(s.artist).toBeNull();
    expect(s.soundLine).toBe("Nightfall");
  });

  it("never splits a comma-list artist", () => {
    const s = parseAudioBlock(
      "## Audio\n\n- **Sound:** Skyline Drive — Ada Vance, Bo Kerrin, Cy Malto",
    );
    expect(s.title).toBe("Skyline Drive");
    expect(s.artist).toBe("Ada Vance, Bo Kerrin, Cy Malto");
  });

  it("splits on the LAST separator so a title keeps its parens, brackets, quotes and colons", () => {
    const s = parseAudioBlock(
      '## Audio\n\n- **Sound:** Long Way Home (From "Night Shift Vol 2") [Remix] — Ada Vance',
    );
    expect(s.title).toBe('Long Way Home (From "Night Shift Vol 2") [Remix]');
    expect(s.artist).toBe("Ada Vance");

    const colon = parseAudioBlock("## Audio\n\n- **Sound:** Éclat (Pt. 1:1) — Nu Vector");
    expect(colon.title).toBe("Éclat (Pt. 1:1)");
    expect(colon.artist).toBe("Nu Vector");
  });

  it("accepts en-dash and spaced-hyphen separators", () => {
    expect(parseAudioBlock("## Audio\n- **Sound:** Nightfall – Veldt").artist).toBe("Veldt");
    expect(parseAudioBlock("## Audio\n- **Sound:** Nightfall - Veldt").artist).toBe("Veldt");
  });

  it("does not split a bare hyphen inside a word", () => {
    const s = parseAudioBlock("## Audio\n- **Sound:** lo-fi study beats");
    expect(s.title).toBe("lo-fi study beats");
    expect(s.artist).toBeNull();
  });

  it("normalizes CRLF when called directly", () => {
    const s = parseAudioBlock(BLOCK.replace(/\n/g, "\r\n"));
    expect(s.title).toBe("Nightfall");
    expect(s.artist).toBe("Veldt");
    expect(s.reuse).toBe("reuse_original");
  });

  it("derives `reusable` from exact tokens, never includes('reuse')", () => {
    expect(parseAudioBlock("## Audio\n- **Reuse:** reuse_original").reusable).toBe(true);
    expect(parseAudioBlock("## Audio\n- **Reuse:** cannot_reuse").reusable).toBe(false);
    expect(parseAudioBlock("## Audio\n- **Reuse:** do_not_reuse").reusable).toBe(false);
    expect(parseAudioBlock("## Audio\n- **Reuse:** no_reuse").reusable).toBe(false);
    expect(parseAudioBlock("## Audio\n- **Reuse:** unclear").reusable).toBeNull();
    expect(parseAudioBlock("## Audio\n- **Sound:** Nightfall").reusable).toBeNull();
  });

  it("handles a voiceover-led item with no sound to attach", () => {
    const s = parseAudioBlock("## Audio\n\n- **Audio type:** voiceover_led");
    expect(s.present).toBe(true);
    expect(s.audioType).toBe("voiceover_led");
    expect(s.title).toBeNull();
    expect(s.soundLine).toBeNull();
  });

  it("unwraps a link-wrapped or angle-wrapped sound page", () => {
    expect(
      parseAudioBlock("## Audio\n- **Sound page:** [audio](https://example.com/a/1/)").soundPageUrl,
    ).toBe("https://example.com/a/1/");
    expect(
      parseAudioBlock("## Audio\n- **Sound page:** <https://example.com/b/>").soundPageUrl,
    ).toBe("https://example.com/b/");
  });

  it("does not read the Sound page bullet as the Sound bullet", () => {
    const s = parseAudioBlock("## Audio\n- **Sound page:** https://example.com/x/");
    expect(s.title).toBeNull();
    expect(s.soundPageUrl).toBe("https://example.com/x/");
  });
});

describe("soundStripOf", () => {
  it("lifts the Audio section out of a whole proposal", () => {
    const s = soundStripOf(PROPOSAL);
    expect(s.present).toBe(true);
    expect(s.title).toBe("Nightfall");
    expect(s.substituteBrief).toMatch(/^Use an upbeat/);
  });

  it("reports present:false when the proposal has no Audio heading", () => {
    const s = soundStripOf("# Title\n\n## Shot list\n\n### Shot 1\nsomething");
    expect(s.present).toBe(false);
    expect(s.title).toBeNull();
  });

  it("still matches a suffixed heading", () => {
    const s = soundStripOf(
      "# T\n\n## Audio (attach in composer)\n\n- **Sound:** Nightfall — Veldt",
    );
    expect(s.present).toBe(true);
    expect(s.title).toBe("Nightfall");
  });

  it("takes the FIRST Audio section when there are two", () => {
    const s = soundStripOf(
      ["# T", "## Audio", "- **Sound:** First — A", "## Audio", "- **Sound:** Second — B"].join(
        "\n",
      ),
    );
    expect(s.title).toBe("First");
  });

  it("does not throw on nullish input", () => {
    expect(soundStripOf(null).present).toBe(false);
    expect(soundStripOf(undefined).present).toBe(false);
  });
});
