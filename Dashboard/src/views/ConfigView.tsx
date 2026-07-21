import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useShell } from "../App";
import {
  useCascade,
  useConfig,
  useReducedMotion,
  useSaveCascade,
  useSaveConfig,
  useSaveSchedule,
  useSchedule,
} from "../lib/hooks";
import { Badge, Button, Card, Eyebrow, Input, SectionHead, Select, Switch } from "../components/ui";
import { RangeSlider } from "../components/ui";
import { IconCheck, IconPin, IconX } from "../components/icons";
import { KeysAndModels } from "../components/config/KeysAndModels";
import { sectionMotion } from "../lib/motion";
import {
  CASCADE_LIMITS,
  FUNNEL_ROWS,
  clampCascadeField,
  funnelProjection,
} from "../lib/cascadeFunnel";
import type { CascadeField } from "../lib/cascadeFunnel";
import { consumeConfigFocus } from "../lib/nav";
import type { CascadeRow, NicheConfig, Tier } from "../lib/types";
import { cx } from "../lib/cx";

const WEIGHT_LABELS: Record<string, string> = {
  reach_multiplier: "Reach multiplier",
  outlier_score: "Outlier score",
  engagement_rate: "Engagement rate",
  velocity: "Velocity",
};

export function ConfigView() {
  const { platform } = useShell();
  const configQ = useConfig(platform);
  const scheduleQ = useSchedule();
  const sched = scheduleQ.data?.[platform];
  const saveSchedule = useSaveSchedule(platform);
  const cascadeQ = useCascade();
  const cascadeRow = cascadeQ.data?.[platform];
  const saveCascade = useSaveCascade(platform);

  // The funnel numbers are edited locally and written on RELEASE — a slider wired straight
  // to a PUT sends one per pixel of a drag. A draft outlives its own PUT: it is dropped
  // when the refetched row agrees (below) or when the hub refuses, so the control never
  // snaps back to the old number for the length of a round trip.
  const [cascadeDrafts, setCascadeDrafts] = useState<Partial<Record<CascadeField, string>>>({});
  const dropDraft = (field: CascadeField) =>
    setCascadeDrafts((p) => {
      if (!(field in p)) return p;
      const next = { ...p };
      delete next[field];
      return next;
    });
  // a draft belongs to the platform it was typed against — never carry it across a switch,
  // where it would both misreport the new platform and PUT the old platform's number.
  useEffect(() => setCascadeDrafts({}), [platform]);
  useEffect(() => {
    if (!cascadeRow) return;
    setCascadeDrafts((p) => {
      const next = { ...p };
      let changed = false;
      for (const field of Object.keys(next) as CascadeField[]) {
        if (clampCascadeField(field, next[field]) !== cascadeRow[field]) continue;
        delete next[field];
        changed = true;
      }
      return changed ? next : p;
    });
  }, [cascadeRow]);

  /** What a control shows: the in-flight draft while it is being edited, the hub's answer
      otherwise. `cascadeField` is the same value as a number, for the projection. */
  const cascadeText = (field: CascadeField) =>
    cascadeDrafts[field] ?? String(clampCascadeField(field, cascadeRow?.[field]));
  const cascadeField = (field: CascadeField) =>
    clampCascadeField(field, cascadeDrafts[field] ?? cascadeRow?.[field]);

  /** Commit one field, one PUT. The value arrives as an ARGUMENT: reading it back out of
      the draft map in the same tick that set it commits the PREVIOUS value, and on the
      first drag reads undefined and gives up without saying so. */
  const commitCascade = (field: CascadeField, raw: string) => {
    if (raw.trim() === "") return dropDraft(field); // emptied box — hand it back to the hub
    const next = clampCascadeField(field, raw);
    if (next === cascadeRow?.[field]) return dropDraft(field);
    setCascadeDrafts((p) => ({ ...p, [field]: String(next) })); // show what was SENT
    saveCascade.mutate({ [field]: next } as Partial<CascadeRow>, {
      // a refused PUT answers with a sentence; never leave the control showing a number
      // the hub did not take.
      onError: () => dropDraft(field),
    });
  };
  const save = useSaveConfig(platform);
  const reduced = useReducedMotion();

  const [cfg, setCfg] = useState<NicheConfig | null>(null);
  const [pages, setPages] = useState<string[]>([]);
  const [newKeyword, setNewKeyword] = useState("");
  const [newPage, setNewPage] = useState("");
  const [saved, setSaved] = useState(false);

  // Pages are server-authoritative (pinning/removing persists immediately, below), so keep
  // the local list in sync with every config refetch. The scoring `cfg`, by contrast, is
  // edited locally until "Save to hub" — so it is seeded ONCE and NOT clobbered by the
  // refetch a pin triggers, which would otherwise silently discard in-progress weight edits.
  const cfgSeeded = useRef(false);
  useEffect(() => {
    if (!configQ.data) return;
    setPages([...configQ.data.pages]);
    if (!cfgSeeded.current) {
      cfgSeeded.current = true;
      setCfg(structuredClone(configQ.data.config));
    }
  }, [configQ.data]);

  // When Home's onboarding checklist sends a new user here to add their first handle,
  // focus the watchlist input once the view has settled. A one-shot, cross-view intent
  // consumed on mount (see lib/nav). We deliberately do NOT programmatically scroll — every
  // scroll variant tried fought the view's enter animation and left the shell blank; the
  // native focus brings the input into view reliably instead.
  const addHandleRef = useRef<HTMLInputElement>(null);
  const focusHandled = useRef(false);
  useEffect(() => {
    if (focusHandled.current || !cfg) return; // wait until the watchlist is actually rendered
    if (consumeConfigFocus() !== "pages") return;
    focusHandled.current = true;
    // Bring the watchlist INTO VIEW, then focus it. Focusing alone (with preventScroll,
    // which is still required — an unguarded focus scroll once moved the window and
    // blanked the whole shell, sidebar included) left someone who had just clicked
    // "Add pages" staring at the virality weights, with the thing they asked for
    // several screens below. scrollIntoView on the section scrolls the app's own scroll
    // container rather than the window, so it does not reproduce that bug.
    const t = setTimeout(() => {
      document
        .getElementById("config-pages")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      addHandleRef.current?.focus({ preventScroll: true });
    }, 600);
    return () => clearTimeout(t);
  }, [cfg]);

  const weights = cfg?.virality?.weights ?? {};
  const weightSum = useMemo(
    () => Object.values(weights).reduce((a, b) => a + (b ?? 0), 0),
    [weights],
  );

  if (configQ.isLoading || !cfg) {
    return <div className="skeleton" style={{ height: 420 }} />;
  }

  function setWeight(k: string, v: number) {
    setCfg((c) => {
      if (!c) return c;
      const next = structuredClone(c);
      next.virality = next.virality ?? { weights: {}, tiers: [], top_n: 100 };
      next.virality.weights = { ...next.virality.weights, [k]: v };
      return next;
    });
    setSaved(false);
  }

  function setTier(i: number, min: number) {
    setCfg((c) => {
      if (!c?.virality) return c;
      const next = structuredClone(c);
      next.virality!.tiers[i].min_score = min;
      return next;
    });
    setSaved(false);
  }

  function removeKeyword(kw: string) {
    setCfg((c) => {
      if (!c?.discovery) return c;
      const next = structuredClone(c);
      next.discovery!.keywords = next.discovery!.keywords.filter((k) => k !== kw);
      return next;
    });
    setSaved(false);
  }
  function addKeyword() {
    const kw = newKeyword.trim();
    if (!kw) return;
    setCfg((c) => {
      const next = structuredClone(c!);
      next.discovery = next.discovery ?? emptyDiscovery();
      if (!next.discovery.keywords.includes(kw)) next.discovery.keywords.push(kw);
      return next;
    });
    setNewKeyword("");
    setSaved(false);
  }

  function toggleDiscovery(on: boolean) {
    setCfg((c) => {
      const next = structuredClone(c!);
      next.discovery = next.discovery ?? emptyDiscovery();
      next.discovery.enabled = on;
      return next;
    });
    setSaved(false);
  }

  // The watchlist persists on edit — pinning a handle and navigating away used to lose it
  // (it only reached the hub on a separate "Save to hub" click), which also left the pipeline
  // with no creators to scrape. A pages-only PUT writes pages.txt without touching
  // niche_config, and the mutation invalidates the config query so Home's onboarding
  // checklist ticks "Add a handle" the moment the write lands.
  function persistPages(next: string[]) {
    setPages(next);
    save.mutate({ pages: next });
  }
  function removePage(p: string) {
    persistPages(pages.filter((x) => x !== p));
  }
  function addPage() {
    const p = newPage.trim();
    if (!p) return;
    setNewPage("");
    if (pages.includes(p)) return;
    persistPages([...pages, p]);
  }

  async function onSave() {
    await save.mutateAsync({ config: cfg!, pages });
    setSaved(true);
    setTimeout(() => setSaved(false), 2400);
  }

  const sumOk = Math.abs(weightSum - 1) < 0.001;

  // Projected off the LIVE values (drafts included), so the volumes move under the thumb
  // while a slider is being dragged and settle on what the hub took.
  const projected = funnelProjection({
    scrape_count: cascadeField("scrape_count"),
    analyze_pct: cascadeField("analyze_pct"),
    media_pct: cascadeField("media_pct"),
    blueprint_pct: cascadeField("blueprint_pct"),
    propose_pct: cascadeField("propose_pct"),
  });
  const blueprintsOn = cascadeRow?.include_blueprints ?? false;

  return (
    <div className="flex flex-col gap-5 max-w-4xl">
      <motion.div
        className="flex items-center justify-between gap-4 flex-wrap"
        {...sectionMotion(0, reduced)}
      >
        <div>
          <Eyebrow>{platform} · niche_config.json + pages.txt</Eyebrow>
          <h2 className="font-display text-2xl">The Bench</h2>
          <p className="text-[var(--ink-dim)] text-[13.5px] mt-1">
            Everything that defines "viral" for this platform, in one place. Weights auto-normalize
            to 1.
          </p>
        </div>
        <Button variant="primary" onClick={onSave} disabled={save.isPending}>
          {saved ? (
            <>
              <IconCheck size={15} /> Saved
            </>
          ) : save.isPending ? (
            "Saving…"
          ) : (
            "Save to hub"
          )}
        </Button>
      </motion.div>

      {/* keys & models — unified per-agent key status + model selection */}
      <motion.div {...sectionMotion(1, reduced)}>
        <KeysAndModels />
      </motion.div>

      {/* weights */}
      <motion.div {...sectionMotion(2, reduced)}>
        <Card className="p-5">
          <SectionHead
            eyebrow="Virality weights"
            title="How virality is scored"
            right={
              <span className={cx("sum-pill", sumOk ? "sum-pill--ok" : "sum-pill--off")}>
                Σ {weightSum.toFixed(2)} {sumOk ? "· balanced" : "· auto-normalized"}
              </span>
            }
          />
          {Object.keys(WEIGHT_LABELS).map((k) => {
            const raw = weights[k] ?? 0;
            const share = weightSum > 0 ? (raw / weightSum) * 100 : 0;
            return (
              <div className="weight-row" key={k}>
                <span className="weight-row__name">{WEIGHT_LABELS[k]}</span>
                <RangeSlider
                  value={raw}
                  min={0}
                  max={0.6}
                  step={0.01}
                  aria-label={WEIGHT_LABELS[k]}
                  onChange={(v) => setWeight(k, v)}
                />
                <span className="weight-row__val">{share.toFixed(0)}%</span>
              </div>
            );
          })}
        </Card>
      </motion.div>

      {/* tiers */}
      <motion.div {...sectionMotion(3, reduced)}>
        <Card className="p-5">
          <SectionHead eyebrow="Tier thresholds" title="Where the notches fall" />
          <div className="flex flex-col gap-2">
            {(cfg.virality?.tiers ?? []).map((t: Tier, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className="w-36 text-[13px]" style={{ color: "var(--brass-ink)" }}>
                  {t.label}
                </span>
                <span className="eyebrow">min score</span>
                <Input
                  type="number"
                  className="w-24"
                  aria-label={`${t.label} min score`}
                  value={t.min_score}
                  min={0}
                  max={100}
                  onChange={(e) => setTier(i, Number(e.target.value))}
                />
                <div className="flex-1">
                  <div className="tape-gauge" style={{ height: 6 }}>
                    <div
                      className="tape-gauge__fill"
                      style={{
                        width: `${Math.max(0, Math.min(100, t.min_score))}%`,
                        background: "var(--thread)",
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </motion.div>

      {/* automatic runs */}
      <motion.div {...sectionMotion(4, reduced)}>
        <Card className="p-5">
          <SectionHead
            eyebrow="Automatic runs"
            title="Run the pipeline on a timer"
            right={
              <Switch
                checked={sched?.enabled ?? false}
                onChange={(v) => saveSchedule.mutate({ enabled: v })}
                label="Enable automatic runs"
              />
            }
          />
          <p className="text-[13px] text-[var(--ink-dim)] mb-3">
            Runs scrape → analyze → media on a repeat.{" "}
            <strong>Only while the hub is running</strong> — there is no background service outside
            it, so this is best-effort, not a guarantee. The interval is measured from the last run
            and survives a restart.
          </p>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="text-[13px] text-[var(--ink-dim)]">Every</label>
            <Select
              value={String(sched?.every_hours ?? 24)}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) =>
                saveSchedule.mutate({ every_hours: Number(e.target.value) })
              }
              aria-label="How often to run"
              disabled={!sched?.enabled}
            >
              <option value="6">6 hours</option>
              <option value="12">12 hours</option>
              <option value="24">day</option>
              <option value="72">3 days</option>
              <option value="168">week</option>
            </Select>
            {sched?.next_run_at ? (
              <Eyebrow>next · {new Date(sched.next_run_at * 1000).toLocaleString()}</Eyebrow>
            ) : null}
          </div>
          <label className="flex items-start gap-2 mt-3 text-[13px] text-[var(--ink-dim)]">
            <input
              type="checkbox"
              checked={sched?.include_blueprints ?? false}
              disabled={!sched?.enabled}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                saveSchedule.mutate({ include_blueprints: e.target.checked })
              }
              className="mt-[3px]"
            />
            <span>
              Also generate blueprints. <strong>Costs API credits on every run</strong> — the
              analysis stage calls Gemini once per clip. Off by default for the same reason
              rendering is never automatic.
            </span>
          </label>
        </Card>
      </motion.div>

      {/* cascading heartbeat — the percentage funnel */}
      <motion.div {...sectionMotion(5, reduced)}>
        <Card className="p-5">
          <SectionHead
            eyebrow="Cascading heartbeat"
            title="Auto-trigger stages on new data"
            right={
              <Switch
                checked={cascadeRow?.enabled ?? false}
                onChange={(v) => saveCascade.mutate({ enabled: v })}
                label="Enable the cascade"
              />
            }
          />
          <p className="text-[13px] text-[var(--ink-dim)] mb-4">
            A 60s tick that walks new data down the pipeline on its own. One batch size anchors the
            funnel and each stage below takes a <strong>percentage</strong> of the stage above it,
            so a later stage can never fire more often than the one feeding it. The chain ends at
            the gate — <strong>rendering is never automatic</strong>. Stored per-platform in{" "}
            <code>config/pipeline_cascade.json</code>.
          </p>

          {/* The funnel, top to bottom: an absolute batch, then four percentages of the row
              above. The right column is what one cycle is expected to move — a ceiling, not
              a promise, which is what the ≤ says. */}
          <div className="mb-4">
            {FUNNEL_ROWS.map((row) => {
              const paid = row.key === "blueprint";
              return (
                <div
                  key={row.key}
                  className={cx(
                    "funnel-row",
                    paid && "funnel-row--paid",
                    // off means this boundary will not fire at all — say so by fading the
                    // whole row rather than leaving a live-looking control that does nothing.
                    paid && !blueprintsOn && "funnel-row--muted",
                  )}
                >
                  <div className="funnel-row__rail">
                    <span className="funnel-row__dot" />
                  </div>
                  <span className="funnel-row__name">
                    {row.label}
                    {paid && (
                      <Badge tone="oxblood" className="ml-2">
                        paid
                      </Badge>
                    )}
                  </span>
                  {row.key === "scrape" ? (
                    <span className="funnel-row__anchor">
                      <Input
                        type="number"
                        className="w-24"
                        value={cascadeText("scrape_count")}
                        min={CASCADE_LIMITS.scrape_count.min}
                        max={CASCADE_LIMITS.scrape_count.max}
                        aria-label="Reels scraped per cycle"
                        onChange={(e) =>
                          setCascadeDrafts((p) => ({ ...p, scrape_count: e.target.value }))
                        }
                        onBlur={(e) => commitCascade("scrape_count", e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      />
                      <Eyebrow>per cycle</Eyebrow>
                    </span>
                  ) : (
                    <RangeSlider
                      value={cascadeField(row.field)}
                      min={CASCADE_LIMITS[row.field].min}
                      max={CASCADE_LIMITS[row.field].max}
                      step={1}
                      aria-label={`${row.label} — percent of the stage above`}
                      /* drag paints, release writes: onChange only moves the draft, and the
                         single PUT goes out of onCommit. */
                      onChange={(v) => setCascadeDrafts((p) => ({ ...p, [row.field]: String(v) }))}
                      onCommit={(v) => commitCascade(row.field, String(v))}
                    />
                  )}
                  <span className="funnel-row__pct">
                    {row.key === "scrape" ? "batch" : `${cascadeField(row.field)}%`}
                  </span>
                  <span className="funnel-row__vol">
                    {row.key === "scrape" ? "" : "≤"}
                    {projected[row.key]}
                    <span className="funnel-row__unit">{row.unit}</span>
                  </span>
                </div>
              );
            })}
          </div>

          <label className="flex items-start gap-2 mb-3 text-[13px] text-[var(--ink-dim)]">
            <input
              type="checkbox"
              checked={blueprintsOn}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                saveCascade.mutate({ include_blueprints: e.target.checked })
              }
              className="mt-[3px]"
            />
            <span>
              Let the cascade generate blueprints. <strong>Costs API credits per clip</strong> —
              that boundary calls Gemini once for every one. Off by default, and while it is off the
              Blueprint row above is greyed out because it will not fire.
            </span>
          </label>

          {/* propose_count is NOT propose_pct: the percentage decides how much blueprint
              output reaches the propose boundary, this decides what one firing publishes. */}
          <div className="flex items-center gap-3 flex-wrap mb-3">
            <label className="text-[13px] text-[var(--ink-dim)]" htmlFor="cascade-propose-count">
              Recipes published per propose firing
            </label>
            <Input
              id="cascade-propose-count"
              type="number"
              className="w-20"
              value={cascadeText("propose_count")}
              min={CASCADE_LIMITS.propose_count.min}
              max={CASCADE_LIMITS.propose_count.max}
              aria-label="Recipes published per propose firing"
              onChange={(e) => setCascadeDrafts((p) => ({ ...p, propose_count: e.target.value }))}
              onBlur={(e) => commitCascade("propose_count", e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            />
            <Eyebrow>
              {CASCADE_LIMITS.propose_count.min}–{CASCADE_LIMITS.propose_count.max} · clamped by
              what is actually available
            </Eyebrow>
          </div>

          {/* A stored configuration the hub refuses to run reports itself here, in its own
              words — the toggle above reads false while this is set, and that is the reason. */}
          {cascadeRow?.problem && (
            <div className="text-[12px] text-[var(--danger)] bg-[var(--danger-wash)] rounded-[var(--r-sm)] px-3 py-2 mb-2">
              {cascadeRow.problem}
            </div>
          )}
          {cascadeRow?.propose_agent_problem && (
            <div className="text-[12px] text-[var(--amber)] px-3 py-2 mb-2">
              {cascadeRow.propose_agent_problem}
            </div>
          )}

          {cascadeRow && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] font-mono text-[var(--ink-dim)]">
              {cascadeRow.due?.length > 0 && (
                <span className="text-[var(--brass-ink)]">next · {cascadeRow.due.join(", ")}</span>
              )}
              {cascadeRow.counts && (
                <span>
                  counts ·{" "}
                  {(Object.entries(cascadeRow.counts) as [string, number][])
                    .map(([s, v]) => `${s}:${v}`)
                    .join("  ")}
                </span>
              )}
            </div>
          )}
        </Card>
      </motion.div>

      {/* discovery */}
      <motion.div {...sectionMotion(6, reduced)}>
        <Card className="p-5">
          <SectionHead
            eyebrow="Discovery"
            title="Auto-expand the niche"
            right={
              <Switch
                checked={cfg.discovery?.enabled ?? false}
                onChange={toggleDiscovery}
                label="Enable discovery"
              />
            }
          />
          <p className="text-[13px] text-[var(--ink-dim)] mb-3">
            Off by default — the run only analyzes handpicked pages. Discovery needs a burner
            session and is opt-in.
          </p>
          <Eyebrow className="mb-2">Keywords</Eyebrow>
          <div className="chips mb-3">
            {(cfg.discovery?.keywords ?? []).map((kw) => (
              <span className="chip" key={kw}>
                {kw}
                <button onClick={() => removeKeyword(kw)} aria-label={`Remove ${kw}`}>
                  <IconX size={12} />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2 max-w-sm">
            <Input
              placeholder="Add keyword…"
              value={newKeyword}
              onChange={(e) => setNewKeyword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addKeyword()}
            />
            <Button variant="outline" onClick={addKeyword}>
              Add
            </Button>
          </div>
        </Card>
      </motion.div>

      {/* pages */}
      <motion.div {...sectionMotion(7, reduced)} id="config-pages">
        <Card className="p-5">
          <SectionHead
            eyebrow={`pages.txt · ${pages.length} handle${pages.length === 1 ? "" : "s"}`}
            title="The watchlist"
          />
          <div className="pages-list mb-3">
            {/* pages.txt is empty on a fresh install — say so rather than leaving a gap
                under a live header that reads as a rendering failure. */}
            {pages.length === 0 && (
              <p className="text-[13px] text-[var(--ink-dim)] py-2">
                No handles yet. Add the creators you want to mine below — this is where the pipeline
                starts.
              </p>
            )}
            {pages.map((p) => (
              <div className="page-row" key={p}>
                <IconPin size={14} className="text-[var(--brass)]" />
                <span className="handle">
                  {p.replace(/^https?:\/\/(www\.)?instagram\.com\//, "@")}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => removePage(p)}
                  aria-label="Remove page"
                >
                  <IconX size={14} />
                </Button>
              </div>
            ))}
          </div>
          <div className="flex gap-2 max-w-lg">
            <Input
              ref={addHandleRef}
              placeholder="instagram.com/handle or @handle…"
              value={newPage}
              onChange={(e) => setNewPage(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addPage()}
            />
            <Button variant="outline" onClick={addPage}>
              Pin page
            </Button>
          </div>
        </Card>
      </motion.div>
    </div>
  );
}

function emptyDiscovery() {
  return {
    enabled: false,
    keywords: [],
    seeds: [],
    search_terms: 6,
    per_query: 50,
    max_candidates: 0,
    min_followers: 5000,
    expand_related: true,
  };
}
