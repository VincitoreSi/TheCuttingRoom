import { useMemo, useState } from "react";
import { useShell } from "../App";
import { useContent, useEvals, useFactors, usePlatforms, useRunStage } from "../lib/hooks";
import { VirtualReelGrid } from "../components/VirtualReelGrid";
import { ReelModal } from "../components/ReelModal";
import { Button, Card, EmptyState, Eyebrow, Input, Select } from "../components/ui";
import { IconCorpus, IconPlay, IconSearch } from "../components/icons";
import { grouped } from "../lib/format";
import { recordScore } from "../lib/evalModel";
import { deriveCorpusEmpty } from "../lib/corpusEmpty";
import type { Factor, Reel } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";
import { cx } from "../lib/cx";

type Sort = "score" | "eval" | "plays" | "reach" | "outlier" | "engagement" | "velocity" | "recent";
const SORTS: { key: Sort; label: string }[] = [
  { key: "score", label: "Virality" },
  { key: "eval", label: "Eval score" },
  { key: "plays", label: "Plays" },
  { key: "reach", label: "Reach ×" },
  { key: "outlier", label: "Outlier ×" },
  { key: "engagement", label: "Engagement" },
  { key: "velocity", label: "Velocity" },
  { key: "recent", label: "Most recent" },
];

type Analyzed = "all" | "yes" | "no";

