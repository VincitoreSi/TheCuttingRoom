import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useShell } from "../App";
import { useConfig, useReducedMotion, useSaveConfig } from "../lib/hooks";
import { Button, Card, Eyebrow, Input, SectionHead, Switch } from "../components/ui";
import { RangeSlider } from "../components/ui";
import { IconCheck, IconPin, IconX } from "../components/icons";
import { KeysAndModels } from "../components/config/KeysAndModels";
import { sectionMotion } from "../lib/motion";
import { consumeConfigFocus } from "../lib/nav";
import type { NicheConfig, Tier } from "../lib/types";
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
    // preventScroll: focus must never move the viewport — an unguarded focus scroll
    // moved the window and blanked the whole shell (sidebar included).
    const t = setTimeout(() => addHandleRef.current?.focus({ preventScroll: true }), 600);
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

      {/* discovery */}
      <motion.div {...sectionMotion(4, reduced)}>
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
      <motion.div {...sectionMotion(5, reduced)} id="config-pages">
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
