import { useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";
import { useEvals, usePageVisible, useReducedMotion } from "../lib/hooks";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Eyebrow,
  RangeSlider,
  SectionHead,
  Select,
} from "../components/ui";
import { CountUp, SignalRing, TapeGauge } from "../components/gauges";
import { Seam } from "../components/Seam";
import { IconEvals } from "../components/icons";
import { FixCard } from "../components/evals/FixCard";
import { CriterionReadout } from "../components/evals/CriterionReadout";
import {
  bySeries,
  criterionEntries,
  criterionMeans,
  facets,
  fixQueue,
  recordScore,
  scoreColor,
  seriesKey,
  TREND_GLYPH,
  type SeriesInfo,
  type SeriesPoint,
} from "../lib/evalModel";
import { sectionMotion } from "../lib/motion";
import { cx } from "../lib/cx";
import type { EvalRecord } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

// stable slice→color assignment from the cutting-room palette (no new hues);
// a 6th+ slice reuses a hue but switches to a dash pattern to stay legible.
const SERIES_COLORS = [
  "var(--brass)",
  "var(--sage)",
  "var(--oxblood-ink)",
  "var(--amber)",
  "var(--tier-above)",
];

const RANGE_OPTIONS: { key: string; label: string; days: number | null }[] = [
  { key: "7d", label: "Last 7 days", days: 7 },
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
  { key: "all", label: "All time", days: null },
];

