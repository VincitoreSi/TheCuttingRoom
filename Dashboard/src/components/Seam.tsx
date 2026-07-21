import { useReducedMotion } from "../lib/hooks";
import { cx } from "../lib/cx";

export type SeamState = "idle" | "working" | "done" | "error";

/**
 * The signature agent-status motif: a needle-and-thread seam.
 *  - idle    : a faint basted (dashed) line waiting to be sewn
 *  - working : the thread marches (dashoffset loop), needle bobs — oxblood
 *  - done    : a solid sage seam finished with a knot
 *  - error   : the seam snaps in two — the hotter red
 */
export function Seam({
  state,
  width = 132,
  className,
  flowOn = true,
}: {
  state: SeamState;
  width?: number;
  className?: string;
  flowOn?: boolean;
}) {
  const reduced = useReducedMotion();
  const animate = state === "working" && !reduced && flowOn;
  const h = 22;
  const midY = h / 2;
  const stroke =
    state === "done"
      ? "var(--sage)"
      : state === "error"
        ? "var(--danger)"
        : state === "working"
          ? "var(--oxblood)"
          : "var(--line-strong)";

  return (
    <svg
      width={width}
      height={h}
      viewBox={`0 0 ${width} ${h}`}
      className={cx("seam", className)}
      role="img"
      aria-label={`agent ${state}`}
    >
      {state === "error" ? (
        <>
          {/* snapped in two */}
          <path
            d={`M2 ${midY} H ${width / 2 - 8}`}
            stroke={stroke}
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray="5 4"
          />
          <path
            d={`M ${width / 2 + 8} ${midY} H ${width - 2}`}
            stroke={stroke}
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray="5 4"
          />
          <path
            d={`M ${width / 2 - 8} ${midY - 5} l 4 5 l -4 5`}
            stroke={stroke}
            strokeWidth={1.6}
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d={`M ${width / 2 + 8} ${midY - 5} l -4 5 l 4 5`}
            stroke={stroke}
            strokeWidth={1.6}
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      ) : (
        <>
          <path
            d={`M2 ${midY} H ${width - 14}`}
            stroke={stroke}
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray={state === "done" ? "0" : "6 4"}
            style={animate ? { animation: "seam-march 0.9s linear infinite" } : undefined}
          />
          {state === "done" ? (
            /* the finishing knot */
            <g stroke="var(--sage)" strokeWidth={2} fill="none">
              <circle cx={width - 12} cy={midY} r={3.2} fill="var(--sage-wash)" />
              <path d={`M ${width - 12} ${midY - 3} v -3`} strokeLinecap="round" />
            </g>
          ) : (
            /* the needle, leading the thread */
            <g style={animate ? { animation: "needle-bob 0.9s var(--ease) infinite" } : undefined}>
              <path
                d={`M ${width - 15} ${midY} l 12 0`}
                stroke={state === "working" ? "var(--brass)" : "var(--ink-faint)"}
                strokeWidth={1.6}
                strokeLinecap="round"
              />
              <circle
                cx={width - 13}
                cy={midY}
                r={1.5}
                fill="none"
                stroke={state === "working" ? "var(--brass)" : "var(--ink-faint)"}
                strokeWidth={1.2}
              />
            </g>
          )}
        </>
      )}
    </svg>
  );
}

/** A compact label + seam, for the header agent status. */
export function SeamStatus({
  state,
  label,
  flowOn = true,
}: {
  state: SeamState;
  label: string;
  flowOn?: boolean;
}) {
  const tone =
    state === "done"
      ? "var(--sage-ink)"
      : state === "error"
        ? "var(--danger)"
        : state === "working"
          ? "var(--oxblood-ink)"
          : "var(--ink-dim)";
  return (
    <div className="flex items-center gap-2.5">
      <Seam state={state} width={92} flowOn={flowOn} />
      <span className="font-mono text-[11px] tracking-wide uppercase" style={{ color: tone }}>
        {label}
      </span>
    </div>
  );
}
