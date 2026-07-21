import { useMemo, useState } from "react";
import { useShell } from "../App";
import { useTrending } from "../lib/hooks";
import { TapeGauge } from "../components/gauges";
import { Badge, Card, EmptyState, Eyebrow, Input, SectionHead, Select } from "../components/ui";
import { CopyButton } from "../components/CopyButton";
import { IconExternal, IconSearch, IconSound } from "../components/icons";
import { compact, score } from "../lib/format";
import { cx } from "../lib/cx";
import { safeUrl } from "../lib/url";
import type { TrendingSound } from "../lib/types";

/* bucket → emphasis. Rising/Hot are surfaced; Saturated is muted (§9.2) — with
   the honest caveat that "trending" here means rising within the tracked
   creators, not the platform-wide chart (§8). */
function bucketTone(bucket: string): "brass" | "sage" | "neutral" {
  const b = bucket.toLowerCase();
  if (b.includes("hot")) return "brass";
  if (b.includes("rising")) return "sage";
  return "neutral"; // steady / saturated
}
function bucketMuted(bucket: string): boolean {
  return bucket.toLowerCase().includes("saturat");
}

export function SoundsView() {
  const { platform } = useShell();
  const trendingQ = useTrending(platform);
  const [q, setQ] = useState("");
  const [bucket, setBucket] = useState("all");

  const sounds = trendingQ.data ?? [];
  const buckets = useMemo(() => Array.from(new Set(sounds.map((s) => s.bucket))), [sounds]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sounds.filter((s) => {
      if (bucket !== "all" && s.bucket !== bucket) return false;
      if (!needle) return true;
      return s.title.toLowerCase().includes(needle) || s.artist.toLowerCase().includes(needle);
    });
  }, [sounds, q, bucket]);

  return (
    <div className="flex flex-col gap-4">
      <SectionHead
        eyebrow={`${platform} · rising within tracked creators`}
        title="The Sound Rack"
        right={
          <Eyebrow>
            {rows.length} {rows.length === 1 ? "sound" : "sounds"}
          </Eyebrow>
        }
      />

      <Card className="p-3 sound-caveat">
        <IconSound size={16} />
        <span>
          "Trending" is derived from your scraped reels — a sound rising among the creators you
          track, not Instagram's global chart. Treat it as a signal, not the truth.
        </span>
      </Card>

      <div className="corpus-toolbar">
        <div className="corpus-toolbar__search">
          <IconSearch size={16} />
          <Input
            placeholder="Search title or artist…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search sounds"
          />
        </div>
        <Select
          value={bucket}
          onChange={(e) => setBucket(e.target.value)}
          aria-label="Filter bucket"
        >
          <option value="all">All buckets</option>
          {buckets.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </Select>
      </div>

      {trendingQ.isLoading ? (
        <div className="skeleton" style={{ height: 360 }} />
      ) : sounds.length === 0 ? (
        <EmptyState
          icon={<IconSound size={28} />}
          title="No sounds tracked yet"
          hint="Scrape and persist media to extract the audio each reel uses; rising sounds surface here."
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<IconSearch size={28} />}
          title="No sounds match your filters"
          hint="Nothing matches this search and bucket. Clear the search or pick a different bucket to see more."
        />
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="sound-table" role="table">
            <div className="sound-row sound-row--head" role="row">
              <span role="columnheader">Sound</span>
              <span role="columnheader">Trend</span>
              <span role="columnheader" className="hide-sm">
                Uses
              </span>
              <span role="columnheader" className="hide-sm">
                Reusable
              </span>
              <span role="columnheader">Example</span>
              <span role="columnheader" aria-label="actions" />
            </div>
            {rows.map((s) => (
              <SoundRow key={s.audio_id} sound={s} platform={platform} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function SoundRow({ sound: s, platform }: { sound: TrendingSound; platform: string }) {
  const muted = bucketMuted(s.bucket);
  return (
    <div className={cx("sound-row", muted && "sound-row--muted")} role="row">
      <div className="sound-cell sound-cell--title" role="cell">
        <div className="flex items-center gap-2 min-w-0">
          <span className="sound-glyph" aria-hidden="true">
            <IconSound size={14} />
          </span>
          <div className="min-w-0">
            <div className="sound-title font-display truncate">{s.title}</div>
            <div className="sound-artist truncate">
              {s.artist}
              {s.is_original && <span className="sound-orig"> · original</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="sound-cell sound-cell--trend" role="cell">
        <div className="flex items-center gap-2">
          <Badge tone={bucketTone(s.bucket)}>{s.bucket}</Badge>
        </div>
        <div className="sound-trend-gauge">
          <TapeGauge
            value={s.trend_score}
            tierColor={muted ? "var(--ink-faint)" : undefined}
            height={6}
            showTicks={false}
          />
        </div>
      </div>

      <div className="sound-cell hide-sm font-mono tnum" role="cell">
        {s.uses_in_corpus}
        {s.recent_uses ? <span className="sound-recent"> ({s.recent_uses} recent)</span> : null}
      </div>

      <div className="sound-cell hide-sm" role="cell">
        {s.is_reusable ? <Badge tone="sage">reusable</Badge> : <span className="eyebrow">no</span>}
      </div>

      <div className="sound-cell sound-cell--example" role="cell">
        {s.example ? (
          <a
            href={safeUrl(s.example.url)}
            target="_blank"
            rel="noreferrer"
            className="sound-example"
            title={`Example reel · virality ${score(s.example.virality_score)}`}
          >
            <img
              src={`/media/${platform}/${s.example.content_id}.jpg`}
              alt=""
              loading="lazy"
              onError={(e) => (e.currentTarget.style.display = "none")}
            />
            <span className="font-mono tnum">{compact(s.example.virality_score, 0)}</span>
          </a>
        ) : (
          <span className="eyebrow">—</span>
        )}
      </div>

      <div className="sound-cell sound-cell--actions" role="cell">
        <a
          href={safeUrl(s.sound_page_url)}
          target="_blank"
          rel="noreferrer"
          className="sound-open"
          title="Open sound page"
        >
          <IconExternal size={14} />
        </a>
        <CopyButton value={s.sound_page_url} label="Copy" copied="Copied" />
      </div>
    </div>
  );
}
