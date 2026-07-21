// Numeric presentation — the "spec sheet" reads like tape-measure figures.

/** 68_252_479 → "68.3M". Keeps small-account numbers honest. */
export function compact(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  const units = [
    { v: 1e9, s: "B" },
    { v: 1e6, s: "M" },
    { v: 1e3, s: "K" },
  ];
  for (const u of units) {
    if (abs >= u.v) {
      const val = n / u.v;
      const d = val >= 100 ? 0 : digits;
      return `${val.toFixed(d).replace(/\.0+$/, "")}${u.s}`;
    }
  }
  return String(n);
}

/** Grouped integer, e.g. 1030 → "1,030". */
export function grouped(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

/** A ratio metric like reach_multiplier → "117.1×". */
export function times(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n >= 100 ? Math.round(n) : n.toFixed(digits)}×`;
}

/** engagement_rate is already a percentage-ish figure in this corpus. */
export function pct(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function score(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(1);
}

export function seconds(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n)}s`;
}

/** "2026-05-29 16:31:07" → "49d ago" (corpus timestamps are recent-relative). */
export function agoFrom(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso.replace(" ", "T"));
  if (Number.isNaN(t)) return "—";
  const days = Math.max(0, (nowMs - t) / 86_400_000);
  if (days < 1) return `${Math.round(days * 24)}h ago`;
  if (days < 30) return `${Math.round(days)}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

export function elapsed(fromSec: number, toSec: number | null, nowMs: number): string {
  const end = toSec ?? nowMs / 1000;
  const s = Math.max(0, end - fromSec);
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}
