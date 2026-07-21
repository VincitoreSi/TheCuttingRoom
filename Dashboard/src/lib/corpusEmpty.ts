import type { Stage } from "./types";

/* Why the reel grid is empty, and what to do about it.

   Three different situations used to render the same sentence — "No reels match. Loosen
   the filter, or run Scrape from the Board." One of them is a scrape that already ran:
   `scrape` writes raw JSON, and only `analyze` turns that into the scored corpus this
   grid reads. Telling someone who has just watched 250 reels download to go and run
   Scrape sends them back around a loop they have already completed.

   Kept as a pure function for the same reason deriveOnboarding is: the interesting part
   is the state machine, not the markup. */

export interface CorpusEmpty {
  title: string;
  hint: string;
  /** the pipeline stage that would fix this, when one would */
  run?: Stage;
  /** when no stage can help, the view that can */
  goto?: "config";
}

export function deriveCorpusEmpty(args: {
  /** reels in the corpus, before any filter is applied */
  total: number;
  /** reels left after the search box, tier and blueprint filters */
  filtered: number;
  /** raw scrape output exists on disk (PlatformSummary.scraped) */
  scraped: boolean;
  /** handles on the watchlist (PlatformSummary.watchlist) */
  watchlist: number;
}): CorpusEmpty | null {
  const { total, filtered, scraped, watchlist } = args;

  if (filtered > 0) return null;

  // Something is loaded and the filters hid it — the only case where the old copy was right.
  if (total > 0) {
    return {
      title: "No reels match",
      hint: "Loosen the filter or clear the search to see the rest of the corpus.",
    };
  }

  // The case that made this worth writing down.
  if (scraped) {
    return {
      title: "Scraped, not scored yet",
      hint:
        "Reels are on disk but nothing has ranked them. Analyze scores every one on the " +
        "four virality signals and builds the corpus this grid reads — no re-scraping.",
      run: "analyze",
    };
  }

  // Offering Scrape with an empty watchlist is offering a button that cannot work — the
  // hub refuses it, and the onboarding checklist has always said "add a handle" is step
  // one. Send them there instead.
  if (watchlist === 0) {
    return {
      title: "No creators watched yet",
      hint: "Add at least one Instagram handle to the watchlist in Config — the scraper needs somewhere to pull from.",
      goto: "config",
    };
  }

  return {
    title: "No reels yet",
    hint: "Scrape pulls recent reels from the creators on your watchlist (guest mode — no login).",
    run: "scrape",
  };
}
