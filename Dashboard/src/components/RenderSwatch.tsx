import { useEffect, useRef, useState } from "react";
import { CopyButton } from "./CopyButton";
import { Badge, Button } from "./ui";
import { IconFilm, IconLink, IconMuted, IconPlay, IconSound, IconX } from "./icons";
import { compact, grouped, seconds } from "../lib/format";
import { countShots, extractSection, firstHeading } from "../lib/proposalMarkdown";
import { parseAudioBlock } from "../lib/soundStrip";
import type { SoundStrip } from "../lib/soundStrip";
import { aspectRatioOf } from "../lib/renderJoin";
import { humanizeAgent } from "../lib/agents";
import { safeUrl } from "../lib/url";
import { cx } from "../lib/cx";
import type { Job, Proposal, RenderRecord } from "../lib/types";

/** Nano Banana bills roughly this much per generated frame — the number behind
    the confirm step. A running render cannot be cancelled, so a misclick costs
    real money; the operator sees the price before the request goes out. */
export const USD_PER_FRAME = 0.04;

export type RenderState = "rendered" | "rendering" | "failed" | "unrendered";

export interface RenderView {
  title: string;
  audioBlock: string | null;
  sound: SoundStrip;
  agent: string | null;
  kind: string | null;
  working: boolean;
  errored: boolean;
  hasVideo: boolean;
  frameCount: number;
  cost: number;
  state: RenderState;
  stateLabel: string;
  stateTone: "sage" | "oxblood" | "danger" | "neutral";
}

/**
 * Everything the swatch and the detail modal must agree on, derived once from
 * the same two inputs. One value drives the badge, the media layer, the marks
 * and the CTA in lockstep, so a card can never say two things at once — and the
 * modal can never disagree with the card that opened it.
 */
export function renderView(proposal: Proposal, render?: RenderRecord, job?: Job): RenderView {
  const audioBlock = extractSection(proposal.text, "Audio");
  const working = job?.status === "queued" || job?.status === "running";
  const errored = job?.status === "error";
  const hasVideo = !!render?.video_url;

  // Before a render exists the shot list is the frame count — one generated
  // frame per `### Shot N`. Once it exists, the record's own frames are the
  // truth. The `||` is load-bearing: reading frames.length alone prices an
  // unrendered item at $0.00.
  const frameCount = render?.frames?.length || countShots(proposal.text);

  const state: RenderState = working
    ? "rendering"
    : errored
      ? "failed"
      : hasVideo
        ? "rendered"
        : "unrendered";

  return {
    title: firstHeading(proposal.text) ?? proposal.file.replace(/\.md$/, ""),
    audioBlock,
    sound: parseAudioBlock(audioBlock),
    agent: render?.agent ?? proposal.agent ?? null,
    kind: render?.kind ?? proposal.kind ?? null,
    working,
    errored,
    hasVideo,
    frameCount,
    cost: frameCount * USD_PER_FRAME,
    state,
    stateLabel: {
      rendered: "rendered",
      rendering: "rendering",
      failed: "failed",
      unrendered: "not rendered",
    }[state],
    stateTone: (
      { rendered: "sage", rendering: "oxblood", failed: "danger", unrendered: "neutral" } as const
    )[state],
  };
}

/** The label ladder shared by the card CTA and the modal CTA. */
export function ctaLabel(v: RenderView): string {
  if (v.working) return "Rendering…";
  if (v.errored) return "Retry";
  return v.hasVideo ? "Re-render" : "Render";
}

/* ------------------------------------------------------------ Confirm overlay
   Rendered over the MEDIA box (card and modal both), never in the meta band:
   the media gives it a full-height canvas, so nothing reflows and the card does
   not change height when the operator arms the confirm. */
