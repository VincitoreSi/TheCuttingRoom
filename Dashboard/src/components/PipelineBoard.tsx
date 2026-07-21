import { motion } from "framer-motion";
import { useShell } from "../App";
import {
  useRunStage,
  useStopStage,
  useAnalysis,
  useStudio,
  usePendingCandidates,
} from "../lib/hooks";
import { latestStageJob, stageSeamState } from "../lib/jobs";
import { useLogStream, useNow, usePageVisible, useReducedMotion } from "../lib/hooks";
import { activitySummary, liveStageIndex } from "../lib/activityModel";
import type { ActivitySummary } from "../lib/activityModel";
import { elapsed } from "../lib/format";
import { plural } from "../lib/boardCounts";
import { sectionMotion } from "../lib/motion";
import { Button } from "./ui";
import { Seam } from "./Seam";
import { AddPagesButton } from "./AddPagesButton";
import { IconArrowRight, IconPlay, IconStop, IconTape } from "./icons";
import type { PlatformSummary, Stage, StageReadiness } from "../lib/types";
import type { ViewKey } from "./Sidebar";
import { cx } from "../lib/cx";

/* Eight marks on the tape (§11). Sources & Studio are informational nodes;
   the runnable stages the hub exposes are auto-search / scrape / analyze /
   media / analysis-engine / propose. `render` is deliberately NOT here — it
   spends image credits and stays behind the human gate in the Studio. The new
   Discover node (auto-search) sits FIRST —
   it's the new front door, feeding Sources via the gate → pages.txt. The
   Blueprint node (analysis-engine) sits after Media and is deliberately NOT
   called "Analyze" — that's the earlier virality-scoring stage.

   `agent` is the log-stream identity the node lights up on: liveStageIndex ORs
   the Job axis with a fresh log event whose `agent` matches (or whose
   `data.stage` equals the node's `stage`). Only the two log-emitting agents get
   an explicit name — similar-content drives Discover, analysis-engine drives
   Blueprint; the job-only stages light purely off their Job axis. */
type NodeDef = {
  key: string;
  label: string;
  stage?: Stage;
  agent?: string;
  hint: string;
  link?: ViewKey;
  /* machine-name of the agent desk this node opens (openAgent → AgentBoardView).
     Only nodes backed by a real `GET /api/agents/{name}/board` desk set this. */
  agentDesk?: string;
  /* the clickable affordance label (defaults to a view-link CTA otherwise). */
  cta?: string;
  /* Sources is not a runnable stage — its "input" is a human adding handles. This gives
     it the same bottom-slot button every other card has, pointing at the one place that
     changes its count. Without it the first node in the chain a new user has to act on
     was the only one with nothing to click. */
  addTo?: { view: ViewKey; focus: string; label: string };
};
const NODES: NodeDef[] = [
  {
    key: "discover",
    label: "Discover",
    stage: "auto-search",
    agent: "auto-search",
    hint: "scout new creators",
    agentDesk: "auto-search",
    cta: "Open desk",
  },
  {
    key: "sources",
    label: "Sources",
    hint: "handpicked pages",
    addTo: { view: "config", focus: "pages", label: "Add pages" },
  },
  { key: "scrape", label: "Scrape", stage: "scrape", hint: "pull reels (guest)" },
  { key: "analyze", label: "Analyze", stage: "analyze", hint: "score 4 signals" },
  { key: "media", label: "Media", stage: "media", hint: "persist top clips" },
  {
    key: "blueprint",
    label: "Blueprint",
    stage: "analysis-engine",
    agent: "analysis-engine",
    hint: "shot-by-shot plan",
    agentDesk: "analysis-engine",
    cta: "Open desk",
  },
  {
    key: "propose",
    label: "Propose",
    stage: "propose",
    hint: "recipes to the gate (free)",
  },
  {
    key: "studio",
    label: "Studio",
    hint: "producers & gate",
    agentDesk: "similar-content",
    cta: "Open board",
  },
];

