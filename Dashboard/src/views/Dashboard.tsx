import { useState } from "react";
import { motion } from "framer-motion";
import { sectionMotion } from "../lib/motion";
import type { Tone } from "../lib/statusTone";
import { useShell } from "../App";
import {
  usePlatforms,
  useRunStage,
  useRunAll,
  useFactors,
  useProducers,
  useConfig,
} from "../lib/hooks";
import { PipelineBoard } from "../components/PipelineBoard";
import { CountUp } from "../components/gauges";
import { Badge, Button, Card, EmptyState, Eyebrow, SectionHead } from "../components/ui";
import { IconArrowRight, IconPlay, IconCheck, IconChevron } from "../components/icons";
import { grouped } from "../lib/format";
import { jobsFor } from "../lib/jobs";
import { statusTone } from "../lib/statusTone";
import { humanizeAgent } from "../lib/agents";
import { deriveOnboarding } from "../lib/onboarding";
import { requestConfigFocus } from "../lib/nav";
import { cx } from "../lib/cx";
import type { Job, PlatformSummary, Producer, Stage } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";
import { useReducedMotion } from "../lib/hooks";

export function Dashboard({ onNavigate }: { onNavigate: (v: ViewKey) => void }) {
  const { platform, jobs } = useShell();
  const platformsQ = usePlatforms();
  const summary = platformsQ.data?.find((p) => p.platform === platform);
  const factorsQ = useFactors(platform);
  const runAll = useRunAll(platform);
  const configQ = useConfig(platform);

  // First-run onboarding, derived from live state. While incomplete it owns the
  // "what do I do first" slot (and steers a new user to add a handle, not run a
  // scrape that must fail); once done it hands that slot back to NextStep.
  const ob = deriveOnboarding({
    pagesCount: configQ.data?.pages.length ?? 0,
    summary,
    jobs: jobsFor(jobs, platform),
  });

  const tiles = [
    { label: "Reels mined", value: summary?.items ?? 0, accent: true },
    { label: "Tier · Viral", value: summary?.viral ?? 0 },
    { label: "Creators tracked", value: summary?.creators ?? 0 },
    { label: "Clips persisted", value: summary?.media_ready ?? 0 },
  ];

  return (
    <div className="flex flex-col gap-7">
      {/* first-run checklist — only while the install is incomplete */}
      {!ob.complete && <GettingStarted ob={ob} onNavigate={onNavigate} />}

      {/* stat tiles */}
      <div className="stat-row">
        {tiles.map((t, i) => (
          <StatTile key={t.label} {...t} index={i} />
        ))}
      </div>

      {/* the hero: pipeline board */}
      <Card className="p-5 md:p-6">
        <SectionHead
          eyebrow="Live pipeline"
          title="The Board"
          right={
            <div className="flex items-center gap-3">
              <span className="eyebrow hidden sm:block">
                {platform} · {summary?.has_data ? "corpus loaded" : "no data"}
              </span>
              <Button
                variant="primary"
                size="sm"
                onClick={() => runAll.mutate()}
                disabled={runAll.isPending}
              >
                <IconPlay size={14} />
                {runAll.isPending ? "Running…" : "Run full pipeline"}
              </Button>
            </div>
          }
        />
        <PipelineBoard summary={summary} onNavigate={onNavigate} />
      </Card>

      {/* agent floor: quick links into each agent's live board */}
      <AgentsStrip />

      {/* next step banner — the post-onboarding nudge, once the checklist is done */}
      {ob.complete && <NextStep summary={summary} onNavigate={onNavigate} />}

      {/* a quick read on what's working, pulled from the corpus.
          Always rendered, even with nothing in it — on a fresh install the shape of the
          board should still show you what this app is going to tell you. `baseline` is
          null until there is a corpus to average, so it is never formatted blind. */}
      <Card className="p-5 md:p-6">
        <SectionHead
          eyebrow={
            factorsQ.data?.baseline != null
              ? `Baseline ${factorsQ.data.baseline.toFixed(0)} · lift vs baseline`
              : "Lift vs baseline"
          }
          title="What travels right now"
          right={
            <Button variant="ghost" size="sm" onClick={() => onNavigate("playbook")}>
              Full playbook <IconArrowRight size={14} />
            </Button>
          }
        />
        {factorsQ.data?.baseline == null ? (
          <EmptyState
            title="Nothing measured yet"
            hint="Scrape and analyze a corpus and the factors that lift virality show up here."
          />
        ) : (
          <div className="grid gap-2.5 sm:grid-cols-2">
            <FactorList title="Winners" tone="sage" items={factorsQ.data.winners.slice(0, 4)} />
            <FactorList title="Drags" tone="danger" items={factorsQ.data.losers.slice(0, 4)} />
          </div>
        )}
      </Card>

      {/* recent activity ledger */}
      <RecentActivity platform={platform} jobs={jobsFor(jobs, platform)} />
    </div>
  );
}