export function EvalsView({ onNavigate }: { onNavigate: (v: ViewKey) => void }) {
  const [agent, setAgent] = useState("");
  const [type, setType] = useState("");
  const [range, setRange] = useState("all");
  const [threshold, setThreshold] = useState(85);
  const [isolated, setIsolated] = useState<Set<string>>(new Set());
  const [activeSlice, setActiveSlice] = useState<string | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<EvalRecord | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);

  const reduced = useReducedMotion();
  const visible = usePageVisible();

  const since = useMemo(() => {
    const opt = RANGE_OPTIONS.find((r) => r.key === range);
    if (!opt || opt.days == null) return undefined;
    return Math.floor(Date.now() / 1000) - opt.days * 86400;
  }, [range]);

  // server-filtered — drives every panel below
  const scopeQ = useEvals({ agent: agent || undefined, target_type: type || undefined, since });
  // unfiltered companion, for the pickers only, so choosing a filter never
  // collapses the other pickers down to a single remaining option
  const facetsQ = useEvals();

  // wrapped in their own useMemo (not just `data ?? []` inline) so downstream
  // useMemo deps below don't see a fresh array identity on every render.
  const rows = useMemo(() => scopeQ.data ?? [], [scopeQ.data]);
  const allRows = useMemo(() => facetsQ.data ?? [], [facetsQ.data]);
  const facetsData = useMemo(() => facets(allRows), [allRows]);
  const allSeriesKeysSorted = useMemo(
    () => Array.from(new Set(allRows.map(seriesKey))).sort(),
    [allRows],
  );

  function colorFor(key: string): string {
    const idx = allSeriesKeysSorted.indexOf(key);
    return SERIES_COLORS[(idx < 0 ? 0 : idx) % SERIES_COLORS.length];
  }
  function dashFor(key: string): string | undefined {
    const idx = allSeriesKeysSorted.indexOf(key);
    return idx >= SERIES_COLORS.length ? "5 3" : undefined;
  }

  const seriesMap = useMemo(() => bySeries(rows), [rows]);
  const xrayRows = activeSlice ? rows.filter((r) => seriesKey(r) === activeSlice) : rows;
  const criterionMap = useMemo(() => criterionMeans(xrayRows), [xrayRows]);
  const criterionData = useMemo(
    () => Array.from(criterionMap.entries()).map(([criterion, stat]) => ({ criterion, ...stat })),
    [criterionMap],
  );
  const mixedTypes = useMemo(
    () => new Set(xrayRows.map((r) => r.target_type)).size > 1,
    [xrayRows],
  );
  const unratedCount = useMemo(
    () => xrayRows.filter((r) => criterionEntries(r).length === 0).length,
    [xrayRows],
  );
  const queue = useMemo(() => fixQueue(rows, threshold), [rows, threshold]);

  const scoredOnly = useMemo(
    () =>
      rows
        .map((r) => ({ r, s: recordScore(r) }))
        .filter((x): x is { r: EvalRecord; s: number } => x.s != null),
    [rows],
  );
  const meanScore = scoredOnly.length
    ? scoredOnly.reduce((a, x) => a + x.s, 0) / scoredOnly.length
    : null;
  const unscoredCount = rows.length - scoredOnly.length;
  const accepts = rows.filter((r) => (r.verdict ?? "").toLowerCase() === "accept").length;
  const acceptPct = rows.length ? (accepts / rows.length) * 100 : 100;
  const sliceCount = seriesMap.size;

  const fixCardRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
  function registerFixCardRef(id: string, el: HTMLDivElement | null) {
    fixCardRefs.current.set(id, el);
  }
  function drillToRecord(rec: EvalRecord) {
    setSelectedRecord(rec);
    const el = fixCardRefs.current.get(rec.target_id);
    if (el) {
      el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
      setFlashId(rec.target_id);
      setTimeout(() => setFlashId(null), reduced ? 0 : 900);
    }
  }

  function toggleSeries(key: string, additive: boolean) {
    setIsolated((prev) => {
      const next = new Set(prev);
      if (additive) {
        if (next.has(key)) next.delete(key);
        else next.add(key);
      } else if (next.size === 1 && next.has(key)) {
        next.clear();
      } else {
        next.clear();
        next.add(key);
      }
      return next;
    });
  }

  function clearFilters() {
    setAgent("");
    setType("");
    setRange("all");
  }

  const loading = scopeQ.isLoading || facetsQ.isLoading;
  const noDataAtAll = !loading && allRows.length === 0;
  const filteredToZero = !loading && allRows.length > 0 && rows.length === 0;

  return (
    <div className="flex flex-col gap-5">
      <motion.div {...sectionMotion(0, reduced)}>
        <SectionHead
          eyebrow="Self-eval · quality now, outcome later"
          title="The Cutting Score"
          right={
            <Eyebrow>
              {queue.length} to fix · {scoredOnly.length} scored
            </Eyebrow>
          }
        />
      </motion.div>

      {!noDataAtAll && (
        <motion.div className="eval-filters" {...sectionMotion(1, reduced)}>
          <Select
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            aria-label="Filter by agent"
          >
            <option value="">All agents</option>
            {facetsData.agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
          <Select
            value={type}
            onChange={(e) => setType(e.target.value)}
            aria-label="Filter by target type"
          >
            <option value="">All target types</option>
            {facetsData.targetTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
          <Select
            value={range}
            onChange={(e) => setRange(e.target.value)}
            aria-label="Filter by range"
          >
            {RANGE_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </Select>
          <div className="eval-filters__threshold">
            <span className="eyebrow">Threshold</span>
            <RangeSlider
              value={threshold}
              min={50}
              max={100}
              step={1}
              onChange={setThreshold}
              aria-label="Score threshold"
            />
            <span className="font-mono tnum thread-text text-[13px]" aria-live="polite">
              {threshold}
            </span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => scopeQ.refetch()}>
            Refresh
          </Button>
        </motion.div>
      )}

      {loading ? (
        <div className="flex flex-col gap-5">
          <div className="skeleton" style={{ height: 100 }} />
          <div className="skeleton" style={{ height: 320 }} />
          <div className="skeleton" style={{ height: 220 }} />
        </div>
      ) : noDataAtAll ? (
        <EmptyState
          icon={<IconEvals size={28} />}
          title="No evaluations yet"
          hint="Every producing agent scores its own output against a rubric before publishing. Those scores land here as a trend."
        />
      ) : filteredToZero ? (
        <EmptyState
          icon={<IconEvals size={28} />}
          title="No evals match this slice"
          hint="Loosen the agent, target type, or range filter to see scored records."
          action={
            <Button variant="outline" onClick={clearFilters}>
              Clear filters
            </Button>
          }
        />
      ) : (
        <>
          {/* §2 score ribbon */}
          <motion.div className="eval-stat-row" {...sectionMotion(2, reduced)}>
            <StatTile i={0} label="Mean score" reduced={reduced}>
              {meanScore != null ? (
                <>
                  <div className="eval-stat__num font-display thread-text">
                    <CountUp to={meanScore} decimals={1} />
                  </div>
                  <TapeGauge
                    value={meanScore}
                    tierColor={scoreColor(meanScore)}
                    height={6}
                    showTicks={false}
                  />
                </>
              ) : (
                <div className="eval-stat__num font-display text-[var(--ink-faint)]">—</div>
              )}
            </StatTile>
            <StatTile i={1} label="Acceptance" reduced={reduced}>
              <div className="flex items-center gap-3">
                <SignalRing
                  value={accepts}
                  max={Math.max(1, rows.length)}
                  label="accept"
                  color={scoreColor(acceptPct)}
                />
                <span className="font-mono tnum text-[13px] text-[var(--ink-dim)]">
                  {accepts} of {rows.length}
                </span>
              </div>
            </StatTile>
            <StatTile i={2} label={`Below ${threshold}`} reduced={reduced}>
              <div
                className="eval-stat__num font-display"
                style={{ color: queue.length ? "var(--danger)" : "var(--ink)" }}
              >
                <CountUp to={queue.length} />
              </div>
            </StatTile>
            <StatTile i={3} label="Slices" reduced={reduced}>
              <div className="eval-stat__num font-display">
                <CountUp to={sliceCount} />
              </div>
              <span className="text-[11px] text-[var(--ink-faint)]">agent × target type</span>
            </StatTile>
          </motion.div>

          {/* §3 trend */}
          <motion.div {...sectionMotion(3, reduced)}>
            <Card className="p-5">
              <SectionHead
                eyebrow="Score trend"
                title="How quality moves over time"
                right={
                  <div className="flex gap-2 flex-wrap" role="group" aria-label="Toggle series">
                    {Array.from(seriesMap.values()).map((s) => {
                      const color = colorFor(s.key);
                      const pressed = isolated.size > 0 && isolated.has(s.key);
                      const dim = isolated.size > 0 && !isolated.has(s.key);
                      return (
                        <button
                          key={s.key}
                          type="button"
                          className="eval-legend eval-legend--btn"
                          aria-pressed={pressed}
                          aria-label={`Toggle ${s.key} series`}
                          style={{ opacity: dim ? 0.35 : 1 }}
                          onClick={(e) => toggleSeries(s.key, e.shiftKey)}
                        >
                          <span className="eval-legend__dot" style={{ background: color }} />
                          <span className="font-mono text-[11px]">{s.key}</span>
                          <span aria-hidden="true">{TREND_GLYPH[s.trend]}</span>
                        </button>
                      );
                    })}
                  </div>
                }
              />
              <div style={{ width: "100%", height: 300 }}>
                <ResponsiveContainer>
                  <LineChart margin={{ top: 6, right: 12, bottom: 4, left: -18 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="3 4" vertical={false} />
                    <XAxis
                      type="number"
                      dataKey="t"
                      domain={["dataMin", "dataMax"]}
                      scale="time"
                      tickFormatter={(t: number) =>
                        new Date(t).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                        })
                      }
                      tick={{
                        fill: "var(--ink-dim)",
                        fontSize: 11,
                        fontFamily: "var(--font-mono)",
                      }}
                      stroke="var(--line-strong)"
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{
                        fill: "var(--ink-dim)",
                        fontSize: 11,
                        fontFamily: "var(--font-mono)",
                      }}
                      stroke="var(--line-strong)"
                    />
                    <ReferenceArea
                      y1={0}
                      y2={60}
                      fill="var(--danger-wash)"
                      ifOverflow="extendDomain"
                    />
                    <ReferenceLine
                      y={60}
                      stroke="var(--amber)"
                      strokeDasharray="4 3"
                      label={{
                        value: "revise",
                        position: "insideTopLeft",
                        fill: "var(--amber)",
                        fontSize: 10,
                      }}
                    />
                    <ReferenceLine
                      y={threshold}
                      stroke="var(--sage)"
                      strokeDasharray="4 3"
                      label={{
                        value: "accept",
                        position: "insideTopLeft",
                        fill: "var(--sage-ink)",
                        fontSize: 10,
                      }}
                    />
                    <Tooltip content={EvalTooltipContent} />
                    {Array.from(seriesMap.values()).map((s) => {
                      const color = colorFor(s.key);
                      const dim = isolated.size > 0 && !isolated.has(s.key);
                      const opacity = dim ? 0.12 : 1;
                      const hasLine = s.points.length >= 3;
                      return (
                        <Line
                          key={s.key}
                          data={s.points}
                          type="monotone"
                          dataKey="v"
                          name={s.key}
                          stroke={hasLine ? color : "transparent"}
                          strokeDasharray={dashFor(s.key)}
                          strokeWidth={2}
                          strokeOpacity={opacity}
                          dot={{ r: 3, fill: color, fillOpacity: opacity }}
                          activeDot={{ r: 5, fill: color }}
                          connectNulls={false}
                          isAnimationActive={!reduced && visible}
                        />
                      );
                    })}
                  </LineChart>
                </ResponsiveContainer>
              </div>
              {Array.from(seriesMap.values()).some(
                (s) => s.points.length > 0 && s.points.length < 3,
              ) && (
                <p className="text-[11px] text-[var(--ink-faint)] mt-2">
                  {Array.from(seriesMap.values())
                    .filter((s) => s.points.length > 0 && s.points.length < 3)
                    .map((s) => s.key)
                    .join(", ")}{" "}
                  — too few runs to call a trend.
                </p>
              )}
            </Card>
          </motion.div>

          {/* §4 criterion x-ray */}
          <motion.div {...sectionMotion(4, reduced)}>
            <Card className="p-5">
              <SectionHead
                eyebrow="Per-criterion x-ray"
                title="What's dragging the score down"
                right={
                  activeSlice ? (
                    <button
                      type="button"
                      className="eval-legend eval-legend--btn"
                      onClick={() => setActiveSlice(null)}
                    >
                      <Badge tone="brass">{activeSlice} ✕</Badge>
                    </button>
                  ) : undefined
                }
              />
              {mixedTypes && (
                <p className="text-[11px] text-[var(--ink-faint)] mb-2">
                  This slice mixes target types — criteria come from different rubrics.
                </p>
              )}
              {criterionData.length === 0 ? (
                <p className="text-[13px] text-[var(--ink-dim)]">
                  No per-criterion detail in this slice.
                </p>
              ) : (
                <div style={{ width: "100%", height: Math.max(180, criterionData.length * 34) }}>
                  <ResponsiveContainer>
                    <BarChart
                      data={criterionData}
                      layout="vertical"
                      margin={{ top: 4, right: 20, bottom: 4, left: 4 }}
                    >
                      <CartesianGrid
                        stroke="var(--line)"
                        strokeDasharray="3 4"
                        horizontal={false}
                      />
                      <XAxis
                        type="number"
                        domain={[0, 100]}
                        tick={{
                          fill: "var(--ink-dim)",
                          fontSize: 11,
                          fontFamily: "var(--font-mono)",
                        }}
                        stroke="var(--line-strong)"
                      />
                      <YAxis
                        type="category"
                        dataKey="criterion"
                        width={150}
                        tick={{
                          fill: "var(--ink-dim)",
                          fontSize: 11,
                          fontFamily: "var(--font-mono)",
                        }}
                        stroke="var(--line-strong)"
                      />
                      <ReferenceLine
                        x={threshold}
                        stroke="var(--line-strong)"
                        strokeDasharray="4 3"
                      />
                      <Bar
                        dataKey="mean"
                        radius={[0, 3, 3, 0]}
                        cursor="pointer"
                        onClick={(d: { worstRecord: EvalRecord }) => drillToRecord(d.worstRecord)}
                      >
                        {criterionData.map((d) => (
                          <Cell key={d.criterion} fill={scoreColor(d.mean)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
              {unratedCount > 0 && (
                <p className="text-[11px] text-[var(--ink-faint)] mt-2">
                  {unratedCount} record{unratedCount === 1 ? "" : "s"}{" "}
                  {unratedCount === 1 ? "has" : "have"} no per-criterion detail.
                </p>
              )}
              {selectedRecord && (
                <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--line)" }}>
                  <Eyebrow className="break-all">{selectedRecord.target_id}</Eyebrow>
                  <CriterionReadout scores={selectedRecord.scores} />
                </div>
              )}
            </Card>
          </motion.div>

          {/* §5 slice small multiples */}
          <motion.div {...sectionMotion(5, reduced)}>
            <div>
              <SectionHead eyebrow="Agent × target type" title="Who's degrading" />
              <div className="eval-slice-grid">
                {Array.from(seriesMap.values()).map((s) => (
                  <SliceCard
                    key={s.key}
                    s={s}
                    color={colorFor(s.key)}
                    active={activeSlice === s.key}
                    onClick={() => setActiveSlice((cur) => (cur === s.key ? null : s.key))}
                    reduced={reduced}
                    visible={visible}
                  />
                ))}
              </div>
            </div>
          </motion.div>

          {/* §6 fix queue — the ACT zone */}
          <motion.div {...sectionMotion(6, reduced)}>
            <Card
              className="p-5 fix-queue"
              style={{
                borderColor: queue.length
                  ? "color-mix(in srgb, var(--danger) 30%, var(--line))"
                  : undefined,
              }}
            >
              <SectionHead
                eyebrow={`Below the ${threshold} bar`}
                title="Send back to the bench"
                right={<Badge tone={queue.length ? "danger" : "sage"}>{queue.length}</Badge>}
              />
              {queue.length === 0 ? (
                <div className="fix-queue__clean">
                  {meanScore != null && (
                    <TapeGauge value={meanScore} tierColor="var(--sage-ink)" height={8} />
                  )}
                  <p className="text-[13px] text-[var(--ink-dim)] mt-2">
                    Clean floor — every scored record clears {threshold}.
                  </p>
                </div>
              ) : (
                <ul className="fix-queue__list" aria-label="Records to fix">
                  {queue.map((r) => (
                    <li key={`${r.agent}-${r.target_id}-${r.ts}`}>
                      <FixCard
                        rec={r}
                        onNavigate={onNavigate}
                        registerRef={registerFixCardRef}
                        flashed={flashId === r.target_id}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {unscoredCount > 0 && (
                <div
                  className="mt-3"
                  title="Excluded from charts and the queue, never silently dropped"
                >
                  <Badge tone="neutral">unscored ({unscoredCount})</Badge>
                </div>
              )}
            </Card>
          </motion.div>
        </>
      )}

      {/* §7 outcome slot — present, empty, honest (phase 3) */}
      <motion.div {...sectionMotion(7, reduced)}>
        <Card className="p-5 outcome-slot" aria-disabled="true">
          <Eyebrow>Outcome · predicted vs actual</Eyebrow>
          <h3 className="font-display text-lg mt-1">Not wired yet</h3>
          <p className="text-[13px] text-[var(--ink-dim)] mt-1 max-w-[60ch]">
            Ships in phase 3 — needs the manual-post → reel link (posted_content_id) and the
            re-scrape job that scores predicted-vs-actual virality. No data is faked here.
          </p>
          <div className="mt-3">
            <Seam state="idle" width={140} />
          </div>
        </Card>
      </motion.div>
    </div>
  );
}

function StatTile({
  i,
  label,
  children,
  reduced,
}: {
  i: number;
  label: string;
  children: React.ReactNode;
  reduced: boolean;
}) {
  return (
    <motion.div className="mat eval-stat" {...sectionMotion(i, reduced)}>
      <Eyebrow>{label}</Eyebrow>
      {children}
    </motion.div>
  );
}

function SliceCard({
  s,
  color,
  active,
  onClick,
  reduced,
  visible,
}: {
  s: SeriesInfo;
  color: string;
  active: boolean;
  onClick: () => void;
  reduced: boolean;
  visible: boolean;
}) {
  const worst = useMemo(() => {
    const m = criterionMeans(s.points.map((p) => p.rec));
    const first = m.entries().next();
    return first.done ? null : { label: first.value[0], v: first.value[1].mean };
  }, [s]);

  return (
    <Card
      interactive
      className={cx("p-4", active && "eval-slice--active")}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="font-mono text-[12px] truncate">{s.key}</span>
        {/* judge is an identity, not a state — keep it neutral and reserve
            brass for the active-slice affordance. */}
        <Badge tone="neutral">{s.judge ?? "—"}</Badge>
      </div>
      {s.points.length >= 2 ? (
        <div style={{ width: "100%", height: 44 }}>
          <ResponsiveContainer>
            <LineChart data={s.points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
              <Line
                type="monotone"
                dataKey="v"
                stroke={color}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={!reduced && visible}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div
          className="text-[11px] text-[var(--ink-faint)]"
          style={{ height: 44, display: "flex", alignItems: "center" }}
        >
          single run · no sparkline
        </div>
      )}
      <div className="flex items-center justify-between gap-3 mt-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span aria-hidden="true">{TREND_GLYPH[s.trend]}</span>
          <div className="flex-1 min-w-0">
            <TapeGauge value={s.mean} tierColor={scoreColor(s.mean)} height={6} showTicks={false} />
          </div>
        </div>
        <SignalRing
          value={s.accepts}
          max={Math.max(1, s.total)}
          label={`${s.accepts}/${s.total}`}
          color={scoreColor(s.mean)}
        />
      </div>
      {worst && (
        <div className="mt-2 text-[11px] font-mono" style={{ color: scoreColor(worst.v) }}>
          {worst.label}: {worst.v.toFixed(0)}
        </div>
      )}
    </Card>
  );
}

const evalTooltipStyle: React.CSSProperties = {
  background: "var(--surface-3)",
  border: "1px solid var(--line-strong)",
  borderRadius: "var(--r-sm)",
  padding: "8px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  color: "var(--ink)",
};

function EvalTooltipContent({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={evalTooltipStyle}>
      {payload.map((p, i) => {
        const pt = p.payload as SeriesPoint;
        return (
          <div
            key={i}
            style={
              i > 0
                ? { marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--line)" }
                : undefined
            }
          >
            <div style={{ color: "var(--ink-dim)", fontSize: 10.5 }}>
              {new Date(pt.t).toLocaleDateString(undefined, { month: "short", day: "numeric" })} ·{" "}
              {seriesKey(pt.rec)}
            </div>
            <div className="tnum" style={{ fontSize: 13 }}>
              {pt.v.toFixed(1)}
            </div>
            <div style={{ color: "var(--ink-dim)", fontSize: 11 }}>
              {pt.rec.verdict ?? "—"} · {pt.rec.judge ?? "—"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
