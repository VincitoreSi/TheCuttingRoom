/* Agent ids are kebab/snake machine names (analysis-engine, auto-search). Show
   them as a Title Case display name wherever a human-facing heading names the
   agent; keep the raw id for keys, openAgent(), aria-labels, and API calls. */
export function humanizeAgent(name: string): string {
  return name
    .split(/[-_]/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}
