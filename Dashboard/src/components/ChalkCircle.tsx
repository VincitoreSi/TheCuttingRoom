import type { ReactNode } from "react";

/**
 * A hand-drawn chalk circle — the editor's "pick" mark. It draws on hover
 * (the grease-pencil considering this one), so a grid of top-tier reels
 * stays calm until you reach for a specific swatch. Drawing/visibility is
 * driven by CSS (.chalk-wrap:hover) so it respects reduced-motion.
 */
export function ChalkCircle({ children }: { children: ReactNode }) {
  return (
    <div className="chalk-wrap">
      <svg
        className="chalk-circle"
        viewBox="0 0 100 130"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M50 4 C 80 3, 98 26, 97 65 C 96 104, 78 127, 49 126 C 20 125, 3 100, 4 63 C 5 28, 22 6, 52 5"
          fill="none"
          stroke="var(--sage-ink)"
          strokeWidth={1.4}
          strokeLinecap="round"
        />
      </svg>
      {children}
    </div>
  );
}
