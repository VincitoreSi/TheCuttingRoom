import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Badge, Button } from "./ui";
import { SignalRing } from "./gauges";
import { BlueprintPanel } from "./BlueprintPanel";
import { safeUrl } from "../lib/url";
import { EASE } from "../lib/motion";
import { IconExternal, IconMuted, IconVolume, IconX } from "./icons";
import { compact, grouped, pct, score, seconds, times } from "../lib/format";
import { tierMeta } from "../lib/tiers";
import { useBlueprint, useReducedMotion } from "../lib/hooks";
import { useShell } from "../App";
import { cx } from "../lib/cx";
import type { Reel } from "../lib/types";

type Tab = "overview" | "blueprint";

export function ReelModal({
  reel,
  onClose,
  initialTab = "overview",
}: {
  reel: Reel;
  onClose: () => void;
  /** Land directly on a tab — e.g. the agent board opens straight to Blueprint. */
  initialTab?: Tab;
}) {
  const { platform } = useShell();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);
  const [tab, setTab] = useState<Tab>(initialTab);
  const reduced = useReducedMotion();
  const tier = tierMeta(reel.tier);
  // fetch lazily — only once the operator opens the Blueprint tab
  const bpQ = useBlueprint(platform, tab === "blueprint" ? reel.content_id : null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const signals = [
    {
      label: "reach",
      value: reel.reach_multiplier ?? 0,
      max: 120,
      display: times(reel.reach_multiplier),
    },
    {
      label: "outlier",
      value: reel.outlier_score ?? 0,
      max: 520,
      display: times(reel.outlier_score, 0),
    },
    {
      label: "engage",
      value: reel.engagement_rate ?? 0,
      max: 8,
      display: pct(reel.engagement_rate),
    },
    {
      label: "velocity",
      value: reel.velocity ?? 0,
      max: 1_400_000,
      display: compact(reel.velocity),
    },
  ];

  return (
    <div className="modal-scrim" onClick={onClose} role="dialog" aria-modal="true">
      <motion.div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        initial={reduced ? false : { opacity: 0, scale: 0.97, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.28, ease: EASE }}
      >
        <div className="modal__media">
          <button className="modal__close" onClick={onClose} aria-label="Close">
            <IconX size={16} />
          </button>
          {reel.video_url ? (
            <video
              ref={videoRef}
              src={reel.video_url}
              poster={reel.thumb_url ?? undefined}
              controls
              autoPlay
              muted={muted}
              loop
              playsInline
            />
          ) : reel.thumb_url ? (
            <img src={reel.thumb_url} alt="" style={{ width: "100%", objectFit: "contain" }} />
          ) : null}
          {reel.video_url && (
            <button
              className="modal__close"
              style={{ right: 52 }}
              onClick={() => setMuted((m) => !m)}
              aria-label={muted ? "Unmute" : "Mute"}
            >
              {muted ? <IconMuted size={16} /> : <IconVolume size={16} />}
            </button>
          )}
        </div>

        <div className="modal__body">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-display text-[22px] leading-tight truncate">@{reel.creator}</div>
              <div className="eyebrow mt-1">
                {compact(reel.creator_followers)} followers · {seconds(reel.duration_s)} ·{" "}
                {reel.video_local ? "clip saved" : "cdn preview"}
              </div>
            </div>
            <Badge tone={tier.tone}>{reel.tier ?? "—"}</Badge>
          </div>

          {/* Overview ↔ Blueprint */}
          <div className="segmented" role="tablist" aria-label="Clip detail">
            <button
              role="tab"
              aria-selected={tab === "overview"}
              className={cx("segmented__btn", tab === "overview" && "segmented__btn--active")}
              onClick={() => setTab("overview")}
            >
              Overview
            </button>
            <button
              role="tab"
              aria-selected={tab === "blueprint"}
              className={cx("segmented__btn", tab === "blueprint" && "segmented__btn--active")}
              onClick={() => setTab("blueprint")}
            >
              Blueprint
              {reel.analyzed && <span className="segmented__dot" aria-hidden="true" />}
            </button>
          </div>

          {tab === "blueprint" ? (
            <BlueprintTab
              loading={bpQ.isLoading}
              blueprint={bpQ.data}
              error={bpQ.isError}
              analyzed={reel.analyzed}
            />
          ) : (
            <>
              <div className="flex items-end gap-4">
                <div>
                  <div className="eyebrow">Virality</div>
                  <div
                    className="font-display leading-none"
                    style={{ fontSize: 52, color: tier.color }}
                  >
                    {score(reel.virality_score)}
                  </div>
                </div>
                <div className="flex gap-3 pb-1">
                  {signals.map((s) => (
                    <SignalRing
                      key={s.label}
                      value={s.value}
                      max={s.max}
                      label={s.label}
                      color={tier.color}
                    />
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
                <Stat k="Plays" v={grouped(reel.plays)} />
                <Stat k="Reach ×" v={times(reel.reach_multiplier)} />
                <Stat k="Outlier ×" v={times(reel.outlier_score, 0)} />
                <Stat k="Engagement" v={pct(reel.engagement_rate)} />
                <Stat k="Velocity /day" v={compact(reel.velocity)} />
                <Stat k="Posted" v={reel.posted?.slice(0, 10) ?? "—"} />
              </div>

              {reel.caption && (
                <div>
                  <div className="eyebrow mb-1.5">Caption</div>
                  <div className="modal__caption">{reel.caption}</div>
                </div>
              )}

              <div className="mt-auto pt-2">
                {safeUrl(reel.url) ? (
                  <a href={safeUrl(reel.url)} target="_blank" rel="noreferrer">
                    <Button variant="outline" className="w-full">
                      Open on Instagram <IconExternal size={14} />
                    </Button>
                  </a>
                ) : (
                  <Button variant="outline" className="w-full" disabled title="No valid link">
                    Open on Instagram <IconExternal size={14} />
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function BlueprintTab({
  loading,
  blueprint,
  error,
  analyzed,
}: {
  loading: boolean;
  blueprint?: import("../lib/types").Blueprint;
  error: boolean;
  analyzed?: boolean;
}) {
  if (loading) return <div className="skeleton" style={{ height: 320 }} />;
  if (blueprint) return <BlueprintPanel blueprint={blueprint} />;
  // 404 or nothing yet — an honest empty state, not an error
  return (
    <div className="bp-empty">
      <div className="font-display text-[16px] text-[var(--ink)]">No blueprint yet</div>
      <p className="text-[13px] text-[var(--ink-dim)] mt-1 max-w-sm">
        {analyzed
          ? "This clip is marked analyzed but the blueprint could not be loaded."
          : "Run the Blueprint stage from the Board to have AnalysisEngine turn this clip into a shot-by-shot, generation-ready plan."}
      </p>
      {error && !analyzed && (
        <p className="eyebrow mt-3">AnalysisEngine hasn't reached this clip.</p>
      )}
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between border-b border-[var(--line)] py-1.5">
      <span className="text-[var(--ink-dim)]">{k}</span>
      <span className="font-mono tnum text-[var(--ink)]">{v}</span>
    </div>
  );
}
