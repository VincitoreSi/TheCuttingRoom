import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AudioCard } from "./AudioCard";
import { CopyButton } from "./CopyButton";
import { Badge, Button, Eyebrow } from "./ui";
import { IconExternal, IconFilm, IconMuted, IconX } from "./icons";
import { ConfirmOverlay, ProgressBar, StatePlate, ctaLabel, renderView } from "./RenderSwatch";
import { Markdown } from "../lib/markdown";
import { EASE } from "../lib/motion";
import { useReducedMotion } from "../lib/hooks";
import { compact, seconds } from "../lib/format";
import { aspectRatioOf } from "../lib/renderJoin";
import { humanizeAgent } from "../lib/agents";
import { cx } from "../lib/cx";
import type { Job, Proposal, RenderRecord } from "../lib/types";

type Tab = "kit" | "sound" | "script";

/**
 * The workbench behind a render swatch: the full-size player, the whole post
 * kit (caption, alternates, hashtags, frames, on-disk path), the full sound
 * sheet, and the script — plus the actions that used to live on the wide card.
 *
 * This is the ONLY modal the Renders tab mounts. "Open full script" is a tab
 * here rather than a hop into ProposalModal, and Un-approve is in the footer:
 * two stacked modals would both lock body scroll and both bind Escape, so one
 * Escape closed both and the inner unmount unlocked scrolling behind the outer.
 */
