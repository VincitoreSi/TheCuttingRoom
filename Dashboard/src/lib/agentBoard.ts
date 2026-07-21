import type { AgentBoard, AgentRun, AgentBoardItem, LogEvent } from "./types";

const TERMINAL = new Set(["Done", "Failed", "Approved", "Rejected"]);

/** Apply one live log event to a board snapshot, returning a new board (immutable). */
export function applyLogEvent(board: AgentBoard, ev: LogEvent): AgentBoard {
  if (ev.agent !== board.agent) return board;
  const evt = ev.event ?? "";
  if (
    !["run.start", "run.end", "item.start", "item.stage", "item.done", "item.error"].includes(evt)
  )
    return board;
  const rid = ev.run_id ?? "unknown";
  const stages = board.workflow_stages;
  const runs = board.runs.slice();
  let idx = runs.findIndex((r) => r.run_id === rid);
  if (idx === -1) {
    const run: AgentRun = {
      run_id: rid,
      platform: ev.platform,
      started: ev.ts,
      ended: null,
      counts: { total: 0, done: 0, failed: 0 },
      items: [],
    };
    runs.unshift(run);
    idx = 0;
  }
  const run: AgentRun = { ...runs[idx], items: runs[idx].items.slice() };
  const data = ev.data ?? {};
  if (evt === "run.end") run.ended = ev.ts ?? run.ended;
  if (evt === "run.start") run.started = ev.ts ?? run.started;
  const cid = ev.content_id;
  if (cid && evt.startsWith("item.")) {
    let it = run.items.find((i) => i.content_id === cid);
    if (!it) {
      it = {
        content_id: cid,
        stage: stages[0] ?? "Queued",
        score: null,
        file: null,
        updated: ev.ts,
      };
      run.items = [...run.items, it];
    }
    const next: AgentBoardItem = { ...it };
    if (evt === "item.error") next.stage = "Failed";
    else if (evt === "item.done")
      next.stage =
        (data.stage as string) ?? (board.kind && board.kind !== "analyzer" ? "Proposed" : "Done");
    else next.stage = (data.stage as string) ?? next.stage;
    if (data.score != null) next.score = data.score as number;
    if (data.file) next.file = data.file as string;
    next.updated = ev.ts;
    run.items = run.items.map((i) => (i.content_id === cid ? next : i));
  }
  run.counts = {
    total: run.items.length,
    done: run.items.filter((i) => ["Done", "Proposed", "Approved"].includes(i.stage)).length,
    failed: run.items.filter((i) => ["Failed", "Rejected"].includes(i.stage)).length,
  };
  runs[idx] = run;
  return { ...board, runs };
}

export const isTerminal = (stage: string) => TERMINAL.has(stage);
