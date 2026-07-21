import { useState } from "react";
import { Badge } from "../ui";
import { IconChevron } from "../icons";
import { WorkflowBoard } from "./WorkflowBoard";
import { agoFrom } from "../../lib/format";
import { useNow } from "../../lib/hooks";
import { statusTone } from "../../lib/statusTone";
import { cx } from "../../lib/cx";
import type { AgentRun } from "../../lib/types";

/** A collapsible run header over a lane strip scoped to just this run's
    items. Live runs (ended == null) are expanded by default; others start
    collapsed — the operator opens history on demand. */
export function RunGroup({
  run,
  stages,
  live,
  onOpenItem,
  agent,
  kind,
}: {
  run: AgentRun;
  stages: string[];
  live: boolean;
  onOpenItem: (id: string) => void;
  agent: string;
  kind?: string | null;
}) {
  const [open, setOpen] = useState(live);
  const now = useNow(15_000);
  const started =
    run.started != null ? agoFrom(new Date(run.started * 1000).toISOString(), now) : "—";

  return (
    <div className={cx("run-group", live && "run-group--live")}>
      <button className="run-group__head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <IconChevron size={14} className={cx("chev", open && "chev--open")} />
        <span className="run-group__id font-mono">{run.run_id.slice(0, 16)}</span>
        {/* a live run is a "running" job, same as everywhere else the app
            reads a job/stage as in-progress — the oxblood working thread. */}
        {live && <Badge tone={statusTone("pipeline-job", "running")}>live</Badge>}
        <span className="eyebrow">started {started}</span>
        <span className="run-group__counts font-mono">
          {run.counts.done}/{run.counts.total} done
          {run.counts.failed > 0 ? ` · ${run.counts.failed} failed` : ""}
        </span>
      </button>
      {open && (
        <div className="run-group__body">
          <WorkflowBoard
            board={{ agent, kind: kind ?? null, workflow_stages: stages, runs: [run] }}
            live={live}
            onOpenItem={onOpenItem}
          />
        </div>
      )}
    </div>
  );
}
