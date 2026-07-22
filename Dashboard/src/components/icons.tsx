import type { SVGProps } from "react";

// Bespoke line icons in the tailoring vocabulary — 1.5px stroke, currentColor,
// tuned to the hairline weight so they read as drawn, not imported.
type P = SVGProps<SVGSVGElement> & { size?: number };
function I({ size = 18, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

/* nav */
export const IconBoard = (p: P) => (
  <I {...p}>
    <path d="M3 12h4l2-4 3 8 2-5 2 3h5" />
    <circle cx="3" cy="12" r="1" fill="currentColor" stroke="none" />
    <circle cx="21" cy="14" r="1" fill="currentColor" stroke="none" />
  </I>
);
export const IconCorpus = (p: P) => (
  <I {...p}>
    <rect x="4" y="4" width="7" height="7" rx="1" />
    <rect x="13" y="4" width="7" height="7" rx="1" />
    <rect x="4" y="13" width="7" height="7" rx="1" />
    <rect x="13" y="13" width="7" height="7" rx="1" />
  </I>
);
export const IconStudio = (p: P) => (
  // spool of thread
  <I {...p}>
    <rect x="6" y="3" width="12" height="18" rx="1.5" />
    <path d="M6 7h12M6 17h12" />
    <path d="M9 10h6M9 13h4" />
  </I>
);
export const IconConfig = (p: P) => (
  // measuring dials
  <I {...p}>
    <path d="M4 8h10M18 8h2M4 16h2M10 16h10" />
    <rect x="13" y="5.5" width="4" height="5" rx="1.2" />
    <rect x="6" y="13.5" width="4" height="5" rx="1.2" />
  </I>
);
export const IconInsights = (p: P) => (
  // an open playbook
  <I {...p}>
    <path d="M12 6c-2-1.4-4.5-1.6-7-1v13c2.5-.6 5-.4 7 1 2-1.4 4.5-1.6 7-1V5c-2.5-.6-5-.4-7 1Z" />
    <path d="M12 6v13" />
  </I>
);

/* actions + status */
export const IconScissors = (p: P) => (
  <I {...p}>
    <circle cx="6" cy="6" r="2.4" />
    <circle cx="6" cy="18" r="2.4" />
    <path d="M8 8l12 8M8 16l12-8" />
    <path d="M20 6l-8 6M20 18l-4-3" />
  </I>
);
export const IconNeedle = (p: P) => (
  <I {...p}>
    <path d="M4 20L18 6" />
    <path d="M18 6l2-2" />
    <circle cx="17" cy="7" r="1.2" />
    <path d="M4 20l1.5-4" />
  </I>
);
export const IconPin = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="8" r="3.2" />
    <path d="M12 11.2V21" />
  </I>
);
export const IconTape = (p: P) => (
  <I {...p}>
    <circle cx="9" cy="12" r="5" />
    <circle cx="9" cy="12" r="1.4" />
    <path d="M13.5 10.5H21v5H10" />
    <path d="M15 10.5v2M17 10.5v2M19 10.5v2" />
  </I>
);
export const IconPlay = (p: P) => (
  <I {...p}>
    <path d="M8 5.5v13l11-6.5-11-6.5Z" />
  </I>
);
export const IconCheck = (p: P) => (
  <I {...p}>
    <path d="M4 12.5l5 5L20 6" />
  </I>
);
export const IconX = (p: P) => (
  <I {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </I>
);
export const IconTrash = (p: P) => (
  <I {...p}>
    <path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13M10 11v6M14 11v6" />
  </I>
);
export const IconArrowRight = (p: P) => (
  <I {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </I>
);
export const IconSearch = (p: P) => (
  <I {...p}>
    <circle cx="11" cy="11" r="6" />
    <path d="M20 20l-4-4" />
  </I>
);
export const IconSun = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
  </I>
);
export const IconMoon = (p: P) => (
  <I {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
  </I>
);
export const IconExternal = (p: P) => (
  <I {...p}>
    <path d="M14 5h5v5M19 5l-8 8M18 13v6H5V6h6" />
  </I>
);
export const IconChevron = (p: P) => (
  <I {...p}>
    <path d="M8 5l7 7-7 7" />
  </I>
);
export const IconMenu = (p: P) => (
  <I {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </I>
);
export const IconVolume = (p: P) => (
  <I {...p}>
    <path d="M4 9v6h4l5 4V5L8 9H4Z" />
    <path d="M17 9a3 3 0 0 1 0 6" />
  </I>
);
export const IconMuted = (p: P) => (
  <I {...p}>
    <path d="M4 9v6h4l5 4V5L8 9H4Z" />
    <path d="M16 9l5 6M21 9l-5 6" />
  </I>
);

/* nav + surfaces added for slice 6.4 */
export const IconSound = (p: P) => (
  // a sound waveform — bars metered like the tape
  <I {...p}>
    <path d="M4 12v0M7.5 8v8M11 5v14M14.5 9v6M18 7v10M21 11v2" />
  </I>
);
export const IconProducers = (p: P) => (
  // production lanes fanning out from the hub
  <I {...p}>
    <circle cx="5" cy="12" r="2" />
    <path d="M7 12h4M11 12l4-5M11 12l4 5M11 12h5" />
    <circle cx="18" cy="7" r="1.6" />
    <circle cx="18" cy="12" r="1.6" />
    <circle cx="18" cy="17" r="1.6" />
  </I>
);
export const IconActivity = (p: P) => (
  // a stitched ledger line — the log pulse
  <I {...p}>
    <path d="M3 12h4l2-6 3 13 2-8 2 4h5" />
  </I>
);
export const IconEvals = (p: P) => (
  // a score trend axis
  <I {...p}>
    <path d="M4 4v16h16" />
    <path d="M7 15l3-4 3 2 4-6" />
  </I>
);
export const IconCopy = (p: P) => (
  <I {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h8" />
  </I>
);
export const IconLink = (p: P) => (
  <I {...p}>
    <path d="M10 14a4 4 0 0 0 6 .5l2-2a4 4 0 0 0-6-6l-1 1" />
    <path d="M14 10a4 4 0 0 0-6-.5l-2 2a4 4 0 0 0 6 6l1-1" />
  </I>
);

/* the render surface — a film strip, sprockets punched down both selvedges,
   reading as cut cloth rather than a camera pictogram. */
export const IconFilm = (p: P) => (
  <I {...p}>
    <rect x="3" y="5" width="18" height="14" rx="1.5" />
    <path d="M7.5 5v14M16.5 5v14" />
    <path d="M3 9.5h4.5M3 14.5h4.5M16.5 9.5H21M16.5 14.5H21" />
  </I>
);

/* stop — a cut line, pairing with the stopped-seam vocabulary */
export const IconStop = (p: P) => (
  <I {...p}>
    <line x1="6" y1="6" x2="18" y2="18" />
    <line x1="18" y1="6" x2="6" y2="18" />
  </I>
);

/* the Discover node (§11) — a scout's glass reading the tape ticks, echoing
   the ruler vocabulary instead of introducing a new pictogram. */
export const IconDiscover = (p: P) => (
  <I {...p}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M10.5 6v2M10.5 13v2M6 10.5h2M13 10.5h2" />
    <circle cx="10.5" cy="10.5" r="1.3" fill="currentColor" stroke="none" />
    <path d="M15.2 15.2L21 21" />
  </I>
);
