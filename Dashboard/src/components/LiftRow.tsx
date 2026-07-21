import { CountUp, TapeGauge } from "./gauges";
import { confidence, type Confidence } from "../lib/playbookModel";
import type { Factor } from "../lib/types";

/**
 * The confidence mark reused by every ranked row and the header legend: a
 * filled dot (solid, n≥80), a hollow ring (thin, 25≤n<80), or a faint hollow
 * dot (noise, n<25). Colored by the row's tone so a low-confidence drag reads
 * as visibly demoted, not equal-weight with a solid one.
 */
export function ConfidenceDot({ conf, color }: { conf: Confidence; color: string }) {
  const base: React.CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: 999,
    flex: "none",
    display: "inline-block",
  };
  if (conf === "solid") return <span aria-hidden="true" style={{ ...base, background: color }} />;
  if (conf === "thin")
    return (
      <span
        aria-hidden="true"
        style={{ ...base, border: `1.5px solid ${color}`, background: "transparent" }}
      />
    );
  return (
    <span
      aria-hidden="true"
      style={{
        ...base,
        border: "1px solid var(--ink-faint)",
        background: "transparent",
        opacity: 0.6,
      }}
    />
  );
}

export const CONFIDENCE_WORD: Record<Confidence, string> = {
  solid: "solid",
  thin: "thin",
  noise: "noise",
};

/**
 * One evidence row: feature+bucket label, a TapeGauge for the bucket's
 * mean_score with a baseline marker overlaid so every bar shows distance from
 * baseline, the signed lift, and a confidence dot + n. Shared by both §4
 * columns. The baseline marker is a sibling overlay here — TapeGauge stays
 * untouched.
 */
export function LiftRow({
  f,
  baseline,
  tone,
  id,
}: {
  f: Factor;
  baseline: number;
  tone: "sage" | "danger";
  id?: string;
}) {
  const conf = confidence(f.n);
  const toneVar = tone === "sage" ? "var(--sage)" : "var(--danger)";
  const liftInk = tone === "sage" ? "var(--sage-ink)" : "var(--danger)";
  const sign = f.lift >= 0 ? "+" : "";
  const marker = Math.max(0, Math.min(100, baseline));

  return (
    <div
      id={id}
      className="flex items-center gap-3 py-2.5 border-b border-[var(--line)] last:border-b-0"
    >
      <div className="min-w-0 w-[34%] shrink-0 chalk-underline">
        {/* truncate on an inner block wrapper (not the chalk-underline div,
            whose ::after decoration overhangs and would be clipped). */}
        <span className="block truncate">
          <span className="text-[12px] text-[var(--ink-dim)]">{f.feature}</span>{" "}
          <span className="text-[13px] text-[var(--ink)] font-medium">{f.bucket}</span>
        </span>
      </div>

      {/* relative wrapper hosts the baseline marker as a sibling overlay */}
      <div className="relative flex-1 min-w-0">
        <TapeGauge value={f.mean_score} tierColor={toneVar} showTicks height={8} />
        <span
          aria-hidden="true"
          title={`baseline ${baseline.toFixed(1)}`}
          style={{
            position: "absolute",
            top: -2,
            bottom: -2,
            left: `${marker}%`,
            width: 1,
            background: "var(--ink-dim)",
            opacity: 0.85,
            pointerEvents: "none",
          }}
        />
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <span className="font-mono tnum text-[13px]" style={{ color: liftInk }}>
          {sign}
          <CountUp to={f.lift} decimals={1} />
        </span>
        <span className="flex items-center gap-1" title={`${CONFIDENCE_WORD[conf]} · n=${f.n}`}>
          <ConfidenceDot conf={conf} color={toneVar} />
          <span className="font-mono text-[10px] text-[var(--ink-faint)]">n{f.n}</span>
        </span>
      </div>
    </div>
  );
}
