import { Badge } from "./ui";
import { CopyButton } from "./CopyButton";
import { IconLink, IconSound } from "./icons";
import { Markdown } from "../lib/markdown";
import { safeUrl } from "../lib/url";
import type { BlueprintAudio, BlueprintAudioStrategy } from "../lib/types";

/**
 * The shared "sound sheet" — reused in the ReelModal Blueprint tab and in the
 * Studio "Ready to post" list. Instagram has no sound API, so posting is manual:
 * the card exists to make the attach-in-IG step impossible to miss (§9.4, D3c).
 *
 * Two feeds:
 *  - structured `audio` + `strategy` from a schema-v2 blueprint, or
 *  - a raw `## Audio` markdown block lifted from a studio proposal.
 */
export function AudioCard({
  audio,
  strategy,
  rawMarkdown,
  compact,
}: {
  audio?: BlueprintAudio;
  strategy?: BlueprintAudioStrategy;
  rawMarkdown?: string;
  compact?: boolean;
}) {
  const title = audio?.audio_title;
  const artist = audio?.audio_artist;
  const url = audio?.sound_page_url;
  const beats = strategy?.beat_markers_s ?? [];

  const reuse =
    strategy?.reuse_recommendation ??
    (audio?.audio_is_reusable === false ? "cannot_reuse" : undefined);

  return (
    <section className={`audio-card${compact ? " audio-card--compact" : ""}`}>
      <div className="audio-card__head">
        <span className="audio-card__glyph" aria-hidden="true">
          <IconSound size={16} />
        </span>
        <div className="min-w-0">
          <div className="eyebrow">Audio · attach manually in Instagram</div>
          {title ? (
            <div className="audio-card__title font-display">{title}</div>
          ) : (
            <div className="audio-card__title font-display text-[var(--ink-dim)]">Sound sheet</div>
          )}
          {artist && <div className="audio-card__artist">{artist}</div>}
        </div>
      </div>

      {(strategy || audio) && (
        <div className="audio-card__chips">
          {strategy?.audio_type && <Badge tone="brass">{label(strategy.audio_type)}</Badge>}
          {audio?.audio_is_original && <Badge tone="neutral">original audio</Badge>}
          {reuse && <Badge tone={reuseTone(reuse)}>{label(reuse)}</Badge>}
          {audio?.music_genre && <Badge tone="neutral">{audio.music_genre}</Badge>}
        </div>
      )}

      {strategy?.substitute_brief && reuse !== "reuse_original" && (
        <p className="audio-card__note">
          <span className="eyebrow">If substituting</span> {strategy.substitute_brief}
        </p>
      )}

      {beats.length > 0 && (
        <div className="audio-card__beats" title="Beat markers (seconds)">
          <span className="eyebrow">Beats</span>
          {beats.slice(0, 24).map((b, i) => (
            <span key={i} className="audio-card__beat font-mono tnum">
              {b.toFixed(1)}
            </span>
          ))}
        </div>
      )}

      {url && safeUrl(url) && (
        <div className="audio-card__actions">
          <a href={safeUrl(url)} target="_blank" rel="noreferrer" className="audio-card__link">
            <IconLink size={13} /> Open sound page
          </a>
          <CopyButton value={url} label="Copy IG sound link" copied="Link copied" />
        </div>
      )}

      {rawMarkdown && (
        <div className="audio-card__raw">
          <Markdown text={rawMarkdown} />
        </div>
      )}

      <div className="audio-card__foot eyebrow">
        Instagram has no post API — open the composer and attach this sound by hand.
      </div>
    </section>
  );
}

function label(s: string): string {
  return s.replace(/_/g, " ");
}

/** Exact tokens, never `includes("reuse")` — "cannot_reuse" contains "reuse",
    which painted a licensed sound sage/positive on the one signal that decides
    whether the operator may attach the original at all. */
function reuseTone(reuse: string): "sage" | "amber" | "neutral" {
  const token = reuse.trim().toLowerCase();
  if (token === "reuse_original") return "sage";
  if (/^(cannot|do_not|no)_?reuse/.test(token)) return "amber";
  return "neutral";
}
