import type { Proposal, RenderRecord } from "./types";

/** One row of the Renders tab: an approved studio item, plus its render if the
    producer has already made one. `render.file` joins to `Proposal.file` exactly. */
export interface RenderRow {
  proposal: Proposal;
  render?: RenderRecord;
}

/** Reels are 9:16 unless a record says otherwise — the shape the player box is
    sized to when a record is missing, still rendering, or malformed. */
const FALLBACK_ASPECT = "9 / 16";

/** Both sides of a ratio must be real, positive numbers for it to be usable as
    a CSS `aspect-ratio`; a zero height would otherwise collapse the box. */
function ratio(w: unknown, h: unknown): string | null {
  const width = Number(w);
  const height = Number(h);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  if (width <= 0 || height <= 0) return null;
  return `${width} / ${height}`;
}

/**
 * The CSS `aspect-ratio` to size a render's player box with, so the video fills
 * its frame edge to edge instead of letterboxing inside a mis-shaped box.
 *
 * Prefers the record's declared `aspect_ratio` ("9:16", "9/16"), then its pixel
 * `width`/`height`, then 9:16. Garbage on either field falls through rather
 * than producing an invalid — or zero-divided — value.
 */
export function aspectRatioOf(
  render?: Pick<RenderRecord, "aspect_ratio" | "width" | "height">,
): string {
  if (!render) return FALLBACK_ASPECT;

  const declared = typeof render.aspect_ratio === "string" ? render.aspect_ratio.trim() : "";
  if (declared) {
    const parts = declared.split(/[:/]/);
    if (parts.length === 2) {
      const parsed = ratio(parts[0], parts[1]);
      if (parsed) return parsed;
    }
  }

  return ratio(render.width, render.height) ?? FALLBACK_ASPECT;
}

/** Index render records by the studio file they came from. Later records win,
    which matches the hub's newest-first list order being collapsed one-per-item. */
export function indexRenders(records: RenderRecord[]): Map<string, RenderRecord> {
  const m = new Map<string, RenderRecord>();
  for (const r of records) {
    if (!r?.file) continue;
    const prev = m.get(r.file);
    if (!prev || (r.updated_at ?? 0) >= (prev.updated_at ?? 0)) m.set(r.file, r);
  }
  return m;
}

/**
 * Join approved proposals to their renders. Rendered items come first (they are
 * the ones an operator can actually post right now), then everything still
 * awaiting a render; within each group, newest activity first.
 *
 * Pure — no fetching, no React — so the ordering rule is unit-testable.
 */
export function joinRenders(approved: Proposal[], byFile: Map<string, RenderRecord>): RenderRow[] {
  const rows: RenderRow[] = approved.map((proposal) => {
    const render = byFile.get(proposal.file);
    return render ? { proposal, render } : { proposal };
  });

  const stamp = (row: RenderRow): number =>
    row.render?.updated_at ?? row.render?.created_at ?? row.proposal.updated_at ?? 0;

  return rows.sort((a, b) => {
    const rendered = Number(!!b.render) - Number(!!a.render);
    if (rendered !== 0) return rendered;
    return stamp(b) - stamp(a);
  });
}
