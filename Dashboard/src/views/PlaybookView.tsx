import { useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
// recharts 3: `TooltipContentProps` is what a custom `content` renderer receives; the older
// `TooltipProps` now describes only what <Tooltip> itself accepts.
import type { TooltipContentProps } from "recharts";
import { useShell } from "../App";
import { useFactors, useInsights, useNow, usePageVisible, useReducedMotion } from "../lib/hooks";
import { Badge, Card, EmptyState, Eyebrow, SectionHead } from "../components/ui";
import { CountUp, SignalRing } from "../components/gauges";
import { Seam } from "../components/Seam";
import { ChalkCircle } from "../components/ChalkCircle";
import { LiftRow, ConfidenceDot } from "../components/LiftRow";
import { IconInsights } from "../components/icons";
import { sectionMotion } from "../lib/motion";
import { AddPagesCta } from "../components/AddPagesButton";
import type { ViewKey } from "../components/Sidebar";
import { cx } from "../lib/cx";
import {
  drags,
  featureBest,
  fieldExtent,
  formula,
  ladder,
  partitionInsights,
  relativeTime,
  winners,
  type LadderRow,
} from "../lib/playbookModel";
import type { Factor, Insight } from "../lib/types";

export function PlaybookView({ onNavigate }: { onNavigate?: (v: ViewKey) => void }) {
  const { platform } = useShell();
  const factorsQ = useFactors(platform);
  const insightsQ = useInsights();
  const reduced = useReducedMotion();
  const visible = usePageVisible();
  const nowSec = useNow() / 1000;

  const factors = factorsQ.data;

  const win = useMemo(() => winners(factors), [factors]);
  const drag = useMemo(() => drags(factors), [factors]);
  const best = useMemo(() => featureBest(factors), [factors]);
  const formulaStr = useMemo(() => formula(factors), [factors]);
  const ladderRows = useMemo(() => ladder(factors), [factors]);
  const extent = useMemo(() => fieldExtent(factors), [factors]);
  const parts = useMemo(
    () => partitionInsights(insightsQ.data, platform),
    [insightsQ.data, platform],
  );

  // Two different things: what the ruler and ladder need to do arithmetic with, and what we
  // are willing to print. With no corpus there is no measured baseline, and showing "50.0"
  // states a measurement that was never taken — so the math falls back, the display does not.
  const baseline = factors?.baseline ?? 50;
  const measured = factors?.baseline != null;
  const winnersRef = useRef<HTMLDivElement>(null);

  // index position of the 0-lift baseline on the worst→best ruler
  const baselinePos =
    extent.max === extent.min
      ? 50
      : Math.max(0, Math.min(100, ((0 - extent.min) / (extent.max - extent.min)) * 100));

  function scrollToFeature(feature: string) {
    const el = document.getElementById(`pf-${feature}`) ?? winnersRef.current;
    el?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  }

  const factorsEmpty =
    !!factors &&
    win.rows.length === 0 &&
    drag.rows.length === 0 &&
    win.noise.length === 0 &&
    drag.noise.length === 0;
  const showFactorSections = !!factors && !factorsEmpty;

  // first-occurrence anchor ids so a ring click lands on that feature's row
  const seenFeatures = new Set<string>();
  function anchorFor(feature: string): string | undefined {
    if (seenFeatures.has(feature)) return undefined;
    seenFeatures.add(feature);
    return `pf-${feature}`;
  }

  const onlyWinners = win.rows.length > 0 && drag.rows.length === 0;
  const onlyDrags = drag.rows.length > 0 && win.rows.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {/* §2 — header spine */}
      <motion.div {...sectionMotion(0, reduced)}>
        <SectionHead
          eyebrow="Corpus · what wins / what drags"
          title="The Playbook"
          right={
            <Eyebrow>
              baseline{" "}
              <span className="font-mono tnum thread-text">
                {measured ? <CountUp to={baseline} decimals={1} /> : "—"}
              </span>
            </Eyebrow>
          }
        />
        <p className="text-[13px] text-[var(--ink-dim)] -mt-2 mb-3 max-w-[70ch]">
          The formula to run right now, the evidence behind it, the full ranked field, and the
          agents' shared learnings — measured against a baseline virality of{" "}
          {measured ? baseline.toFixed(1) : "—"}.
        </p>

        {/* honestly-labeled tape ruler: left = worst lift, brass index = baseline, right = best */}
        <div className="tape-spine playbook-ruler" aria-hidden="true">
          <div className="tape-spine__ticks">
            {Array.from({ length: 21 }).map((_, i) => (
              <span
                key={i}
                className={cx("tape-spine__tick", i % 5 === 0 && "tape-spine__tick--major")}
              />
            ))}
          </div>
          <span
            className="tape-spine__index"
            style={{ left: `${baselinePos}%` }}
            title={measured ? `baseline ${baseline.toFixed(1)}` : "baseline — not measured yet"}
          />
        </div>
        {/* the "baseline" caption sits under its brass index (baselinePos), not
            centered, so the label lines up with the marker it names. */}
        <div className="relative mt-1.5 h-[14px]">
          <span className="absolute left-0 font-mono text-[10px] text-[var(--ink-faint)]">
            {factors ? `${extent.min.toFixed(1)} lift` : "corpus not built yet"}
          </span>
          <span
            className="absolute font-mono text-[10px] text-[var(--ink-dim)]"
            style={{ left: `${baselinePos}%`, transform: "translateX(-50%)" }}
          >
            baseline
          </span>
          <span className="absolute right-0 font-mono text-[10px] text-[var(--ink-faint)]">
            {factors ? `+${extent.max.toFixed(1)} lift` : ""}
          </span>
        </div>

        {/* confidence legend — the n thresholds the old view collected but hid */}
        <div className="flex items-center gap-4 mt-2.5 flex-wrap">
          <LegendDot conf="solid" label="solid · n ≥ 80" />
          <LegendDot conf="thin" label="thin · 25 ≤ n < 80" />
          <LegendDot conf="noise" label="noise · n < 25" />
        </div>
      </motion.div>

      {/* §3 — THE FORMULA hero */}
      {showFactorSections && best.length > 0 && (
        <motion.div {...sectionMotion(1, reduced)}>
          <Card
            className="p-5"
            style={{
              background: "var(--thread-wash)",
              border: "1px solid var(--line-strong)",
            }}
          >
            <Eyebrow>
              {formulaStr ? "If you shipped one clip today" : "Early read — thin sample"}
            </Eyebrow>
            {formulaStr ? (
              <h3 className="font-display text-[22px] leading-tight mt-1 text-[var(--ink)]">
                {formulaStr}
              </h3>
            ) : (
              <h3 className="font-display text-lg mt-1 text-[var(--ink-dim)]">
                Not enough confident features to call a full formula yet — the strongest reads so
                far:
              </h3>
            )}
            <div className="flex flex-wrap gap-6 mt-4">
              {best.map((b) => (
                <button
                  key={b.feature}
                  type="button"
                  onClick={() => scrollToFeature(b.feature)}
                  className="flex flex-col items-center gap-1 text-center"
                  aria-label={`Jump to ${b.feature} rows`}
                >
                  <ChalkCircle>
                    <SignalRing
                      value={b.mean_score}
                      max={100}
                      label={b.feature}
                      color="var(--sage)"
                    />
                  </ChalkCircle>
                  <span className="text-[11px] text-[var(--ink-dim)] max-w-[9rem] truncate">
                    {b.bucket}
                  </span>
                  <span className="font-mono tnum text-[12px]" style={{ color: "var(--sage-ink)" }}>
                    +<CountUp to={b.lift} decimals={1} />
                  </span>
                </button>
              ))}
            </div>
          </Card>
        </motion.div>
      )}
      {showFactorSections && best.length === 0 && (
        <motion.div {...sectionMotion(1, reduced)}>
          <Card
            className="p-5"
            style={{ background: "var(--thread-wash)", border: "1px solid var(--line-strong)" }}
          >
            <Eyebrow>The formula</Eyebrow>
            <p className="text-[14px] text-[var(--ink-dim)] mt-1">
              Not enough scored clips to call a formula yet — see the field below.
            </p>
          </Card>
        </motion.div>
      )}

      {/* §4 — Winners │ Drags */}
      <motion.div {...sectionMotion(2, reduced)} ref={winnersRef}>
        {factorsQ.isLoading ? (
          <div className="skeleton" style={{ height: 240 }} />
        ) : factorsEmpty || !factors ? (
          <Card className="p-5">
            <EmptyState
              icon={<IconInsights size={28} />}
              title={`No corpus for ${platform} yet`}
              hint="Run Analyze to score clips — factors appear once there's a scored sample."
              action={<AddPagesCta onNavigate={onNavigate} />}
            />
          </Card>
        ) : (
          <div
            className={cx(
              onlyWinners || onlyDrags ? "flex flex-col gap-4" : "grid gap-4 md:grid-cols-2",
            )}
          >
            {win.rows.length > 0 && (
              <FactorColumn
                title="What wins right now"
                glyph="▲"
                glyphColor="var(--sage-ink)"
                tone="sage"
                rows={win.rows}
                noise={win.noise}
                baseline={baseline}
                anchorFor={anchorFor}
              />
            )}
            {onlyWinners && (
              <p className="mat-2 text-[13px] text-[var(--ink-dim)] p-3">
                No factor is dragging below baseline right now — a genuinely clean field.
              </p>
            )}
            {drag.rows.length > 0 && (
              <FactorColumn
                title="What drags right now"
                glyph="▼"
                glyphColor="var(--danger)"
                tone="danger"
                rows={drag.rows}
                noise={drag.noise}
                baseline={baseline}
              />
            )}
            {onlyDrags && (
              <p className="mat-2 text-[13px] text-[var(--ink-dim)] p-3">
                Nothing is clearly winning above baseline yet.
              </p>
            )}
          </div>
        )}
      </motion.div>

      {/* §5 — Lift ladder */}
      {showFactorSections && ladderRows.length >= 3 && (
        <motion.div {...sectionMotion(3, reduced)}>
          <LiftLadder rows={ladderRows} reduced={reduced} visible={visible} baseline={baseline} />
        </motion.div>
      )}

      {/* §6 — Shared exchange */}
      <motion.div {...sectionMotion(4, reduced)}>
        <Card className="p-5">
          <SectionHead
            eyebrow="Shared exchange · memory/shared"
            title="Cross-agent learnings"
            right={<Eyebrow>all platforms + shared</Eyebrow>}
          />
          {insightsQ.isLoading ? (
            <div className="skeleton" style={{ height: 160 }} />
          ) : parts.methods.length + parts.findings.length + parts.antipatterns.length === 0 ? (
            <EmptyState
              icon={<IconInsights size={28} />}
              title="Nothing logged yet"
              hint="Findings agents share across platforms land here."
            />
          ) : (
            <ul className="log-thread">
              <InsightBand
                label="Methods"
                tone="brass"
                state="done"
                rows={parts.methods}
                now={nowSec}
              />
              <InsightBand
                label="Findings"
                tone="sage"
                state="done"
                rows={parts.findings}
                now={nowSec}
              />
              <InsightBand
                label="Antipatterns"
                tone="danger"
                state="error"
                rows={parts.antipatterns}
                now={nowSec}
              />
            </ul>
          )}
        </Card>
      </motion.div>
    </div>
  );
}

