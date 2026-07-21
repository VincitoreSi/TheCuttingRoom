/* Pure reader over a proposal's `## Audio` block.
 *
 * Instagram has no post API, so every render ships silent and the operator
 * attaches the sound by hand. The only machine-readable source for WHICH sound
 * is the markdown bullet list that scripts/capture-demo.py::_audio_block() writes
 * into the proposal — every bullet independently optional. This module turns
 * that block into the four values a compact card strip needs (title, artist,
 * reuse verdict, sound page) without ever guessing.
 *
 * Node-safe and React-free so the grammar is unit-testable under the
 * `src/**\/*.test.ts` vitest glob.
 */

import { extractSection } from "./proposalMarkdown";

export interface SoundStrip {
  /** An `## Audio` section existed at all — distinct from "it named a sound". */
  present: boolean;
  title: string | null;
  artist: string | null;
  /** The original joined value ("Nightfall — Veldt"); the CopyButton payload,
      because that whole string is what gets pasted into IG's sound search. */
  soundLine: string | null;
  audioType: string | null;
  /** The raw token, e.g. "reuse_original" — kept for display/labelling. */
  reuse: string | null;
  /** true / false / null when the recipe does not say. Never inferred. */
  reusable: boolean | null;
  /** NOT passed through safeUrl — validate at render time, at the href. */
  soundPageUrl: string | null;
  musicBrief: string | null;
  substituteBrief: string | null;
}

const EMPTY: SoundStrip = {
  present: false,
  title: null,
  artist: null,
  soundLine: null,
  audioType: null,
  reuse: null,
  reusable: null,
  soundPageUrl: null,
  musicBrief: null,
  substituteBrief: null,
};

/** A bullet whose label matches, e.g. `- **Sound page:** https://…`. Matching by
    LABEL is the whole point: the generator also emits an italic no-metadata
    fallback line that contains an em dash, and treating "a line with an em dash"
    as a sound title would mistake it for one. */
function bullet(block: string, label: string): string | null {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  const re = new RegExp(`^\\s*[-*]\\s*\\*{0,2}\\s*${escaped}\\s*:\\s*\\*{0,2}\\s*(.+?)\\s*$`, "im");
  const m = block.match(re);
  if (!m) return null;
  const value = clean(m[1]);
  return value || null;
}

/** Strip the emphasis/code markers the generator may leave around a value. */
function clean(s: string): string {
  return s
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/^_+|_+$/g, "")
    .trim();
}

/** Unwrap `[text](url)` and `<url>` down to the bare URL. */
function bareUrl(s: string): string {
  const md = s.match(/\[[^\]]*\]\(([^)\s]+)\)/);
  if (md) return md[1].trim();
  const angled = s.match(/^<([^>]+)>$/);
  if (angled) return angled[1].trim();
  return s;
}

/** Split "Title — Artist" on the LAST spaced separator.
 *
 * The generator appends the artist last, and titles legitimately contain
 * parens, brackets, quotes and colons (`Long Way Home (From "Night Shift Vol 2")
 * [Remix] — Ada Vance, Bo Kerrin`). Splitting on the FIRST
 * separator would cut a title in half. The separator must be spaced, so a bare
 * hyphen inside a word ("lo-fi") never splits, and the artist is never split on
 * its internal commas.
 */
function splitSound(value: string): { title: string; artist: string | null } {
  const re = / [—–-] /g;
  let at = -1;
  let m: RegExpExecArray | null;
  while ((m = re.exec(value)) !== null) at = m.index;
  if (at === -1) return { title: value, artist: null };
  const title = value.slice(0, at).trim();
  const artist = value.slice(at + 3).trim();
  if (!title || !artist) return { title: value, artist: null };
  return { title, artist };
}

/** Exact-token reuse verdict.
 *
 * Deliberately NOT `reuse.includes("reuse")` — "cannot_reuse" contains "reuse",
 * which would paint a licensed sound as reusable on the one signal that decides
 * whether the operator can attach the original at all.
 */
function reusableFrom(reuse: string | null): boolean | null {
  if (!reuse) return null;
  const token = reuse.trim().toLowerCase();
  if (token === "reuse_original") return true;
  if (/^(cannot|do_not|no)_?reuse/.test(token)) return false;
  return null;
}

/** Parse an already-lifted `## Audio` block. */
export function parseAudioBlock(block: string | null | undefined): SoundStrip {
  if (!block) return { ...EMPTY };
  const text = block.replace(/\r\n/g, "\n");
  if (!text.trim()) return { ...EMPTY };

  const soundLine = bullet(text, "Sound");
  const split = soundLine ? splitSound(soundLine) : null;
  const reuse = bullet(text, "Reuse");
  const page = bullet(text, "Sound page");

  return {
    present: true,
    title: split ? split.title : null,
    artist: split ? split.artist : null,
    soundLine,
    audioType: bullet(text, "Audio type"),
    reuse,
    reusable: reusableFrom(reuse),
    soundPageUrl: page ? bareUrl(page) : null,
    musicBrief: bullet(text, "Music"),
    substituteBrief: bullet(text, "If not reusable, substitute"),
  };
}

/** Convenience over a whole proposal body — composes over the already-tested
    `extractSection` so heading-boundary logic lives in exactly one place. */
export function soundStripOf(proposalText: string | null | undefined): SoundStrip {
  if (!proposalText) return { ...EMPTY };
  return parseAudioBlock(extractSection(proposalText, "Audio"));
}