export function RenderModal({
  proposal,
  render,
  job,
  progress,
  busy,
  onClose,
  onRender,
  onUnapprove,
}: {
  proposal: Proposal;
  render?: RenderRecord;
  job?: Job;
  progress?: { frame: number; total: number };
  busy?: boolean;
  onClose: () => void;
  onRender: (force: boolean) => void;
  onUnapprove: () => void;
}) {
  const [tab, setTab] = useState<Tab>("kit");
  const [confirming, setConfirming] = useState(false);
  const reduced = useReducedMotion();

  const v = renderView(proposal, render, job);
  const sound = v.sound;
  const frames = render?.frames ?? [];

  // Escape backs out of an armed confirm FIRST, and only closes the modal when
  // nothing is armed — so a stray Escape cannot lose a half-made decision.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (confirming) setConfirming(false);
      else onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose, confirming]);

  // Width-drive the media column from the record's own ratio: the track is
  // capped at 400px OR at whatever width the clip needs to fit 84vh tall,
  // whichever is smaller. Set as a CSS VAR, never as an inline
  // grid-template-columns, so the 720px media query can re-point it.
  const [w, h] = aspectRatioOf(render)
    .split("/")
    .map((n) => Number(n.trim()));
  const track = `min(400px, calc(84vh * ${w} / ${h}))`;

  return (
    <div
      className="modal-scrim"
      // Same guard as Escape: while the cost confirm is armed, an outside click backs out
      // of the confirm rather than discarding the half-made decision along with the modal.
      onClick={() => (confirming ? setConfirming(false) : onClose())}
      role="dialog"
      aria-modal="true"
      aria-label={`Render: ${v.title}`}
    >
      <motion.div
        className="modal modal--render"
        style={{ ["--render-media-track" as string]: track }}
        onClick={(e) => e.stopPropagation()}
        initial={reduced ? false : { opacity: 0, scale: 0.97, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.28, ease: EASE }}
      >
        <div className="modal__media modal__media--render">
          <button className="modal__close" onClick={onClose} aria-label="Close">
            <IconX size={16} />
          </button>
          {v.hasVideo && (
            // the mute-toggle offset convention; there is no mute toggle
            // because every render the pipeline makes today is silent
            <a
              className="modal__close"
              style={{ right: 52 }}
              href={render?.video_url ?? undefined}
              target="_blank"
              rel="noreferrer"
              aria-label="Open the MP4"
            >
              <IconExternal size={16} />
            </a>
          )}

          {v.hasVideo ? (
            <video
              controls
              autoPlay
              muted
              loop
              playsInline
              poster={render?.poster_url ?? undefined}
              src={render?.video_url ?? undefined}
            />
          ) : (
            <div
              className="render-swatch__plate-box"
              style={{ aspectRatio: aspectRatioOf(render) }}
            >
              <StatePlate view={v} job={job} seed={proposal.file.charCodeAt(0) % 360} glyph={44} />
            </div>
          )}

          {v.working && <ProgressBar progress={progress} />}

          {confirming && (
            <ConfirmOverlay
              view={v}
              busy={busy}
              onCancel={() => setConfirming(false)}
              onConfirm={() => {
                setConfirming(false);
                onRender(v.hasVideo);
              }}
            />
          )}
        </div>

        <div className="modal__body">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-display text-[22px] leading-tight">{v.title}</div>
              <div className="eyebrow mt-1">
                {humanizeAgent(v.agent ?? "") || "Unattributed"}
                {v.kind ? ` · ${v.kind}` : ""}
              </div>
            </div>
            <div className="flex flex-col items-end gap-1.5 flex-none">
              <Badge tone={v.stateTone}>{v.stateLabel}</Badge>
              {render && render.has_audio === false && (
                <Badge tone="neutral">
                  <IconMuted size={11} /> silent
                </Badge>
              )}
            </div>
          </div>

          {v.errored && (
            <div className="flex flex-col gap-1.5">
              <Badge tone="danger">render error</Badge>
              {/* a LOG cap, not a media cap — the tail is unbounded output */}
              {job?.tail && <pre className="render-swatch__tail">{job.tail}</pre>}
            </div>
          )}

          <div className="segmented" role="tablist" aria-label="Render detail">
            <button
              role="tab"
              aria-selected={tab === "kit"}
              className={cx("segmented__btn", tab === "kit" && "segmented__btn--active")}
              onClick={() => setTab("kit")}
            >
              Post kit
            </button>
            <button
              role="tab"
              aria-selected={tab === "sound"}
              className={cx("segmented__btn", tab === "sound" && "segmented__btn--active")}
              onClick={() => setTab("sound")}
            >
              Sound
              {sound.title && <span className="segmented__dot" aria-hidden="true" />}
            </button>
            <button
              role="tab"
              aria-selected={tab === "script"}
              className={cx("segmented__btn", tab === "script" && "segmented__btn--active")}
              onClick={() => setTab("script")}
            >
              Script
            </button>
          </div>

          {tab === "kit" && (
            <div className="flex flex-col gap-4">
              {/* Only meaningful once a render exists — before that every field is "—"
                  and the frame count would contradict the "render N frames" CTA below. */}
              {v.hasVideo && (
                <div className="render-swatch__spec font-mono tnum">
                  {seconds(render?.duration_s)} · {frames.length} frames · {render?.width ?? "—"}×
                  {render?.height ?? "—"} · {render?.fps ?? "—"} fps · {compact(render?.bytes)} ·{" "}
                  {render?.provider ?? "unknown"}
                </div>
              )}

              {render?.caption ? (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <Eyebrow>Caption</Eyebrow>
                    <CopyButton
                      value={render.caption}
                      label="Copy caption"
                      copied="Caption copied"
                    />
                  </div>
                  <div className="modal__caption">{render.caption}</div>
                </div>
              ) : (
                <p className="render-swatch__muted">
                  No caption generated yet — write one in the composer.
                </p>
              )}

              {render?.alt_captions && render.alt_captions.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <Eyebrow>Alternates</Eyebrow>
                  {render.alt_captions.map((alt, i) => (
                    <div key={i} className="flex flex-col gap-1">
                      <div className="modal__caption">{alt}</div>
                      <div>
                        <CopyButton value={alt} label="Copy alternate" copied="Caption copied" />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {render?.hashtags && render.hashtags.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <Eyebrow>Hashtags</Eyebrow>
                  <div className="flex flex-wrap gap-1">
                    {render.hashtags.map((tag) => (
                      <Badge key={tag} tone="neutral">
                        {tag.startsWith("#") ? tag : `#${tag}`}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {frames.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <Eyebrow>Frames</Eyebrow>
                  <ul className="render-swatch__frames">
                    {frames.map((f, i) => (
                      <li key={`${f.frame}-${i}`}>
                        <div>
                          {f.frame} · {seconds(f.duration_s)} ·{" "}
                          {f.kb != null ? compact(f.kb * 1024) : "—"}
                        </div>
                        <div className="render-swatch__ost">{f.on_screen_text || "—"}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {render?.local_path && (
                <div className="render-swatch__path">
                  {/* The <code> is direction:rtl so the ellipsis eats the path's
                      start rather than its filename. <bdi> isolates the text
                      back to LTR — without it the leading "/" is bidi-reordered
                      onto the end and the path reads as a directory. */}
                  <code>
                    <bdi>{render.local_path}</bdi>
                  </code>
                  <CopyButton value={render.local_path} label="Copy path" copied="Path copied" />
                </div>
              )}
            </div>
          )}

          {tab === "sound" && (
            <div className="flex flex-col gap-4">
              {/* No compact strip here: AudioCard's own head already prints the
                  title + artist and its actions row already carries the sound
                  page link + copy, so stacking the strip on top rendered the
                  same name twice with two near-duplicate action pairs. The card
                  on the grid keeps the strip; the modal shows the full sheet. */}
              {/* Feeding the parsed values in lights up AudioCard's title,
                  artist, chips and "Copy IG sound link" row; rawMarkdown still
                  renders the music brief and substitute guidance verbatim. */}
              <AudioCard
                audio={{
                  audio_title: sound.title ?? undefined,
                  audio_artist: sound.artist ?? undefined,
                  sound_page_url: sound.soundPageUrl ?? undefined,
                }}
                strategy={{
                  audio_type: sound.audioType ?? undefined,
                  reuse_recommendation: sound.reuse ?? undefined,
                  substitute_brief: sound.substituteBrief ?? undefined,
                }}
                rawMarkdown={v.audioBlock ?? undefined}
              />
            </div>
          )}

          {/* the body already scrolls — no inner max-height */}
          {tab === "script" && (
            <div>
              <Markdown text={proposal.text} />
            </div>
          )}

          <div className="render-swatch__foot">
            <Button variant="ghost" size="sm" title="Send back to proposed" onClick={onUnapprove}>
              Un-approve
            </Button>
            <div className="flex-1" />
            <Button
              size="sm"
              variant={v.hasVideo || v.errored || v.working ? "outline" : "primary"}
              className={cx(!v.hasVideo && !v.working && "render-swatch__cta")}
              disabled={busy || v.working}
              onClick={() => setConfirming(true)}
            >
              <IconFilm size={14} /> {ctaLabel(v)}
            </Button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
