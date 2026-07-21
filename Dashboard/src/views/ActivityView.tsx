import { useMemo, useState, type CSSProperties } from "react";
import { motion } from "framer-motion";
import { useLogStream, useNow, usePageVisible, useReducedMotion } from "../lib/hooks";
import { Badge, Card, EmptyState, Eyebrow, SectionHead, Select } from "../components/ui";
import { IconActivity } from "../components/icons";
import { Seam, type SeamState } from "../components/Seam";
import { relativeTime } from "../lib/playbookModel";
import { statusTone, type Tone } from "../lib/statusTone";
import { sectionMotion } from "../lib/motion";
import { cx } from "../lib/cx";
import {
  activityFacets,
  floorSummary,
  groupByRun,
  isLive,
  streamState,
  throughputBins,
  type RunGroup,
  type ThroughputBin,
} from "../lib/activityModel";
import type { LogEvent } from "../lib/types";

/* The Floor Log, rebuilt as run-threads (R6). The flat central log is the union
   of many agents' lifecycle events (§10.1); the eye reads them best as one
   thread per run — a needle-and-thread spine that sews itself while the run is
   live (log-thread--live) and knots when it settles. All the parsing lives in
   the shared activityModel; this view only lays it out. */

const SYNTHETIC_RUN = "∅";

// SeamState → badge tone + floor-vocabulary label, matching the Seam colors and
// streamState's "Sewing / Knotted / Snapped" wording exactly.
function stateTone(state: SeamState): Tone {
  if (state === "working") return "oxblood";
  if (state === "done") return "sage";
  if (state === "error") return "danger";
  return "neutral";
}
function stateLabel(state: SeamState): string {
  if (state === "working") return "sewing";
  if (state === "done") return "knotted";
  if (state === "error") return "snapped";
  return "idle";
}

function runKey(ev: LogEvent): string {
  return `${ev.agent ?? ""}:${ev.run_id ?? SYNTHETIC_RUN}`;
}

