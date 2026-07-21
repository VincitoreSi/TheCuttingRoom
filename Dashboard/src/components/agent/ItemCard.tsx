import { motion } from "framer-motion";
import { useReducedMotion } from "../../lib/hooks";
import { scoreColor } from "../../lib/evalModel";
import { cx } from "../../lib/cx";
import type { AgentBoardItem } from "../../lib/types";
export type { AgentBoardItem };

/** The card/dot CSS-class variant (views.css item-card and item-card__dot
    modifiers) is a structural read of the stage — it also carries non-color
    state (the rejected dot dims via opacity, the active/failed dots glow) —
    so it stays a small local classifier. The score badge's color comes from
    the shared statusTone helper below, same as every other status pill. */
export type StageTone = "neutral" | "active" | "done" | "approved" | "rejected" | "failed";

export function stageTone(stage: string): StageTone {
  switch (stage) {
    case "Failed":
      return "failed";
    case "Rejected":
      return "rejected";
    case "Approved":
      return "approved";
    case "Done":
      return "done";
    case "Proposed":
    case "Queued":
      return "neutral";
    default:
      return "active"; // Analyzing, Self-eval, Generating, …
  }
}

/** A compact card for one item on the workflow board. `layoutId` on the
    content_id lets Framer Motion carry the same element across lanes (§10.5
    sanctioned card-travel) instead of cross-fading a new one in. */
export function ItemCard({ item, onOpen }: { item: AgentBoardItem; onOpen: (id: string) => void }) {
  const reduced = useReducedMotion();
  const tone = stageTone(item.stage);

  return (
    <motion.button
      type="button"
      layout
      layoutId={item.content_id}
      onClick={() => onOpen(item.content_id)}
      className={cx("item-card", `item-card--${tone}`)}
      initial={reduced ? false : { opacity: 0, scale: 0.94 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34 }}
      title={item.content_id}
    >
      <span className={cx("item-card__dot", `item-card__dot--${tone}`)} aria-hidden="true" />
      <span className="item-card__id font-mono">{item.content_id}</span>
      {item.score != null && (
        // stageTone (the dot) reads workflow state; the score badge's COLOR
        // reads the shared scoreColor rule — one score→color truth app-wide.
        <span className="ui-badge item-card__score" style={{ color: scoreColor(item.score) }}>
          {Math.round(item.score)}
        </span>
      )}
    </motion.button>
  );
}
