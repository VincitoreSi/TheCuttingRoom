import { useMemo } from "react";
import { LayoutGroup } from "framer-motion";
import { Seam, type SeamState } from "../Seam";
import { usePageVisible, useReducedMotion } from "../../lib/hooks";
import { isTerminal } from "../../lib/agentBoard";
import { ItemCard } from "./ItemCard";
import type { AgentBoard, AgentBoardItem } from "../../lib/types";

/** One lane per `board.workflow_stages` entry (+ a Failed lane appended only
    when occupied). Cards live inside a single LayoutGroup so an ItemCard's
    layoutId can travel lane-to-lane (§10.5 sanctioned card-travel) — the
    lane row itself scrolls horizontally in its own container; the page body
    never does. Lane headers reuse the Seam motif — no new signature element. */
export function WorkflowBoard({
  board,
  live,
  onOpenItem,
}: {
  board: AgentBoard;
  live: boolean;
  onOpenItem: (id: string) => void;
}) {
  const reduced = useReducedMotion();
  const visible = usePageVisible();
  const seamOn = live && visible && !reduced;

  const lanes = useMemo(() => {
    const items = board.runs.flatMap((r) => r.items);
    const stages = [...board.workflow_stages];
    if (!stages.includes("Failed") && items.some((i) => i.stage === "Failed"))
      stages.push("Failed");
    return stages.map((stage) => ({
      stage,
      items: items.filter((i: AgentBoardItem) => i.stage === stage),
    }));
  }, [board]);

  return (
    <LayoutGroup>
      <div className="wf-board">
        {lanes.map((lane) => {
          const hasItems = lane.items.length > 0;
          const seamState: SeamState =
            lane.stage === "Failed" && hasItems
              ? "error"
              : hasItems && (isTerminal(lane.stage) || lane.stage === "Proposed")
                ? "done"
                : hasItems && seamOn
                  ? "working"
                  : "idle";
          return (
            <div className="wf-lane" key={lane.stage}>
              <div className="wf-lane__head">
                <Seam state={seamState} width={64} />
                <span className="wf-lane__label eyebrow">{lane.stage}</span>
                <span className="wf-lane__count font-mono">{lane.items.length}</span>
              </div>
              <div className="wf-lane__items">
                {hasItems ? (
                  lane.items.map((item) => (
                    <ItemCard key={item.content_id} item={item} onOpen={onOpenItem} />
                  ))
                ) : (
                  <span className="wf-lane__empty eyebrow">—</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </LayoutGroup>
  );
}
