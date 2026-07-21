import {
  forwardRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { cx } from "../lib/cx";

/* ------------------------------------------------------------------ Button */
type BtnVariant = "primary" | "outline" | "ghost" | "sage" | "oxblood" | "danger";
type BtnSize = "md" | "sm" | "icon";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "outline", size = "md", className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cx("ui-btn", `ui-btn--${variant}`, `ui-btn--${size}`, className)}
      {...rest}
    >
      {children}
    </button>
  );
});

/* -------------------------------------------------------------------- Card */
export function Card({
  className,
  children,
  interactive,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  return (
    <div className={cx("mat", interactive && "ui-card--interactive", className)} {...rest}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------- Badge */
export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "brass" | "sage" | "oxblood" | "amber" | "danger";
  className?: string;
}) {
  return <span className={cx("ui-badge", `ui-badge--${tone}`, className)}>{children}</span>;
}

/* ---------------------------------------------------------------- Eyebrow */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("eyebrow", className)}>{children}</div>;
}

/* ------------------------------------------------------------- SectionHead */
export function SectionHead({
  eyebrow,
  title,
  right,
}: {
  eyebrow?: string;
  title: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4 mb-4">
      <div>
        {eyebrow && <Eyebrow className="mb-1.5">{eyebrow}</Eyebrow>}
        <h2 className="font-display text-[22px] leading-none tracking-tight text-[var(--ink)]">
          {title}
        </h2>
      </div>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------------ Switch */
export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx("ui-switch", checked && "ui-switch--on")}
    >
      <span className="ui-switch__knob" />
    </button>
  );
}

/* ------------------------------------------------------------------ Select */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <div className="ui-select-wrap">
        <select ref={ref} className={cx("ui-select", className)} {...rest}>
          {children}
        </select>
      </div>
    );
  },
);

/* ------------------------------------------------------------------- Input */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx("ui-input", className)} {...rest} />;
  },
);

/* ------------------------------------------------------------- RangeSlider
   A tailoring-flavored slider — the track is a tape segment, the thumb a
   brass index. Sum-normalized weight editing uses this. */
export function RangeSlider({
  value,
  min = 0,
  max = 1,
  step = 0.01,
  onChange,
  "aria-label": ariaLabel,
}: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
  "aria-label"?: string;
}) {
  const pctPos = ((value - min) / (max - min)) * 100;
  return (
    <input
      type="range"
      className="ui-range"
      style={{ ["--fill" as string]: `${pctPos}%` }}
      value={value}
      min={min}
      max={max}
      step={step}
      aria-label={ariaLabel}
      onChange={(e) => onChange(parseFloat(e.target.value))}
    />
  );
}

/* ----------------------------------------------------------------- Tooltip
   Pure-CSS hover/focus tooltip so we don't pull a positioning lib. */
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="ui-tip" data-tip={label} tabIndex={0}>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------- Empty state */
export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="ui-empty">
      {icon && <div className="ui-empty__icon">{icon}</div>}
      <div className="font-display text-lg text-[var(--ink)]">{title}</div>
      {hint && <p className="text-[var(--ink-dim)] max-w-sm mt-1">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