export function ActivityView() {
  const { events, connected } = useLogStream(300);
  const nowSec = useNow(5_000) / 1000;
  const reduced = useReducedMotion();
  const visible = usePageVisible();

  const [agent, setAgent] = useState("all");
  const [level, setLevel] = useState("all");
  const [run, setRun] = useState("all");

  // facets + floor vitals are computed over the UNFILTERED ring so picking one
  // filter never collapses the others or distorts the pulse (activityModel §).
  const facets = useMemo(() => activityFacets(events), [events]);
  // the level filter compares lowercased, so the options must be lowercased +
  // deduped too — otherwise a "WARN"/"Error" event yields an option that, once
  // picked, matches nothing.
  const levelOptions = useMemo(
    () => Array.from(new Set(facets.levels.map((l) => l.toLowerCase()))).sort(),
    [facets.levels],
  );
  const allGroups = useMemo(() => groupByRun(events), [events]);
  const stream = useMemo(() => streamState(allGroups, nowSec), [allGroups, nowSec]);
  const floor = useMemo(() => floorSummary(allGroups, events, nowSec), [allGroups, events, nowSec]);
  const bins = useMemo(() => throughputBins(events, nowSec, 300, 15), [events, nowSec]);

  // a run may have dropped out of the facet list; keep the selection valid
  const runExists = run === "all" || facets.runs.some((r) => r.key === run);

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        if (agent !== "all" && e.agent !== agent) return false;
        if (level !== "all" && (e.level ?? "").toLowerCase() !== level) return false;
        if (runExists && run !== "all" && runKey(e) !== run) return false;
        return true;
      }),
    [events, agent, level, run, runExists],
  );

  const groups = useMemo(() => groupByRun(filtered), [filtered]);
  // the vertical stitch only animates on live runs, stilled when the tab hides
  // (reduced-motion is handled by the global clamp).
  const flowOn = !reduced && visible;

  return (
    <div className="flex flex-col gap-4">
      <motion.div {...sectionMotion(0, reduced)}>
        <SectionHead
          eyebrow="Central log · lifecycle events"
          title="The Floor Log"
          right={
            <span className="flex items-center gap-3">
              <span className="flex items-center gap-1.5">
                <Seam state={connected ? stream.state : "idle"} width={44} flowOn={flowOn} />
                <span className="font-mono text-[11px] text-[var(--ink-dim)]">
                  {connected ? stream.label : "Offline"}
                </span>
              </span>
              {/* the SSE transport pill — only when connected, so it never
                  doubles the Seam's "Offline" label; the shared Badge primitive
                  replaces the bespoke .activity-live chip. */}
              {connected && <Badge tone="sage">streaming</Badge>}
            </span>
          }
        />
      </motion.div>

      {/* §B — the pulse ribbon: floor vitals + throughput sparkline */}
      <motion.div {...sectionMotion(1, reduced)}>
        <PulseRibbon
          active={floor.active}
          done={floor.done}
          errored={floor.errored}
          agentsLive={floor.agentsLive}
          perMin={floor.perMin}
          bins={bins}
          reduced={reduced}
        />
      </motion.div>

      <div className="corpus-toolbar">
        <Select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Filter agent">
          <option value="all">All agents</option>
          {facets.agents.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </Select>
        <Select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Filter level">
          <option value="all">All levels</option>
          {levelOptions.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </Select>
        <Select
          value={runExists ? run : "all"}
          onChange={(e) => setRun(e.target.value)}
          aria-label="Filter run"
        >
          <option value="all">All runs</option>
          {facets.runs.map((r) => (
            <option key={r.key} value={r.key}>
              {r.agent} · {r.runId === SYNTHETIC_RUN ? "loose events" : r.runId}
            </option>
          ))}
        </Select>
        <Eyebrow>
          {groups.length} {groups.length === 1 ? "thread" : "threads"}
        </Eyebrow>
      </div>

      {groups.length === 0 ? (
        !connected ? (
          <EmptyState
            icon={<IconActivity size={28} />}
            title="Reaching the floor…"
            hint="Not connected to the hub's event stream yet. Start the hub (python cli.py start) — the log sews itself here as agents run."
          />
        ) : (
          <EmptyState
            icon={<IconActivity size={28} />}
            title="Quiet floor"
            hint="Agents post lifecycle events here as they run. Kick off a stage from the Board to see the thread sew itself."
          />
        )
      ) : (
        <div className="flex flex-col gap-3">
          {groups.map((g, i) => (
            <motion.div key={g.key} {...sectionMotion(2 + i, reduced)}>
              <RunThread g={g} nowSec={nowSec} flowOn={flowOn} />
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunThread({ g, nowSec, flowOn }: { g: RunGroup; nowSec: number; flowOn: boolean }) {
  const live = isLive(g, nowSec);
  const tone = stateTone(g.state);
  const spineStyle = live
    ? ({ "--stitch-play": flowOn ? "running" : "paused" } as CSSProperties)
    : undefined;

  return (
    <Card className="p-4">
      <div className="run-thread__head">
        <span className="run-thread__agent font-mono">{g.agent || "unnamed"}</span>
        <Badge tone={tone}>{stateLabel(g.state)}</Badge>
        {g.total > 0 && (
          <span className="run-thread__counts font-mono">
            {g.done}/{g.total} done
            {g.failed > 0 && <span className="text-[var(--danger)]"> · {g.failed} failed</span>}
          </span>
        )}
        <span className="run-thread__run font-mono">
          {g.runId === SYNTHETIC_RUN ? "loose events" : `run ${g.runId}`}
        </span>
        {/* live feed → seconds-scale "just now / 5m ago", not the hour-grained
            agoFrom the corpus uses (else every fresh event reads "0h ago"). */}
        <span className="run-thread__time font-mono">{relativeTime(g.lastTs, nowSec)}</span>
      </div>

      <ol className={cx("log-thread", "mt-2", live && "log-thread--live")} style={spineStyle}>
        {g.events.map((e, i) => (
          <LogRow key={`${e.ts}-${i}`} ev={e} nowSec={nowSec} />
        ))}
      </ol>
    </Card>
  );
}

function LogRow({ ev, nowSec }: { ev: LogEvent; nowSec: number }) {
  const tone = statusTone("log-level", ev.level);
  const dataKeys = ev.data ? Object.entries(ev.data) : [];
  return (
    <li className={cx("log-row", `log-row--${tone}`)}>
      <span className="log-row__knot" aria-hidden="true" />
      <div className="log-row__body">
        <div className="log-row__top">
          <span className="log-row__event font-mono">{ev.event || "event"}</span>
          <Badge tone={tone}>{ev.level ?? "—"}</Badge>
          <span className="log-row__time font-mono">{relativeTime(ev.ts, nowSec)}</span>
        </div>
        {ev.msg && <div className="log-row__msg">{ev.msg}</div>}
        <div className="log-row__meta">
          {ev.platform && <span className="font-mono">{ev.platform}</span>}
          {ev.content_id && <span className="font-mono log-row__data">· {ev.content_id}</span>}
          {dataKeys.map(([k, v]) => (
            <span key={k} className="font-mono log-row__data">
              {k}={typeof v === "object" ? JSON.stringify(v) : String(v)}
            </span>
          ))}
        </div>
      </div>
    </li>
  );
}

function PulseRibbon({
  active,
  done,
  errored,
  agentsLive,
  perMin,
  bins,
  reduced,
}: {
  active: number;
  done: number;
  errored: number;
  agentsLive: number;
  perMin: number;
  bins: ThroughputBin[];
  reduced: boolean;
}) {
  return (
    <Card className="pulse">
      <PulseStat n={active} label="live" tone="oxblood" />
      <PulseStat n={done} label="knotted" tone="sage" />
      <PulseStat n={errored} label="snapped" tone="danger" />
      <PulseStat n={agentsLive} label={agentsLive === 1 ? "agent" : "agents"} tone="neutral" />
      <div className="pulse__spark">
        <Sparkline bins={bins} reduced={reduced} />
        <span className="pulse__rate font-mono tnum">
          {perMin}
          <span className="pulse__rate-unit">/min</span>
        </span>
      </div>
    </Card>
  );
}

function PulseStat({ n, label, tone }: { n: number; label: string; tone: Tone }) {
  return (
    <div className="pulse__stat">
      <span className={cx("pulse__stat-n font-mono tnum", `pulse__stat-n--${tone}`)}>{n}</span>
      <span className="pulse__stat-l">{label}</span>
    </div>
  );
}

/* A tiny throughput sparkline — fixed-length zero-filled bins from the shared
   model, so it's always a valid shape (flat when quiet, never an empty SVG). */
function Sparkline({ bins, reduced }: { bins: ThroughputBin[]; reduced: boolean }) {
  const w = 132;
  const h = 30;
  const gap = 1.5;
  const n = Math.max(1, bins.length);
  const bw = (w - gap * (n - 1)) / n;
  const max = Math.max(1, ...bins.map((b) => b.n));
  return (
    <svg
      className="pulse__spark-svg"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`throughput, last ${bins.length} bins`}
    >
      {bins.map((b, i) => {
        const bh = b.n === 0 ? 1.5 : Math.max(2, (b.n / max) * (h - 3));
        const x = i * (bw + gap);
        // most-recent bin (last) reads brass; the quiet tail stays faint
        const recent = i >= n - 3;
        return (
          <rect
            key={i}
            x={x}
            y={h - bh}
            width={bw}
            height={bh}
            rx={0.8}
            fill={b.n === 0 ? "var(--line-strong)" : recent ? "var(--brass)" : "var(--oxblood)"}
            fillOpacity={b.n === 0 ? 0.6 : 0.85}
            style={
              reduced ? undefined : { transition: "height 0.4s var(--ease), y 0.4s var(--ease)" }
            }
          />
        );
      })}
    </svg>
  );
}