export function PipelineBoard({
  summary,
  onNavigate,
}: {
  summary?: PlatformSummary;
  onNavigate?: (v: ViewKey) => void;
}) {
  const { platform, jobs, connected, openAgent } = useShell();
  const run = useRunStage(platform);
  const stop = useStopStage(platform);
  const now = useNow();
  const nowSec = now / 1000;
  const reduced = useReducedMotion();
  const visible = usePageVisible();
  const analysisQ = useAnalysis(platform);
  const studioQ = useStudio(platform);
  const pendingQ = usePendingCandidates(platform);
  // the same curated lifecycle log the Activity floor reads — so a log-only
  // agent (analysis-engine, similar-content) lights the board even without a Job.
  const { events } = useLogStream(300);

  const analyzedCount = analysisQ.data?.filter((b) => !b.is_reference).length ?? 0;

  // Every node reports ITS OWN stage. Sources used to show `creators` and Scrape `items`,
  // both of which are derived from the scored corpus — so a handle added a moment ago read
  // "0 pages", and 250 freshly scraped reels read "0 reels", until analyze (two stages
  // later) had run. The board was reporting the end of the pipeline at every mark.
  const counts: Record<string, string> = {
    discover: pendingQ.data ? `${pendingQ.data.length} pending` : "—",
    sources: summary ? plural(summary.watchlist, "page") : "—",
    scrape: summary ? plural(summary.scraped_items, "reel") : "—",
    analyze: summary ? `${summary.items} scored · ${summary.viral} viral` : "—",
    media: summary ? `${summary.media_ready} saved` : "—",
    // `summary` is polled every 15s, the analysis query only refetches when a job
    // settles — so during a blueprint run this node sat on "0 blueprints" while the
    // hub already knew about several. Prefer the live number, fall back to the query
    // (which excludes reference clips) before the summary has loaded.
    blueprint: summary
      ? plural(summary.analyzed, "blueprint")
      : analysisQ.data
        ? plural(analyzedCount, "blueprint")
        : "—",
    studio: studioQ.data ? plural(studioQ.data.length, "proposal") : "—",
  };

  // Band A — the whole-floor live read (Job axis OR log axis), one shared
  // selector feeding this strip, the Header, and Activity §A alike.
  const band = activitySummary(events, jobs, platform, nowSec, connected);
  // which node is live drives the data-flow stitch travelling the seam. Now the
  // shared selector: live by Job (running/queued) OR by a fresh in-flight log
  // event for the node's agent/stage — closes the gap where a log-only agent
  // produced no Board motion.
  const liveIndex = liveStageIndex(NODES, jobs, events, platform, nowSec);
  // the animation only runs on live activity, and stills under reduced-motion
  // or a hidden tab (§10.5)
  const flowOn = liveIndex >= 0 && !reduced && visible;

  return (
    <div className="board">
      <BandA band={band} flowOn={flowOn} reduced={reduced} />

      {/* the measuring-tape spine that the flow rides on */}
      <TapeSpine liveIndex={liveIndex} flowOn={flowOn} />
      {/* mobile-only vertical measuring rail — the tape motif when stacked */}
      <div className="board__vtape" aria-hidden="true" />

      <div className="board__track">
        {NODES.map((node, i) => {
          const job = node.stage ? latestStageJob(jobs, platform, node.stage) : undefined;
          const seam = node.stage ? stageSeamState(job) : "idle";
          const status =
            job?.status === "running" || job?.status === "queued"
              ? "running"
              : job?.status === "done"
                ? "done"
                : job?.status === "error"
                  ? "error"
                  : job?.status === "stopped"
                    ? "stopped"
                    : "idle";
          const running = status === "running";
          // a node is clickable when it opens an agent desk, or (legacy) when it
          // links to a view and a navigator is available. `activate` picks the
          // desk over the view link.
          const canOpenDesk = !!node.agentDesk;
          const canNavigate = !!node.link && !!onNavigate;
          const clickable = canOpenDesk || canNavigate;
          const activate = canOpenDesk
            ? () => openAgent(node.agentDesk!)
            : canNavigate
              ? () => onNavigate!(node.link!)
              : undefined;

          return (
            <div className="board__cell" key={node.key}>
              <motion.div
                {...sectionMotion(i, reduced)}
                className={cx(
                  "stage-node",
                  `stage-node--${status}`,
                  clickable && "stage-node--link",
                )}
                onClick={activate}
                role={clickable ? "button" : undefined}
                tabIndex={clickable ? 0 : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          activate?.();
                        }
                      }
                    : undefined
                }
              >
                {/* fixed 22px header height (the Seam glyph's height) so the
                    IconTape nodes don't sit 6px higher and misalign the counts. */}
                <div className="flex items-center justify-between min-h-[22px]">
                  <span className="eyebrow">{String(i + 1).padStart(2, "0")}</span>
                  {node.stage ? (
                    <Seam state={seam} width={44} />
                  ) : (
                    <IconTape size={16} className="text-[var(--ink-faint)]" />
                  )}
                </div>

                <div className="flex items-center justify-between gap-2">
                  <div className="font-display text-[17px] leading-none">{node.label}</div>
                  {/* desk affordance for nodes whose bottom slot is taken by the
                      Run button — makes the whole-card "open desk" click visible
                      (Studio has no Run, so it uses the bottom link-hint instead). */}
                  {node.agentDesk && node.stage && (
                    <span className="board__desk-tag" aria-hidden="true">
                      desk <IconArrowRight size={10} />
                    </span>
                  )}
                </div>
                <div className="text-[11.5px] text-[var(--ink-dim)]">{node.hint}</div>

                {/* the count sits in the content flow so it aligns across every
                    card (all cards share the eyebrow/label/hint structure above
                    it); it owns the full card width so long counts never truncate
                    behind an action. */}
                <div className="board__count font-mono text-[12px] text-[var(--ink-2)] tnum">
                  {counts[node.key]}
                </div>

                {/* only the action is bottom-pinned (mt-auto), giving every stage
                    card a shared full-width Run-button baseline. */}
                {node.stage ? (
                  <StageAction
                    stage={node.stage}
                    label={node.label}
                    running={running}
                    elapsedText={job ? elapsed(job.started, job.ended, now) : "…"}
                    pending={run.isPending}
                    stopping={stop.isPending}
                    readiness={summary?.readiness?.[node.stage]}
                    onRun={(s) => run.mutate(s)}
                    onStop={(s) => stop.mutate(s)}
                  />
                ) : node.addTo ? (
                  <AddPagesButton
                    onNavigate={onNavigate}
                    label={node.addTo.label}
                    variant="outline"
                    className="board__run mt-auto"
                  />
                ) : clickable ? (
                  <span className="board__link-hint eyebrow mt-auto">
                    {node.cta ?? "Open"} <IconArrowRight size={12} />
                  </span>
                ) : null}

                {job?.status === "error" && job.tail && (
                  <div className="board__err font-mono" title={job.tail}>
                    {job.tail.split("\n").slice(-1)[0]?.slice(0, 90)}
                  </div>
                )}
              </motion.div>

              {i < NODES.length - 1 && (
                <div
                  className={cx(
                    "connector",
                    running && "connector--live",
                    status === "done" && "connector--done",
                  )}
                  aria-hidden="true"
                >
                  {/* a chalk-thread stitch travels the live seam — the data-flow
                      animation (§10.5). CSS-driven so it dies under reduced-motion;
                      play-state pauses when the tab is hidden. */}
                  {running && (
                    <span
                      className="connector__stitch"
                      style={{ animationPlayState: flowOn ? "running" : "paused" }}
                    />
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* The bottom slot of a stage card: Run, or why you can't yet.

   Every one of these stages already refuses cleanly when its input is missing ("no scraped
   data — scrape first"). But the only way to read that was to click Run, wait for a
   subprocess to fail, and squint at a truncated tail. The hub now reports the same
   preconditions up front, so a doomed Run is disabled and the stage that would fix it is
   one click away — following `blocked_by` walks back down the pipeline until it terminates
   at something a human has to do (add a handle, set a key). */
function StageAction({
  stage,
  label,
  running,
  elapsedText,
  pending,
  stopping,
  readiness,
  onRun,
  onStop,
}: {
  stage: Stage;
  label: string;
  running: boolean;
  elapsedText: string;
  pending: boolean;
  stopping: boolean;
  readiness?: StageReadiness;
  onRun: (s: Stage) => void;
  onStop: (s: Stage) => void;
}) {
  // No readiness yet (first paint, or an older hub) → behave exactly as before rather
  // than locking every button on a summary that has not loaded.
  const blocked = !running && readiness != null && !readiness.ready;

  if (!blocked) {
    // ONE button in the bottom slot, whose meaning follows the state. A second, separate
    // Stop button was tried and could not fit: the cells are `flex: 1 1 0` on an
    // eight-node track, so two buttons clipped their own card. The running button is the
    // stop control — the elapsed time is the label, and pressing it cuts the run.
    return (
      <Button
        variant={running ? "oxblood" : "outline"}
        size="sm"
        className="board__run mt-auto"
        onClick={(e) => {
          e.stopPropagation();
          if (running) onStop(stage);
          else onRun(stage);
        }}
        disabled={stopping || (!running && pending)}
        title={running ? `Stop ${label}` : `Run ${label}`}
        // explicit, because the visible label while running is a clock — without this it
        // still announces as "Run" to a screen reader while it in fact stops the stage.
        aria-label={running ? `Stop ${label}` : `Run ${label}`}
      >
        {running ? (
          <>
            <IconStop size={12} />
            <span className="font-mono text-[11px]">{elapsedText}</span>
          </>
        ) : (
          <>
            <IconPlay size={13} /> Run
          </>
        )}
      </Button>
    );
  }

  const fix = readiness!.blocked_by;
  return (
    <div className="board__blocked mt-auto">
      <div className="board__blocked-why" title={readiness!.reason}>
        {readiness!.reason}
      </div>
      {fix ? (
        <Button
          variant="outline"
          size="sm"
          className="board__run"
          onClick={(e) => {
            e.stopPropagation();
            onRun(fix);
          }}
          disabled={pending}
          title={`Run ${fix} — the stage ${label} is waiting on`}
        >
          <IconPlay size={13} /> Run {fix}
        </Button>
      ) : (
        <Button
          variant="outline"
          size="sm"
          className="board__run"
          disabled
          title={readiness!.reason}
        >
          <IconPlay size={13} /> Run
        </Button>
      )}
    </div>
  );
}

/* Band A — the live status band above the tape. One shared selector
   (`activitySummary`) reports the whole floor as a Seam + label; the two mono
   tiles count runs/agents active in the window, and the chalk-thread packet
   advertises how many clips are in flight on the live run. Quiet by design when
   idle — it never shouts an empty floor. */
function BandA({
  band,
  flowOn,
  reduced,
}: {
  band: ActivitySummary;
  flowOn: boolean;
  reduced: boolean;
}) {
  const hasTiles = band.runsActive > 0 || band.agentsActive > 0;
  return (
    <div className={cx("board__live", `board__live--${band.seamState}`)}>
      <Seam state={band.seamState} width={52} flowOn={flowOn && !reduced} />
      <span className="board__live-label font-display">{band.label}</span>

      <span className="board__live-tiles">
        {hasTiles && (
          <>
            <LiveTile n={band.runsActive} label={band.runsActive === 1 ? "run" : "runs"} />
            <LiveTile n={band.agentsActive} label={band.agentsActive === 1 ? "agent" : "agents"} />
          </>
        )}
        {band.packetCount != null && (
          <span
            className="board__packet"
            title={`${band.packetCount} clip${band.packetCount === 1 ? "" : "s"} in flight`}
          >
            <span
              className="board__packet-stitch"
              style={{ animationPlayState: flowOn ? "running" : "paused" }}
              aria-hidden="true"
            />
            <span className="font-mono tnum">{band.packetCount}</span> in flight
          </span>
        )}
      </span>
    </div>
  );
}

function LiveTile({ n, label }: { n: number; label: string }) {
  return (
    <span className="board__live-tile">
      <span className="font-mono tnum board__live-tile-n">{n}</span>
      <span className="board__live-tile-l">{label}</span>
    </span>
  );
}

/* The tick-ruled tape the pipeline sits on. Pure CSS + one numbered major per
   node, re-spaced to whatever mark count NODES holds (7, now Discover leads)
   — the rhythm is re-spaced, not restyled. A brass index travels to the live
   stage when one is running. */
function TapeSpine({ liveIndex, flowOn }: { liveIndex: number; flowOn: boolean }) {
  const TICKS = 50;
  const MARKS = NODES.length; // 7
  return (
    <div className="tape-spine" aria-hidden="true">
      <div className="tape-spine__ticks">
        {Array.from({ length: TICKS }).map((_, i) => (
          <span
            key={i}
            className={cx("tape-spine__tick", i % 5 === 0 && "tape-spine__tick--major")}
          />
        ))}
      </div>
      {Array.from({ length: MARKS }).map((_, i) => (
        <span key={i} className="tape-spine__num" style={{ left: `${(i / (MARKS - 1)) * 100}%` }}>
          {Math.round((i / (MARKS - 1)) * 100)}
        </span>
      ))}
      {liveIndex >= 0 && (
        <span
          className={cx("tape-spine__index", flowOn && "tape-spine__index--flow")}
          style={{ left: `${(liveIndex / (MARKS - 1)) * 100}%` }}
        />
      )}
    </div>
  );
}
