import { memo, useRef, useState } from "react";
import { Badge } from "./ui";
import { TapeGauge } from "./gauges";
import { IconCheck, IconPlay, IconSound } from "./icons";
import { ChalkCircle } from "./ChalkCircle";
import { compact, pct, score, times } from "../lib/format";
import { isTopTier, tierMeta } from "../lib/tiers";
import { scoreColor } from "../lib/evalModel";
import type { Reel } from "../lib/types";

/**
 * A reel "swatch": poster on the mat, video previews on hover, full player
 * on click. Top-tier reels get the editor's chalk circle.
 */
export const ReelCard = memo(function ReelCard({
  reel,
  onOpen,
}: {
  reel: Reel;
  onOpen: (r: Reel) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [canHoverPlay, setCanHoverPlay] = useState(false);
  const [imgOk, setImgOk] = useState(true);
  const tier = tierMeta(reel.tier);
  const top = isTopTier(reel.tier);
  const hasPoster = !!reel.thumb_url && imgOk;

  function onEnter() {
    if (!reel.video_url) return;
    setCanHoverPlay(true);
    const v = videoRef.current;
    if (v) {
      v.currentTime = 0;
      v.play().catch(() => {});
    }
  }
  function onLeave() {
    const v = videoRef.current;
    if (v) v.pause();
  }

  const card = (
    <article
      className="reel-card mat ui-card--interactive"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onClick={() => onOpen(reel)}
      tabIndex={0}
      role="button"
      aria-label={`${reel.creator} — virality ${score(reel.virality_score)}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(reel);
        }
      }}
    >
      <div className="reel-card__media">
        {hasPoster ? (
          <img
            src={reel.thumb_url!}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            onError={() => setImgOk(false)}
          />
        ) : (
          /* expired CDN poster / no media yet — an on-brand pattern placeholder */
          <div
            className="reel-card__placeholder"
            style={{ ["--seed" as string]: (reel.content_id.charCodeAt(0) % 360) + "deg" }}
          >
            <span className="reel-card__monogram font-display">
              @{reel.creator?.slice(0, 2) ?? "··"}
            </span>
            <span className="reel-card__placeholder-note eyebrow">media not persisted</span>
          </div>
        )}
        {canHoverPlay && reel.video_url && (
          <video
            ref={videoRef}
            src={reel.video_url}
            muted
            loop
            playsInline
            preload="none"
            poster={reel.thumb_url ?? undefined}
          />
        )}
        <div className="reel-card__scrim" />
        <div className="reel-card__tier">
          <Badge tone={tier.tone}>{reel.tier ?? "—"}</Badge>
          <div className="reel-card__marks">
            {reel.analyzed && (
              <span className="reel-mark reel-mark--analyzed" title="Blueprint ready">
                <IconCheck size={11} /> blueprint
              </span>
            )}
            {reel.audio_id && (
              <span
                className="reel-mark reel-mark--audio"
                title={reel.audio_title ? `Sound: ${reel.audio_title}` : "Sound attached"}
              >
                <IconSound size={11} />
              </span>
            )}
            {reel.eval_score != null && (
              <span
                className="reel-mark reel-mark--qc font-mono tnum"
                title="Self-eval score"
                style={{ color: scoreColor(reel.eval_score) }}
              >
                {Math.round(reel.eval_score)}
              </span>
            )}
          </div>
        </div>
        {reel.video_url && (
          <span className="reel-card__play" aria-hidden="true">
            <IconPlay size={14} />
          </span>
        )}
        <div className="reel-card__creator">
          <div className="name truncate">@{reel.creator}</div>
          <div className="sub">
            {compact(reel.creator_followers)} followers · {compact(reel.plays)} plays
          </div>
        </div>
      </div>

      <div className="reel-card__meta">
        <div className="reel-card__score">
          <span className="val font-display" style={{ color: tier.color }}>
            {score(reel.virality_score)}
          </span>
          <div className="flex-1 min-w-0">
            <TapeGauge value={reel.virality_score ?? 0} tierColor={tier.color} height={7} />
          </div>
        </div>
        <div className="reel-card__signals">
          <Signal k="reach" v={times(reel.reach_multiplier)} />
          <Signal k="outlier" v={times(reel.outlier_score, 0)} />
          <Signal k="engage" v={pct(reel.engagement_rate)} />
          <Signal k="vel" v={compact(reel.velocity)} />
        </div>
      </div>
    </article>
  );

  return top ? <ChalkCircle>{card}</ChalkCircle> : card;
});

function Signal({ k, v }: { k: string; v: string }) {
  return (
    <div className="reel-card__signal">
      <div className="v tnum">{v}</div>
      <div className="k">{k}</div>
    </div>
  );
}
