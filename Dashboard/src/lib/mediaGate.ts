// The media gate, mirrored on the client so the Config card can show the effective score
// floor the hub will actually apply. The resolution rule is IDENTICAL to the backend's
// core/virality.resolve_media_filter: an explicit min_score wins, otherwise min_tier maps
// through the platform's own tiers, otherwise there is no floor.

import { tierMeta } from "./tiers";
import type { MediaFilter, Tier } from "./types";

/** The score a tier label demands, read from the platform's configured tiers (never a
    hardcoded table) so labels/thresholds match the scoring engine. Null if the label is
    absent. */
export function tierThreshold(tiers: Tier[], label: string | null | undefined): number | null {
  if (!label) return null;
  const t = tiers.find((x) => x.label === label);
  return t ? t.min_score : null;
}

/** The effective score floor a media_filter resolves to. `undefined`/empty filter, or a
    min_tier that names no configured tier, resolves to null (no gate). */
export function mediaGateMinScore(
  tiers: Tier[],
  mf: MediaFilter | null | undefined,
): number | null {
  if (!mf) return null;
  if (mf.min_score != null) return mf.min_score;
  return tierThreshold(tiers, mf.min_tier);
}

/** Tiers ordered for the gate dropdown — highest first — reusing tiers.ts's tone ranking,
    with the configured min_score as the tiebreak so two labels that share a rank still sort
    by their actual cutoff. */
export function orderedTiers(tiers: Tier[]): Tier[] {
  return [...tiers].sort(
    (a, b) => tierMeta(b.label).rank - tierMeta(a.label).rank || b.min_score - a.min_score,
  );
}