export function Corpus({ onNavigate }: { onNavigate?: (v: ViewKey) => void }) {
  const { platform } = useShell();
  const contentQ = useContent(platform);
  // `scraped` separates "nobody has scraped this" from "scraped, never analyzed" — two
  // states this grid renders identically (as nothing) but which need opposite advice.
  const summary = usePlatforms().data?.find((p) => p.platform === platform);
  const runStage = useRunStage(platform);
  // join AnalysisEngine self-eval scores (per blueprint) onto the corpus rows
  const evalsQ = useEvals({ target_type: "blueprint" });
  const [tab, setTab] = useState<"reels" | "factors">("reels");
  const [open, setOpen] = useState<Reel | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("score");
  const [tierFilter, setTierFilter] = useState<string>("all");
  const [analyzed, setAnalyzed] = useState<Analyzed>("all");

  const evalMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of evalsQ.data ?? []) {
      // shared score truth (evalModel.recordScore) — the same fallback the
      // Evals tab uses, so the "Eval score" sort never drops a record just
      // because it carries a per-criterion rubric instead of `overall`.
      const s = recordScore(e);
      if (s != null) m.set(e.target_id, s);
    }
    return m;
  }, [evalsQ.data]);

  const reels = useMemo(
    () =>
      (contentQ.data ?? []).map((r) => ({ ...r, eval_score: evalMap.get(r.content_id) ?? null })),
    [contentQ.data, evalMap],
  );

  const tiers = useMemo(() => {
    const s = new Set<string>();
    reels.forEach((r) => r.tier && s.add(r.tier));
    return Array.from(s);
  }, [reels]);

  const analyzedCount = useMemo(() => reels.filter((r) => r.analyzed).length, [reels]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = reels.filter((r) => {
      if (tierFilter !== "all" && r.tier !== tierFilter) return false;
      if (analyzed === "yes" && !r.analyzed) return false;
      if (analyzed === "no" && r.analyzed) return false;
      if (!needle) return true;
      return r.creator?.toLowerCase().includes(needle) || r.caption?.toLowerCase().includes(needle);
    });
    const key = (r: Reel): number => {
      switch (sort) {
        case "eval":
          return r.eval_score ?? -1;
        case "plays":
          return r.plays ?? 0;
        case "reach":
          return r.reach_multiplier ?? 0;
        case "outlier":
          return r.outlier_score ?? 0;
        case "engagement":
          return r.engagement_rate ?? 0;
        case "velocity":
          return r.velocity ?? 0;
        case "recent":
          return r.posted ? Date.parse(r.posted.replace(" ", "T")) : 0;
        default:
          return r.virality_score ?? 0;
      }
    };
    out = [...out].sort((a, b) => key(b) - key(a));
    return out;
  }, [reels, q, sort, tierFilter, analyzed]);

  const empty = deriveCorpusEmpty({
    total: reels.length,
    filtered: filtered.length,
    scraped: summary?.scraped ?? false,
    watchlist: summary?.watchlist ?? 0,
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="segmented" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "reels"}
            className={cx("segmented__btn", tab === "reels" && "segmented__btn--active")}
            onClick={() => setTab("reels")}
          >
            Reels
          </button>
          <button
            role="tab"
            aria-selected={tab === "factors"}
            className={cx("segmented__btn", tab === "factors" && "segmented__btn--active")}
            onClick={() => setTab("factors")}
          >
            Factors
          </button>
        </div>
        <Eyebrow>
          {contentQ.isLoading
            ? "loading corpus…"
            : `${grouped(filtered.length)} of ${grouped(reels.length)} ${reels.length === 1 ? "reel" : "reels"}`}
        </Eyebrow>
      </div>

      {tab === "reels" ? (
        <>
          <div className="corpus-toolbar">
            <div className="corpus-toolbar__search">
              <IconSearch size={16} />
              <Input
                placeholder="Search creators or captions…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                aria-label="Search corpus"
              />
            </div>
            <Select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              aria-label="Sort by"
            >
              {SORTS.map((s) => (
                <option key={s.key} value={s.key}>
                  Sort · {s.label}
                </option>
              ))}
            </Select>
            <Select
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
              aria-label="Filter tier"
            >
              <option value="all">All tiers</option>
              {tiers.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
            <Select
              value={analyzed}
              onChange={(e) => setAnalyzed(e.target.value as Analyzed)}
              aria-label="Filter by blueprint status"
            >
              <option value="all">All reels</option>
              <option value="yes">Analyzed · {analyzedCount}</option>
              <option value="no">Not analyzed</option>
            </Select>
          </div>

          {contentQ.isLoading ? (
            <GridSkeleton />
          ) : empty ? (
            <EmptyState
              icon={<IconCorpus size={28} />}
              title={empty.title}
              hint={empty.hint}
              action={
                empty.run ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => runStage.mutate(empty.run!)}
                    disabled={runStage.isPending}
                  >
                    <IconPlay size={14} />
                    {runStage.isPending ? "Running…" : `Run ${empty.run}`}
                  </Button>
                ) : empty.goto && onNavigate ? (
                  <Button variant="primary" size="sm" onClick={() => onNavigate(empty.goto!)}>
                    Open Config
                  </Button>
                ) : null
              }
            />
          ) : (
            <VirtualReelGrid reels={filtered} onOpen={setOpen} />
          )}
        </>
      ) : (
        <FactorsPanel />
      )}

      {open && <ReelModal reel={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="reel-grid">
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="skeleton" style={{ aspectRatio: "3 / 4.5" }} />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- Factors panel */
function FactorsPanel() {
  const { platform } = useShell();
  const factorsQ = useFactors(platform);
  const contentQ = useContent(platform);

  if (factorsQ.isLoading) return <div className="skeleton" style={{ height: 280 }} />;
  // `baseline` is null (not the object itself) when the corpus is empty, so testing the
  // response for truthiness is not enough — that guard passed and null.toFixed() threw.
  if (!factorsQ.data || factorsQ.data.baseline == null)
    return (
      <EmptyState title="No factors yet" hint="Analyze the corpus to compute virality factors." />
    );

  const { winners, losers, baseline } = factorsQ.data;
  const maxLift = Math.max(1, ...[...winners, ...losers].map((f) => Math.abs(f.lift)));

  // creator leaderboard from content: avg virality, weighted by count
  const leaderboard = buildLeaderboard(contentQ.data ?? []);

  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card className="p-5">
        <div className="flex items-baseline justify-between mb-3">
          <div>
            <Eyebrow>Lift vs baseline</Eyebrow>
            <h3 className="font-display text-lg">What moves virality</h3>
          </div>
          <span className="font-mono text-[12px] text-[var(--ink-dim)]">
            baseline {baseline.toFixed(1)}
          </span>
        </div>
        <div className="mb-4">
          <div className="eyebrow mb-1.5" style={{ color: "var(--sage-ink)" }}>
            ▲ Winners
          </div>
          {winners.slice(0, 7).map((f, i) => (
            <FactorBar key={i} f={f} max={maxLift} tone="sage" />
          ))}
        </div>
        <div>
          <div className="eyebrow mb-1.5" style={{ color: "var(--danger)" }}>
            ▼ Drags
          </div>
          {losers.slice(0, 7).map((f, i) => (
            <FactorBar key={i} f={f} max={maxLift} tone="danger" />
          ))}
        </div>
      </Card>

      <Card className="p-5">
        <Eyebrow>Top creators by mean virality</Eyebrow>
        <h3 className="font-display text-lg mb-3">Leaderboard</h3>
        <div>
          {leaderboard.slice(0, 12).map((c, i) => (
            <div key={c.creator} className="leader-row">
              <span className="leader-row__rank">{String(i + 1).padStart(2, "0")}</span>
              <div className="min-w-0">
                <div className="text-[13px] truncate">@{c.creator}</div>
                <div
                  className="leader-row__bar mt-1"
                  style={{ width: `${(c.avg / (leaderboard[0]?.avg || 100)) * 100}%` }}
                />
              </div>
              <div className="text-right">
                <div className="font-mono tnum text-[13px]" style={{ color: "var(--brass-ink)" }}>
                  {c.avg.toFixed(1)}
                </div>
                <div className="font-mono text-[10px] text-[var(--ink-faint)]">{c.n} reels</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function FactorBar({ f, max, tone }: { f: Factor; max: number; tone: "sage" | "danger" }) {
  const color = tone === "sage" ? "var(--sage)" : "var(--danger)";
  const w = (Math.abs(f.lift) / max) * 50; // half-width; center is baseline
  const positive = f.lift >= 0;
  return (
    <div className="factor-bar">
      <span className="factor-bar__label" title={`${f.feature} · ${f.bucket} (n=${f.n})`}>
        <span className="text-[var(--ink-dim)]">{f.feature}</span> {f.bucket}
      </span>
      <div className="factor-bar__track">
        <div className="factor-bar__mid" />
        <div
          className="factor-bar__fill"
          style={{ width: `${w}%`, left: positive ? "50%" : `${50 - w}%`, background: color }}
        />
      </div>
      <span className="factor-bar__val" style={{ color }}>
        {positive ? "+" : ""}
        {f.lift.toFixed(1)}
      </span>
    </div>
  );
}

function buildLeaderboard(reels: Reel[]) {
  const map = new Map<string, { sum: number; n: number }>();
  for (const r of reels) {
    if (!r.creator || r.virality_score == null) continue;
    const cur = map.get(r.creator) ?? { sum: 0, n: 0 };
    cur.sum += r.virality_score;
    cur.n += 1;
    map.set(r.creator, cur);
  }
  return Array.from(map.entries())
    .map(([creator, v]) => ({ creator, avg: v.sum / v.n, n: v.n }))
    .filter((c) => c.n >= 2)
    .sort((a, b) => b.avg - a.avg);
}
