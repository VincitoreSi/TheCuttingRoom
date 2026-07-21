import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ReelCard } from "./ReelCard";
import type { Reel } from "../lib/types";

const GAP = 16;
const MIN_CARD = 224;

/**
 * Windowed rendering of the corpus. Only the visible rows mount, so the
 * whole scored dataset scrolls smoothly. Columns are derived from width.
 */
export function VirtualReelGrid({ reels, onOpen }: { reels: Reel[]; onOpen: (r: Reel) => void }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const cols = Math.max(1, Math.floor((width + GAP) / (MIN_CARD + GAP))) || 1;
  const cardW = cols > 0 ? (width - GAP * (cols - 1)) / cols : MIN_CARD;
  // media is 3:4, meta block ~92px
  const rowH = Math.round(cardW * (4 / 3) + 92 + GAP);
  const rowCount = Math.ceil(reels.length / cols);

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowH,
    overscan: 3,
  });

  // re-measure rows when column count changes
  useEffect(() => {
    virtualizer.measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cols, rowH]);

  return (
    <div ref={scrollRef} className="corpus-scroll">
      <div style={{ height: virtualizer.getTotalSize(), width: "100%", position: "relative" }}>
        {virtualizer.getVirtualItems().map((vrow) => {
          const start = vrow.index * cols;
          const rowReels = reels.slice(start, start + cols);
          return (
            <div
              key={vrow.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vrow.start}px)`,
                display: "grid",
                gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
                gap: GAP,
                paddingBottom: GAP,
              }}
            >
              {rowReels.map((r) => (
                <ReelCard key={r.content_id} reel={r} onOpen={onOpen} />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
