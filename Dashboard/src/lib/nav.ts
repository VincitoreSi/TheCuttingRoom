/* A one-shot, cross-view navigation intent.

   The shell is a plain view-switcher (no router), and views remount on switch. When
   the Home onboarding checklist sends a user to Config to add their first handle, it
   wants Config to open scrolled to the watchlist with the input focused. Rather than
   thread a prop through the shell (and churn its memoized context on every set), we
   stash a single pending intent here and let ConfigView consume it once on mount. */

let pendingConfigFocus: string | null = null;

/** Ask the next Config mount to focus a region (e.g. "pages"). */
export function requestConfigFocus(target: string): void {
  pendingConfigFocus = target;
}

/** Read-and-clear the pending Config focus intent. Returns null if none is set. */
export function consumeConfigFocus(): string | null {
  const t = pendingConfigFocus;
  pendingConfigFocus = null;
  return t;
}