export function ConfirmOverlay({
  view,
  busy,
  onConfirm,
  onCancel,
}: {
  view: RenderView;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="render-swatch__confirm" onClick={onCancel}>
      <div className="eyebrow">Confirm render</div>
      <div className="render-swatch__cost font-display tnum">${view.cost.toFixed(2)}</div>
      <div className="render-swatch__cost-note font-mono">
        {view.frameCount} frames · ~${USD_PER_FRAME.toFixed(2)} per frame
      </div>
      <div className="render-swatch__cost-warn">A running render can’t be cancelled.</div>
      <div className="render-swatch__confirm-actions" onClick={(e) => e.stopPropagation()}>
        <Button
          variant="primary"
          size="sm"
          className="w-full"
          disabled={busy || view.working}
          onClick={onConfirm}
        >
          {view.hasVideo ? "Re-render" : "Render"}
        </Button>
        <Button variant="ghost" size="sm" className="w-full" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ Shared plate
   The un-rendered / working / errored no-media surface. The same element in the
   card and the modal so the two never disagree about what state an item is in. */
export function StatePlate({
  view,
  job,
  seed,
  glyph = 34,
}: {
  view: RenderView;
  job?: Job;
  seed: number;
  glyph?: number;
}) {
  const note = view.errored
    ? "render failed"
    : view.working
      ? (job?.status ?? "queued")
      : view.frameCount > 0
        ? `${view.frameCount} shots in the script`
        : "no shot list found";

  return (
    <div
      className={cx(
        "reel-card__placeholder",
        view.working && "render-swatch__plate--working",
        view.errored && "render-swatch__plate--error",
      )}
      style={{ ["--seed" as string]: `${seed}deg` }}
    >
      <span className="reel-card__monogram" aria-hidden="true">
        {view.errored ? <IconX size={glyph} /> : <IconFilm size={glyph} />}
      </span>
      <span className="reel-card__placeholder-note eyebrow">{note}</span>
    </div>
  );
}

/** The 3px determinate/indeterminate progress hairline on the media's foot.
    "frame 3/6" is unreadable across a grid of towers; a filling bar is not. */
export function ProgressBar({ progress }: { progress?: { frame: number; total: number } }) {
  const determinate = !!progress && progress.total > 0;
  // Without a frame count the bar must NOT sit at full width — a solid, full brass bar
  // reads as "finished" for the entire render. Unknown progress gets a travelling sliver
  // instead, which reads as "working".
  const pctWidth = determinate
    ? `${Math.max(0, Math.min(100, Math.round((progress!.frame / progress!.total) * 100)))}%`
    : undefined;
  return (
    <div
      className={cx("render-swatch__bar", !determinate && "render-swatch__bar--indeterminate")}
      aria-hidden="true"
    >
      <i style={determinate ? { width: pctWidth } : undefined} />
    </div>
  );
}

/* ------------------------------------------------------------ Sound strip
   The compact sound line: what to attach, whether it can be attached, and the
   two one-click affordances (open the IG sound page, copy the search string).
   The long music brief and substitute guidance live in the modal's full sheet. */
export function SoundStripRow({ sound }: { sound: SoundStrip }) {
  const page = safeUrl(sound.soundPageUrl);
  const fallback =
    sound.audioType === "voiceover_led"
      ? "voiceover-led — no sound to attach"
      : sound.present
        ? "no sound in the recipe"
        : "no audio metadata";

  return (
    <>
      <div className="render-swatch__sound">
        <span className="render-swatch__sound-glyph" aria-hidden="true">
          <IconSound size={13} />
        </span>
        <div className="min-w-0">
          {sound.title ? (
            <>
              <div className="render-swatch__sound-title truncate">{sound.title}</div>
              {sound.artist && (
                <div className="render-swatch__sound-artist truncate">{sound.artist}</div>
              )}
            </>
          ) : (
            <div className="render-swatch__sound-artist">{fallback}</div>
          )}
        </div>
      </div>

      {/* never an inert anchor, never a copy button with nothing to copy */}
      {(page || sound.soundLine) && (
        <div className="audio-card__actions">
          {page && (
            <a className="audio-card__link" href={page} target="_blank" rel="noreferrer">
              <IconLink size={13} /> Sound page
            </a>
          )}
          {sound.soundLine && (
            <CopyButton value={sound.soundLine} label="Copy sound" copied="Sound copied" />
          )}
        </div>
      )}
    </>
  );
}

/**
 * One approved studio item as a compact vertical swatch, in the Corpus card's
 * visual language: a media box sized only by the record's own aspect ratio, the
 * scrim, overlay chips, an overlay title, and a meta band beneath.
 *
 * The article is NOT role="button": it contains a copy button, an anchor and a
 * CTA, and nesting interactive content inside a button is invalid and swallows
 * clicks. Instead `.render-swatch__hit` is an absolutely-inset transparent
 * button covering the MEDIA ONLY. The meta band sits outside the hit target
 * entirely, which is why nothing here needs stopPropagation.
 */
export function RenderSwatch({
  proposal,
  render,
  job,
  progress,
  busy,
  onRender,
  onSelect,
}: {
  proposal: Proposal;
  render?: RenderRecord;
  job?: Job;
  /** Optional live frame progress, when a producer reports it. */
  progress?: { frame: number; total: number };
  busy?: boolean;
  onRender: (force: boolean) => void;
  onSelect: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [hover, setHover] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const v = renderView(proposal, render, job);
  const hasPoster = !!render?.poster_url;
  const sound = v.sound;

  // Escape backs out of an armed confirm before anything else can act on it.
  useEffect(() => {
    if (!confirming) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setConfirming(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [confirming]);

  useEffect(() => {
    if (hover) videoRef.current?.play().catch(() => {});
  }, [hover]);

  return (
    <article
      className="render-swatch reel-card mat ui-card--interactive"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* The record's own shape is the ONLY sizing input. No height and no
          max-height, ever: capping the height fights aspect-ratio and puts the
          9:16 reel in letterbox bars. aspectRatioOf(undefined) is 9/16, so an
          un-rendered swatch is already a correctly shaped tower. */}
      <div className="reel-card__media" style={{ aspectRatio: aspectRatioOf(render) }}>
        {hasPoster ? (
          <img
            src={render?.poster_url ?? undefined}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
          />
        ) : (
          <StatePlate view={v} job={job} seed={proposal.file.charCodeAt(0) % 360} />
        )}

        {v.hasVideo && hover && (
          <video
            ref={videoRef}
            muted
            loop
            playsInline
            preload="none"
            poster={render?.poster_url ?? undefined}
            src={render?.video_url ?? undefined}
          />
        )}

        {/* always drawn, including over the plate, so the overlay furniture
            reads identically in every state and the silhouette never changes */}
        <div className="reel-card__scrim" />

        <div className="reel-card__tier">
          <Badge tone={v.stateTone}>{v.stateLabel}</Badge>
          <div className="reel-card__marks">
            {v.working && (
              <span className="reel-mark reel-mark--live">
                {progress ? `${progress.frame}/${progress.total}` : (job?.status ?? "queued")}
              </span>
            )}
            {v.errored && (
              <span className="reel-mark reel-mark--error">
                <IconX size={11} /> failed
              </span>
            )}
            {render && render.has_audio === false && (
              <span className="reel-mark reel-mark--silent">
                <IconMuted size={11} /> silent
              </span>
            )}
            {sound.reusable === true && (
              <span className="reel-mark reel-mark--reusable">reusable</span>
            )}
            {sound.reusable === false && (
              <span className="reel-mark reel-mark--licensed">licensed</span>
            )}
          </div>
        </div>

        {v.working && <ProgressBar progress={progress} />}

        {v.hasVideo && !v.working && (
          <span className="reel-card__play" aria-hidden="true">
            <IconPlay size={14} />
          </span>
        )}

        {/* #f4efe4 over the plate is invisible in light theme — the --plate
            variant swaps in the ink ramp when there is no media beneath. */}
        <div className={cx("reel-card__creator", !hasPoster && "render-swatch__creator--plate")}>
          <div className="name truncate">{v.title}</div>
          <div className="sub truncate">
            {humanizeAgent(v.agent ?? "") || "Unattributed"}
            {v.kind ? ` · ${v.kind}` : ""}
          </div>
        </div>

        <button
          className="render-swatch__hit"
          onClick={onSelect}
          aria-label={`${v.title} — ${v.stateLabel}`}
        />

        {confirming && (
          <ConfirmOverlay
            view={v}
            busy={busy}
            onCancel={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false);
              // force is true exactly when a video already exists
              onRender(v.hasVideo);
            }}
          />
        )}
      </div>

      <div className="reel-card__meta">
        <SoundStripRow sound={sound} />

        <div className="reel-card__signals">
          <div className="reel-card__signal">
            <div className="v tnum">{render?.duration_s ? seconds(render.duration_s) : "—"}</div>
            <div className="k">dur</div>
          </div>
          <div className="reel-card__signal">
            {/* real even before a render — this is what is about to be billed */}
            <div className="v tnum">{grouped(v.frameCount)}</div>
            <div className="k">frames</div>
          </div>
          <div className="reel-card__signal">
            <div className="v tnum">{render?.bytes ? compact(render.bytes) : "—"}</div>
            <div className="k">size</div>
          </div>
          <div className="reel-card__signal">
            <div className="v tnum">{render?.fps ?? "—"}</div>
            <div className="k">fps</div>
          </div>
        </div>

        {/* outside the hit button, so clicking it never opens the modal */}
        <div className="render-swatch__act">
          <Button
            size="sm"
            variant={v.hasVideo || v.errored || v.working ? "outline" : "primary"}
            className={cx("w-full", !v.hasVideo && !v.working && "render-swatch__cta")}
            disabled={busy || v.working}
            onClick={() => setConfirming(true)}
          >
            <IconFilm size={14} /> {ctaLabel(v)}
          </Button>
        </div>
      </div>
    </article>
  );
}
