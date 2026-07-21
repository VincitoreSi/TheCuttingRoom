/* The count line under each node on the Board.

   Split out of PipelineBoard for one reason: `plural` is handed numbers that come straight
   off the wire, and TypeScript cannot police the wire. `PlatformSummary.watchlist` is typed
   `number` because that is what the current hub sends — but a hub process started before
   the code on disk keeps serving the OLDER response shape from memory, where the field does
   not exist at all. The types say it is there, so nothing complains, and the Board renders
   the literal string "undefined pages".

   That happened: a hub left running from an earlier session was reused after the tree was
   re-cloned. `./init` now refuses to adopt a stale hub and `GET /api/hub` reports the skew,
   which is the actual fix. This is the second line of defence — a missing count should read
   as "not known" and never as a word the user has to decode. */

/** `2 pages`, `1 page`, and `—` when the number never arrived.
 *
 * Only real count-nouns get an "s"; the adjective/status words (pending, viral, saved)
 * must not, so those stay hand-written at the call site.
 */
export function plural(n: number | undefined | null, noun: string): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}