/* Quick links from the home board into each agent's live desk (Task 8). */
function AgentsStrip() {
  const { openAgent } = useShell();
  const producersQ = useProducers();
  const producers = producersQ.data ?? [];

  return (
    <Card className="p-5 md:p-6">
      <SectionHead eyebrow="Registered producers + analyzers" title="Agents · The Floor" />
      {producersQ.isLoading ? (
        <div className="skeleton" style={{ height: 90 }} />
      ) : producers.length === 0 ? (
        // Was `return null`, which made a whole card silently disappear on a fresh
        // install — the one moment someone most needs to be told an agent must register.
        <EmptyState
          title="No agents on the floor"
          hint="An agent appears here once it registers itself with the hub — e.g. cd SimilarContent && uv run cli.py register."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {producers.map((p) => (
            <AgentTile key={p.name} producer={p} onOpen={() => openAgent(p.name)} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AgentTile({ producer: p, onOpen }: { producer: Producer; onOpen: () => void }) {
  return (
    <button className="mat-2 p-3.5 text-left flex flex-col gap-2" onClick={onOpen}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-display truncate min-w-0">{humanizeAgent(p.name)}</span>
        {p.kind && <Badge tone="brass">{p.kind}</Badge>}
      </div>
      <p className="text-[12.5px] text-[var(--ink-dim)] truncate">
        {p.consumes.length > 0 ? `reads ${p.consumes.join(", ")}` : "idle · no inputs configured"}
      </p>
      <span className="eyebrow thread-text">
        Open desk <IconArrowRight size={13} />
      </span>
    </button>
  );
}

/* First-run checklist. The primary CTA auto-advances to the first incomplete step
   and ACTS in place — opens Config on the watchlist for step 1, fires the stage for
   steps 2–3 — so a new user never has to guess where to go next. */
function GettingStarted({
  ob,
  onNavigate,
}: {
  ob: ReturnType<typeof deriveOnboarding>;
  onNavigate: (v: ViewKey) => void;
}) {
  const { platform, jobs } = useShell();
  const run = useRunStage(platform);
  const active = ob.steps[ob.activeIndex];
  const doneCount = ob.steps.filter((s) => s.done).length;

  const stageOf: Partial<Record<string, Stage>> = { scrape: "scrape", analyze: "analyze" };
  const activeStage = stageOf[active.key];
  const running =
    !!activeStage &&
    jobsFor(jobs, platform).some(
      (j) => j.stage === activeStage && (j.status === "running" || j.status === "queued"),
    );

  function act() {
    if (active.key === "handle") {
      requestConfigFocus("pages");
      onNavigate("config");
    } else if (activeStage) {
      run.mutate(activeStage);
    }
  }
  const ctaLabel =
    active.key === "handle" ? "Add handle" : active.key === "scrape" ? "Run Scrape" : "Run Analyze";

  return (
    <Card className="p-5 md:p-6">
      <SectionHead
        eyebrow="First run"
        title="Getting started"
        right={
          <span className="eyebrow hidden sm:block">
            {doneCount} / {ob.steps.length} done
          </span>
        }
      />
      <ol className="getting-started">
        {ob.steps.map((s, i) => (
          <li
            key={s.key}
            className={cx(
              "getting-started__step",
              s.done && "is-done",
              i === ob.activeIndex && "is-active",
            )}
          >
            <span className="getting-started__mark" aria-hidden="true">
              {s.done ? <IconCheck size={13} /> : i + 1}
            </span>
            <div className="min-w-0">
              <div className="getting-started__label">{s.label}</div>
              {i === ob.activeIndex && <div className="getting-started__hint">{s.hint}</div>}
            </div>
          </li>
        ))}
      </ol>
      <div className="getting-started__cta">
        <Button variant="primary" onClick={act} disabled={running || run.isPending}>
          {running ? (
            "Running…"
          ) : (
            <>
              {ctaLabel} <IconArrowRight size={15} />
            </>
          )}
        </Button>
      </div>
    </Card>
  );
}

function StatTile({
  label,
  value,
  accent,
  index,
}: {
  label: string;
  value: number;
  accent?: boolean;
  index: number;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.div {...sectionMotion(index, reduced)} className="stat-tile mat">
      <Eyebrow>{label}</Eyebrow>
      <div className={accent ? "stat-tile__num thread-text" : "stat-tile__num"}>
        <CountUp to={value} format={(n) => grouped(n)} />
      </div>
    </motion.div>
  );
}

function FactorList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "sage" | "danger";
  items: { feature: string; bucket: string; lift: number; n: number }[];
}) {
  const color = tone === "sage" ? "var(--sage-ink)" : "var(--danger)";
  return (
    <div className="mat-2 p-3.5">
      <div className="eyebrow mb-2" style={{ color }}>
        {tone === "sage" ? "▲ " : "▼ "}
        {title}
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((f, i) => (
          <li key={i} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="truncate min-w-0">
              <span className="text-[var(--ink-dim)]">{f.feature}</span>{" "}
              <span className="text-[var(--ink)]">{f.bucket}</span>
            </span>
            <span className="font-mono tnum text-[12px]" style={{ color }}>
              {f.lift > 0 ? "+" : ""}
              {f.lift.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* A heuristic "do this next" prompt — the agent's suggested move. */
function NextStep({
  summary,
  onNavigate,
}: {
  summary?: PlatformSummary;
  onNavigate: (v: ViewKey) => void;
}) {
  const { platform } = useShell();
  const run = useRunStage(platform);

  // NextStep only renders once onboarding is complete (a scored corpus exists), so
  // the old "no corpus yet → Run Scrape" branch is gone — GettingStarted owns that
  // phase. This is purely the post-onboarding nudge: persist media, then propose.
  let stage: Stage | null = null;
  let text: string;
  let cta: string;
  if (summary && summary.media_ready === 0) {
    stage = "media";
    text = `${summary.viral} viral reels scored, but none saved for inline play. Persist the top clips.`;
    cta = "Run Media";
  } else {
    text = `${summary?.items ?? 0} reels scored across ${summary?.creators ?? 0} creators. Draft a proposal from the top cluster.`;
    cta = "Open Studio";
  }

  return (
    <div className="next-step">
      <div className="next-step__sheen sheen-sweep" aria-hidden="true" />
      <div className="next-step__body">
        <Eyebrow>Next step</Eyebrow>
        <p className="next-step__text">{text}</p>
      </div>
      <Button
        variant="primary"
        onClick={() => (stage ? run.mutate(stage) : onNavigate("proposals"))}
        disabled={run.isPending}
      >
        {cta} <IconArrowRight size={15} />
      </Button>
    </div>
  );
}

function RecentActivity({
  platform,
  jobs,
}: {
  platform: string;
  jobs: ReturnType<typeof jobsFor>;
}) {
  if (jobs.length === 0) return null;
  return (
    <Card className="p-5">
      <SectionHead eyebrow={`${platform} · job ledger`} title="Recent runs" />
      <div className="flex flex-col divide-y divide-[var(--line)]">
        {jobs.slice(0, 6).map((j, i) => (
          <JobRow key={i} job={j} />
        ))}
      </div>
    </Card>
  );
}

/* One row of the Recent-runs ledger. A failed job that captured stderr becomes a
   click-to-expand row revealing the full tail — so "scrape failed" no longer means
   hunting through terminal output to learn WHY (e.g. no handles on the watchlist). */
function JobRow({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  const canExpand = job.status === "error" && !!job.tail;
  const toggle = () => canExpand && setOpen((o) => !o);
  return (
    <div className="py-2">
      <div
        className={cx(
          "flex items-center justify-between text-[13px]",
          canExpand && "cursor-pointer",
        )}
        onClick={toggle}
        role={canExpand ? "button" : undefined}
        tabIndex={canExpand ? 0 : undefined}
        aria-expanded={canExpand ? open : undefined}
        onKeyDown={
          canExpand
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle();
                }
              }
            : undefined
        }
      >
        <span className="font-mono text-[12px] text-[var(--ink-dim)] w-24 flex items-center gap-1 min-w-0">
          {canExpand && (
            <IconChevron
              size={12}
              className={cx("transition-transform shrink-0", open && "rotate-90")}
            />
          )}
          <span className="truncate">{job.stage}</span>
        </span>
        <span
          className="font-mono text-[11px] uppercase tracking-wide"
          style={{ color: STATUS_INK[statusTone("pipeline-job", job.status)] }}
        >
          {job.status}
        </span>
        <span className="font-mono text-[11px] text-[var(--ink-faint)] tnum">
          rc {job.rc ?? "—"}
        </span>
      </div>
      {canExpand && open && <pre className="job-tail">{job.tail}</pre>}
    </div>
  );
}

/* Tone → ink-color for the RecentActivity job ledger. Mirrors the shared
   statusTone decision (statusTone.ts) so the ledger never recomputes its
   own switch — sage = done, danger = error, oxblood = working, ink = idle. */
const STATUS_INK: Record<Tone, string> = {
  sage: "var(--sage-ink)",
  danger: "var(--danger)",
  oxblood: "var(--oxblood-ink)",
  amber: "var(--amber)",
  brass: "var(--brass-ink)",
  neutral: "var(--ink-dim)",
};
