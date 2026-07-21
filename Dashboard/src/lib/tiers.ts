// Maps a tier label from the corpus to its color + tone. The engine emits
// labels like "Viral", "High", "Above Average", "Average" (see niche_config).

export interface TierMeta {
  color: string;
  tone: "brass" | "sage" | "oxblood" | "neutral";
  rank: number;
}

export function tierMeta(label: string | null | undefined): TierMeta {
  const l = (label ?? "").toLowerCase();
  if (l.includes("viral")) return { color: "var(--tier-viral)", tone: "brass", rank: 4 };
  if (l.includes("high")) return { color: "var(--tier-high)", tone: "oxblood", rank: 3 };
  if (l.includes("above")) return { color: "var(--tier-above)", tone: "sage", rank: 2 };
  return { color: "var(--tier-avg)", tone: "neutral", rank: 1 };
}

/** The top tier gets the chalk circle — the editor's "pick". */
export function isTopTier(label: string | null | undefined): boolean {
  return (label ?? "").toLowerCase().includes("viral");
}
