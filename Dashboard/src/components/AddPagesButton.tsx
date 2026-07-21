import { Button } from "./ui";
import { IconArrowRight } from "./icons";
import { requestConfigFocus } from "../lib/nav";
import { usePlatforms } from "../lib/hooks";
import { useShell } from "../App";
import type { ViewKey } from "./Sidebar";

/* The one way into the watchlist.

   Every empty state in this app traces back to the same missing thing — nobody has put a
   creator on the watchlist — and each one used to phrase its own way out, or offer none.
   The Board's Sources card had no click target at all; the Corpus said "run Scrape", which
   the hub refuses with no handles. One component so the answer is identical wherever the
   question comes up, and so it always LANDS on the watchlist rather than the top of Config.

   `requestConfigFocus("pages")` is consumed by ConfigView on mount: it scrolls the
   watchlist into view and focuses the input. */
export function AddPagesButton({
  onNavigate,
  label = "Add Instagram handle",
  variant = "primary",
  className,
}: {
  onNavigate?: (v: ViewKey) => void;
  label?: string;
  variant?: "primary" | "outline";
  className?: string;
}) {
  if (!onNavigate) return null;
  return (
    <Button
      variant={variant}
      size="sm"
      className={className}
      onClick={(e) => {
        e.stopPropagation();
        requestConfigFocus("pages");
        onNavigate("config");
      }}
      title="Open the watchlist in Config and add an Instagram handle"
    >
      {label} <IconArrowRight size={12} />
    </Button>
  );
}

/* The same button, but only when an empty watchlist is genuinely the reason you are
   looking at an empty screen.

   Several views bottom out in "there is no corpus": no sounds, no proposals, no factors.
   On a fresh install every one of those has the same cause and the same fix, and none of
   them offered it. Once handles ARE watchlisted the emptiness means something else — the
   pipeline has not been run — so the button gates itself off rather than nagging. */
export function AddPagesCta({ onNavigate }: { onNavigate?: (v: ViewKey) => void }) {
  const { platform } = useShell();
  const summary = usePlatforms().data?.find((p) => p.platform === platform);
  if (!summary || summary.watchlist > 0) return null;
  return <AddPagesButton onNavigate={onNavigate} />;
}