function LegendDot({ conf, label }: { conf: "solid" | "thin" | "noise"; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <ConfidenceDot conf={conf} color="var(--sage)" />
      <span className="font-mono text-[10px] text-[var(--ink-dim)]">{label}</span>
    </span>
  );
}

function FactorColumn({
  title,
  glyph,
  glyphColor,
  tone,
  rows,
  noise,
  baseline,
  anchorFor,
}: {
  title: string;
  glyph: string;
  glyphColor: string;
  tone: "sage" | "danger";
  rows: Factor[];
  noise: Factor[];
  baseline: number;
  anchorFor?: (feature: string) => string | undefined;
}) {
  const [showNoise, setShowNoise] = useState(false);
  const shown = rows.slice(0, 6);
  return (
    <Card className="p-5">
      <Eyebrow className="mb-2">
        <span style={{ color: glyphColor }}>{glyph}</span> {title}
      </Eyebrow>
      <div>
        {shown.map((f, i) => (
          <LiftRow
            key={`${f.feature}-${f.bucket}-${i}`}
            f={f}
            baseline={baseline}
            tone={tone}
            id={anchorFor?.(f.feature)}
          />
        ))}
      </div>
      {noise.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            className="font-mono text-[11px] text-[var(--ink-dim)] hover:text-[var(--ink)]"
            onClick={() => setShowNoise((v) => !v)}
            aria-expanded={showNoise}
          >
            {noise.length} low-confidence bucket{noise.length === 1 ? "" : "s"} (n&lt;25){" "}
            {showNoise ? "— hide" : "— show"}
          </button>
          {showNoise && (
            <div className="mt-1 opacity-70">
              {noise.map((f, i) => (
                <LiftRow
                  key={`noise-${f.feature}-${f.bucket}-${i}`}
                  f={f}
                  baseline={baseline}
                  tone={tone}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function LiftLadder({
  rows,
  reduced,
  visible,
  baseline,
}: {
  rows: LadderRow[];
  reduced: boolean;
  visible: boolean;
  baseline: number;
}) {
  const [open, setOpen] = useState(false);
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.lift)));
  return (
    <Card className="p-5">
      <div className="flex items-end justify-between gap-3 mb-3 flex-wrap">
        <div>
          <Eyebrow>Lift ladder · the full field</Eyebrow>
          <h3 className="font-display text-lg">
            Every scored bucket vs baseline {baseline.toFixed(1)}
          </h3>
        </div>
        <button
          type="button"
          className="md:hidden font-mono text-[11px] text-[var(--ink-dim)] hover:text-[var(--ink)]"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "Hide" : `Show full field (${rows.length})`}
        </button>
      </div>
      <div
        className={cx(open ? "block" : "hidden", "md:block")}
        style={{ width: "100%", height: Math.max(200, rows.length * 30) }}
      >
        <ResponsiveContainer>
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 20, bottom: 4, left: 4 }}
          >
            <XAxis
              type="number"
              domain={[-maxAbs, maxAbs]}
              tick={{ fill: "var(--ink-dim)", fontSize: 11, fontFamily: "var(--font-mono)" }}
              stroke="var(--line-strong)"
            />
            <YAxis
              type="category"
              dataKey="label"
              width={140}
              tick={{ fill: "var(--ink-dim)", fontSize: 10, fontFamily: "var(--font-mono)" }}
              stroke="var(--line-strong)"
            />
            <ReferenceLine x={0} stroke="var(--brass)" strokeDasharray="6 4" />
            <Tooltip
              content={LadderTooltip}
              cursor={{ fill: "var(--surface-3)", fillOpacity: 0.4 }}
            />
            <Bar dataKey="lift" isAnimationActive={!reduced && visible} radius={[0, 2, 2, 0]}>
              {rows.map((r) => (
                <Cell
                  key={r.label}
                  fill={r.lift >= 0 ? "var(--sage)" : "var(--danger)"}
                  fillOpacity={r.conf === "thin" ? 0.45 : 1}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

const ladderTooltipStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line-strong)",
  borderRadius: "var(--r-sm)",
  padding: "8px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  color: "var(--ink)",
};

function LadderTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload || !payload.length) return null;
  const r = payload[0].payload as LadderRow;
  return (
    <div style={ladderTooltipStyle}>
      <div style={{ color: "var(--ink-dim)", fontSize: 10.5 }}>{r.label}</div>
      <div className="tnum" style={{ color: r.lift >= 0 ? "var(--sage-ink)" : "var(--danger)" }}>
        lift {r.lift >= 0 ? "+" : ""}
        {r.lift.toFixed(1)} · score {r.mean_score.toFixed(1)}
      </div>
      <div style={{ color: "var(--ink-dim)", fontSize: 11 }}>
        n={r.n} · {r.conf}
      </div>
    </div>
  );
}

function InsightBand({
  label,
  tone,
  state,
  rows,
  now,
}: {
  label: string;
  tone: "brass" | "sage" | "danger";
  state: "done" | "error";
  rows: Insight[];
  now: number;
}) {
  if (rows.length === 0) return null;
  return (
    <li className="mb-4 last:mb-0">
      <div className="mb-1.5">
        <Badge tone={tone}>{label}</Badge>
      </div>
      <div className="flex flex-col gap-1">
        {rows.map((ins, i) => (
          <div key={i} className="mat flex items-start gap-2.5 p-2.5">
            <div className="shrink-0 pt-0.5">
              <Seam state={state} width={44} />
            </div>
            <div className="min-w-0 flex-1 chalk-underline">
              <p className="text-[13px] text-[var(--ink-2)] break-words">{ins.text}</p>
              {ins.tags?.length > 0 && (
                <div className="flex gap-1.5 mt-1 flex-wrap">
                  {ins.tags.map((t) => (
                    <span key={t} className="font-mono text-[10px] text-[var(--ink-faint)]">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <span className="font-mono text-[10px] text-[var(--ink-dim)] shrink-0">
              {relativeTime(ins.ts, now)}
            </span>
          </div>
        ))}
      </div>
    </li>
  );
}
