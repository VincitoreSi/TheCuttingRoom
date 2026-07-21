import { useEffect, useRef, useState } from "react";
import { IconCheck, IconCopy } from "./icons";
import { cx } from "../lib/cx";

/**
 * Copy-to-clipboard with a brief confirm state. Used for generation prompts and
 * the Instagram sound link — the two things an operator hand-carries out of the
 * app into a generator / the IG composer.
 */
export function CopyButton({
  value,
  label = "Copy",
  copied = "Copied",
  className,
  block,
}: {
  value: string;
  label?: string;
  copied?: string;
  className?: string;
  block?: boolean;
}) {
  const [done, setDone] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => () => clearTimeout(timeoutRef.current), []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // clipboard blocked (insecure context) — fall back to a temp textarea
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* give up silently */
      }
      document.body.removeChild(ta);
    }
    setDone(true);
    clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setDone(false), 1400);
  }

  return (
    <button
      type="button"
      className={cx("copy-btn", done && "copy-btn--done", block && "copy-btn--block", className)}
      onClick={copy}
      aria-label={label}
    >
      {done ? <IconCheck size={13} /> : <IconCopy size={13} />}
      <span>{done ? copied : label}</span>
    </button>
  );
}
