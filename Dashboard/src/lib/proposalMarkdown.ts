/* Pure readers over a studio proposal's markdown body.
   Lifted out of StudioView so RenderSwatch can reuse them and so they sit under
   the vitest glob (node env) — no component rendering needed to cover them. */

export function firstHeading(md: string): string | null {
  const m = md.match(/^#{1,3}\s+(.+)$/m);
  return m ? m[1].trim() : null;
}

/** A short "what to make" preview: the proposal body cleaned of markdown,
    skipping headings and the `Run:` metadata line, so a card shows the actual
    content and not only the attached sound. */
export function readySummary(md: string): string {
  const text = md
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !/^#{1,6}\s/.test(l) && !/^-{3,}$/.test(l) && !/^Run:/i.test(l))
    .map((l) =>
      l
        .replace(/[#*`>_]/g, "")
        .replace(/\s+/g, " ")
        .trim(),
    )
    .filter(Boolean)
    .slice(0, 4)
    .join(" ");
  return text.length > 240 ? `${text.slice(0, 240)}…` : text;
}

/** Lift a `## Section` block (up to the next same-or-higher heading) out of a
    proposal — used to surface the manual-post `## Audio` instruction. */
export function extractSection(md: string, name: string): string | null {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let start = -1;
  let level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(#{1,6})\s+(.+)$/);
    if (m && m[2].trim().toLowerCase().startsWith(name.toLowerCase())) {
      start = i;
      level = m[1].length;
      break;
    }
  }
  if (start === -1) return null;
  const out = [lines[start]];
  for (let i = start + 1; i < lines.length; i++) {
    const m = lines[i].match(/^(#{1,6})\s+/);
    if (m && m[1].length <= level) break;
    out.push(lines[i]);
  }
  return out.join("\n").trim();
}

/** How many frames a render of this proposal will cost, before one exists: one
    generated frame per `### Shot N` heading. The trailing number is required so
    the enclosing `## Shot list (generation-ready prompts)` heading is not itself
    counted as a shot. Used only to price the confirm step — the render record's
    own `frames` wins once it exists. */
export function countShots(md: string): number {
  const m = md.replace(/\r\n/g, "\n").match(/^###\s+Shot\s+\d+/gim);
  return m ? m.length : 0;
}
