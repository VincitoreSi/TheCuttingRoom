/* The cascade card's arithmetic — the percentage funnel, on its own.

   The cascade is anchored by ONE absolute number (`scrape_count`, the batch size that
   starts a cycle) and every stage after it takes a percentage of what the stage above it
   produced. The hub derives its `steps` map from exactly these five fields, so what this
   card projects and what the daemon fires on come out of one shape — and because every
   percentage is <= 100, each derived step is >= the one before it. Monotonicity is
   structural now; it can no longer be typed into the form.

   Split out of ConfigView for the same reason boardCounts was: these numbers arrive
   straight off the wire, and TypeScript cannot police the wire. A hub process that
   predates the funnel serves a row with no `analyze_pct` in it at all — `undefined / 100`
   is NaN, and the card renders "≤NaN scored", which points nowhere near the cause. Every
   field is coerced here, once, and a field that never arrived reads as its default. */

/** The five funnel rows, in pipeline order. `scrape` is the anchor and is an absolute
    count; the four below it are percentages of the row above, and they are exactly the
    hub's CASCADE_STAGES. `render` is not here and must never be — nothing in this file
    names a stage the cascade is allowed to launch. */
export const FUNNEL_ROWS = [
  { key: "scrape", field: "scrape_count", label: "Scrape", unit: "reels" },
  { key: "analyze", field: "analyze_pct", label: "Analyze", unit: "scored" },
  { key: "media", field: "media_pct", label: "Media", unit: "clips" },
  { key: "blueprint", field: "blueprint_pct", label: "Blueprint", unit: "blueprints" },
  { key: "propose", field: "propose_pct", label: "Propose", unit: "recipes" },
] as const;

export type FunnelKey = (typeof FUNNEL_ROWS)[number]["key"];

/** Every number this card can PUT, with the hub's own bounds and its default. Clamping
    here as well as there is deliberate: a silent server-side clamp means the number the
    operator reads back is not the number they typed, so they type it again. */
export const CASCADE_LIMITS = {
  scrape_count: { min: 1, max: 5000, fallback: 250 },
  analyze_pct: { min: 1, max: 100, fallback: 100 },
  media_pct: { min: 1, max: 100, fallback: 60 },
  blueprint_pct: { min: 1, max: 100, fallback: 20 },
  propose_pct: { min: 1, max: 100, fallback: 20 },
  propose_count: { min: 1, max: 25, fallback: 5 },
  blueprint_top_pct: { min: 1, max: 100, fallback: 20 },
} as const;

export type CascadeField = keyof typeof CASCADE_LIMITS;

/** The real per-creator scrape size — niche_config.json's `reels_per_creator`, which is what
    scrape.py passes as `--limit`. This, not the cascade's `scrape_count`, is the number the
    scraper actually used, so it anchors the funnel and is what the Scrape row edits. Its
    fallback is 100 — scrape.py's own default when the field is absent — deliberately NOT the
    cascade's misleading 250 batch default. Bounds match `scrape_count` so the anchor and its
    input agree. */
export const REELS_PER_CREATOR = { min: 1, max: 5000, fallback: 100 } as const;

/** Coerce a `reels_per_creator` value into range, reading anything that never arrived (an
    older config, a cleared input box) as the 100 default rather than propagating NaN. Mirrors
    clampCascadeField, kept separate because it is a niche_config field, not a cascade one. */
export function reelsPerCreator(raw: unknown): number {
  const { min, max, fallback } = REELS_PER_CREATOR;
  // null/undefined is an absent field, "" is an emptied box — both read as the default, not
  // as Number(null|"")===0 clamped to the floor, which would misreport a mid-edit box as 1.
  const blank = raw == null || (typeof raw === "string" && raw.trim() === "");
  const n = typeof raw === "number" ? raw : Number(raw);
  if (blank || !Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

/** The write-through payload for a Scrape-row edit. The hub overwrites niche_config.json
    wholesale on PUT /api/config, so an edit has to carry the WHOLE config with just
    `reels_per_creator` replaced — dropping the weights/tiers/keywords that share the file
    would silently reset the scoring engine. Returns a fresh object; the input is untouched. */
export function withReelsPerCreator<T extends Record<string, unknown>>(config: T, n: number): T {
  return { ...config, reels_per_creator: reelsPerCreator(n) };
}

/** Coerce one field into its own range. Anything that is not a finite number never
    arrived (an older hub, a cleared input box) and reads as the default rather than
    propagating NaN into every row below it. */
export function clampCascadeField(field: CascadeField, raw: unknown): number {
  const { min, max, fallback } = CASCADE_LIMITS[field];
  // "" is Number 0, which would clamp to the floor — but an emptied input box is someone
  // mid-edit, not someone asking for 1. Treat it as absent, like a field off an older hub.
  const blank = typeof raw === "string" && raw.trim() === "";
  const n = typeof raw === "number" ? raw : Number(raw);
  if (blank || !Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

export type FunnelProjection = Record<FunnelKey, number>;

/** What one cascade cycle is expected to move, row by row.

    Only `scrape` is a promise — it is the batch size. Everything below it is a ceiling
    (not every scraped reel scores, not every scored reel gets media), which is why the
    card prefixes those rows with "≤". `propose` is a ceiling for a second reason too:
    `propose_count` decides how many recipes ONE firing actually publishes. */
export function funnelProjection(
  row?: Partial<Record<CascadeField, number>> | null,
): FunnelProjection {
  const pct = (field: CascadeField, of: number) =>
    Math.round((of * clampCascadeField(field, row?.[field])) / 100);
  const scrape = clampCascadeField("scrape_count", row?.scrape_count);
  const analyze = pct("analyze_pct", scrape);
  const media = pct("media_pct", analyze);
  const blueprint = pct("blueprint_pct", media);
  return { scrape, analyze, media, blueprint, propose: pct("propose_pct", blueprint) };
}
