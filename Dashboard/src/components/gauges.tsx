import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "../lib/hooks";
import { cx } from "../lib/cx";

/** Odometer count-up. Data arrives; it doesn't just appear. */
export function CountUp({
  to,
  duration = 900,
  decimals = 0,
  className,
  format,
}: {
  to: number;
  duration?: number;
  decimals?: number;
  className?: string;
  format?: (n: number) => string;
}) {
  const reduced = useReducedMotion();
  const [val, setVal] = useState(reduced ? to : 0);
  const raf = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (reduced) {
      setVal(to);
      return;
    }
    const start = performance.now();
    const from = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      // easeOutCubic mirrors our motion easing
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(from + (to - from) * eased);
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [to, duration, reduced]);

  const shown = format ? format(val) : val.toFixed(decimals);
  return <span className={cx("tnum", className)}>{shown}</span>;
}

/**
 * The Tape score gauge — virality rendered as distance traveled down a
 * tailor's tape (0–100), with tick ruling and tier notches. This is the
 * app's core data mark; it reuses the thread only for the traveled length.
 */
export function TapeGauge({
  value,
  tierColor,
  showTicks = true,
  height = 8,
}: {
  value: number; // 0..100
  tierColor?: string;
  showTicks?: boolean;
  height?: number;
}) {
  const reduced = useReducedMotion();
  const [w, setW] = useState(reduced ? value : 0);
  useEffect(() => {
    if (reduced) return setW(value);
    const id = requestAnimationFrame(() => setW(value));
    return () => cancelAnimationFrame(id);
  }, [value, reduced]);

  return (
    <div className="tape-gauge" style={{ height }}>
      {showTicks && (
        <div className="tape-gauge__ticks" aria-hidden="true">
          {Array.from({ length: 11 }).map((_, i) => (
            <span
              key={i}
              className={cx("tape-gauge__tick", i % 5 === 0 && "tape-gauge__tick--major")}
            />
          ))}
        </div>
      )}
      <div
        className="tape-gauge__fill"
        style={{
          width: `${Math.max(0, Math.min(100, w))}%`,
          background: tierColor
            ? `linear-gradient(90deg, color-mix(in srgb, ${tierColor} 55%, var(--oxblood)), ${tierColor})`
            : "var(--thread)",
        }}
      />
      <span
        className="tape-gauge__index"
        style={{
          left: `${Math.max(0, Math.min(100, w))}%`,
          background: tierColor ?? "var(--brass)",
        }}
      />
    </div>
  );
}

/** A small circular "signal ring" — one virality factor as a filled arc. */
export function SignalRing({
  value,
  max,
  label,
  color = "var(--brass)",
}: {
  value: number;
  max: number;
  label: string;
  color?: string;
}) {
  const reduced = useReducedMotion();
  const r = 15;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, max ? value / max : 0));
  const [shown, setShown] = useState(reduced ? frac : 0);
  useEffect(() => {
    if (reduced) return setShown(frac);
    const id = requestAnimationFrame(() => setShown(frac));
    return () => cancelAnimationFrame(id);
  }, [frac, reduced]);

  return (
    <div className="flex flex-col items-center gap-1.5">
      <svg width={40} height={40} viewBox="0 0 40 40">
        <circle cx="20" cy="20" r={r} fill="none" stroke="var(--surface-3)" strokeWidth={3.5} />
        <circle
          cx="20"
          cy="20"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={3.5}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - shown)}
          transform="rotate(-90 20 20)"
          style={{ transition: reduced ? undefined : "stroke-dashoffset 0.9s var(--ease)" }}
        />
      </svg>
      <span className="eyebrow" style={{ letterSpacing: "0.08em" }}>
        {label}
      </span>
    </div>
  );
}
