import { useEffect, useRef, useState, type ReactNode } from "react";
import { IconChevron } from "../icons";
import { cx } from "../../lib/cx";

/* The shell every schema-driven config control sits in.
 *
 * Shared by the agent desk and the Config view, which had grown two copies of the same
 * markup — and so, inevitably, the same layout bug in both places.
 *
 * ORDER IS THE POINT. The description used to sit BETWEEN the label and the control,
 * which made a control's vertical position a function of how long its description
 * happened to be: in one row a nine-line description pushed its dropdown 106px below the
 * toggle beside it, and the grid read as broken. With the control directly under a
 * single-line label, every control in a row starts at the same offset no matter what
 * follows it.
 */
export function ConfigField({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="config-field">
      <label className="config-field__head">
        <span className="config-field__label" title={label}>
          {label}
        </span>
        {/* Fixed-height slot. A switch is 25px and an input 38px, so without this the
            description under a toggle starts 13px higher than the one beside it — the
            same ragged edge one level down. The slot centres whatever it holds. */}
        <span className="config-field__control">{children}</span>
      </label>
      {/* Outside the <label> deliberately: the expand toggle below is a <button>, and a
          button nested inside a label also activates the control that label points at —
          clicking "more" would flip the switch next to it. */}
      {description && <Hint text={description} />}
    </div>
  );
}

/* A description clamped to two lines, expandable in place.
 *
 * These run from eight words to sixty, and the long ones carry warnings you cannot afford
 * to bury ("render_seed is FLUX/NVIDIA-NIM ONLY — ignored by nano_banana"). So the text
 * stays on the page and only its overflow folds away, rather than hiding the whole thing
 * behind an affordance someone has to know to click.
 */
function Hint({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measured, not guessed from character count: these sit in an auto-fill grid, so
    // whether a description needs three lines or two depends on the column width at this
    // viewport — and that changes as the window resizes.
    const measure = () => {
      if (expanded) return; // while expanded scrollHeight === clientHeight, which would clear it
      setOverflows(el.scrollHeight > el.clientHeight + 1);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, expanded]);

  return (
    <div className="config-field__hint-wrap">
      <p ref={ref} className={cx("config-field__hint", !expanded && "config-field__hint--clamp")}>
        {text}
      </p>
      {(overflows || expanded) && (
        <button
          type="button"
          className="config-field__more"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "less" : "more"}
          <IconChevron size={11} className={cx("config-field__chev", expanded && "is-open")} />
        </button>
      )}
    </div>
  );
}
