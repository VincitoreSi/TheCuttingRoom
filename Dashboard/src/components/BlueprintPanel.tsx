import { useState, type ReactNode } from "react";
import { AudioCard } from "./AudioCard";
import { CopyButton } from "./CopyButton";
import { Badge } from "./ui";
import { TapeGauge } from "./gauges";
import { IconChevron } from "./icons";
import { cx } from "../lib/cx";
import { scoreColor } from "../lib/evalModel";
import type { Blueprint, BlueprintShot } from "../lib/types";

/**
 * The generation-ready blueprint (schema v2) rendered for one clip. This is the
 * document every producer reads (§4) — so it leads with what an operator copies
 * out: the score, the per-shot prompts, the regeneration guide, and the sound.
 */
export function BlueprintPanel({ blueprint: bp }: { blueprint: Blueprint }) {
  const evalScore = bp.evaluation?.score_0_100;
  const shots = bp.shots ?? [];
  const overlays = bp.text_overlays ?? [];
  const chars = bp.characters_and_subjects ?? [];
  const vf = bp.virality_formula;
  const rg = bp.regeneration_guide;

  return (
    <div className="bp">
      {/* header: eval score as a tape reading + verdict */}
      <div className="bp__topline">
        <div className="min-w-0">
          <div className="eyebrow">
            {bp.is_reference ? "Reference blueprint" : "Blueprint"} · v{bp.schema_version} ·{" "}
            {bp.model ?? "—"}
          </div>
          {bp.video_metadata?.one_line_summary && (
            <p className="bp__summary">{bp.video_metadata.one_line_summary}</p>
          )}
        </div>
        {evalScore != null && (
          <div className="bp__score">
            <div className="eyebrow">Self-eval</div>
            <div className="bp__score-num font-display" style={{ color: scoreColor(evalScore) }}>
              {evalScore.toFixed(0)}
            </div>
            {bp.evaluation?.verdict && (
              <Badge tone={bp.evaluation.verdict === "accept" ? "sage" : "neutral"}>
                {bp.evaluation.verdict}
              </Badge>
            )}
          </div>
        )}
      </div>

      {evalScore != null && (
        <div className="bp__evalbar">
          <TapeGauge value={evalScore} tierColor={scoreColor(evalScore)} height={7} />
        </div>
      )}

      {/* per-criterion self-eval — the QC readout */}
      {bp.evaluation?.per_criterion && (
        <div className="bp__criteria">
          {Object.entries(bp.evaluation.per_criterion).map(([k, v]) => (
            <div key={k} className="bp__criterion" title={`${k}: ${v}`}>
              <span className="bp__criterion-k">{k.replace(/_/g, " ")}</span>
              <span
                className="bp__criterion-v font-mono tnum"
                style={{ color: scoreColor(Number(v)) }}
              >
                {v}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* virality formula */}
      {vf && (
        <Section title="Virality formula" eyebrow="Why it travels">
          {vf.hook && (
            <div className="bp__row">
              <span className="bp__row-k">Hook</span>
              <span className="bp__row-v">
                {vf.hook.type ? <b>{vf.hook.type}. </b> : null}
                {vf.hook.first_seconds}
              </span>
            </div>
          )}
          {vf.replicable_formula && (
            <div className="bp__row">
              <span className="bp__row-k">Formula</span>
              <span className="bp__row-v">{vf.replicable_formula}</span>
            </div>
          )}
          {Array.isArray(vf.retention_devices) && vf.retention_devices.length > 0 && (
            <div className="bp__row">
              <span className="bp__row-k">Retention</span>
              <span className="bp__row-v">
                {vf.retention_devices.map((device, i) => (
                  <span key={i} className="bp__inline-chip">
                    {typeof device === "string" ? device : renderDevice(device)}
                  </span>
                ))}
              </span>
            </div>
          )}
          {vf.tags && vf.tags.length > 0 && (
            <div className="bp__tags">
              {vf.tags.map((t) => (
                <span key={t} className="font-mono text-[10.5px] text-[var(--ink-faint)]">
                  #{t}
                </span>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* the audio sheet — shared with the studio ready-to-post */}
      {(bp.audio || bp.audio_strategy) && (
        <Section title="Sound" eyebrow="Attach manually">
          <AudioCard audio={bp.audio} strategy={bp.audio_strategy} compact />
        </Section>
      )}

      {/* shot list — the meat: per-shot generation + negative prompts */}
      {shots.length > 0 && (
        <Section title={`Shot list · ${shots.length}`} eyebrow="Copy-ready prompts">
          <div className="bp__shots">
            {shots.map((s) => (
              <ShotRow key={s.shot_index} shot={s} />
            ))}
          </div>
        </Section>
      )}

      {/* regeneration guide */}
      {rg && (
        <Section title="Regeneration guide" eyebrow="Assemble the clip">
          {rg.recommended_models && rg.recommended_models.length > 0 && (
            <div className="bp__chips mb-2">
              {rg.recommended_models.map((m) => (
                <Badge key={m} tone="brass">
                  {m}
                </Badge>
              ))}
            </div>
          )}
          {rg.master_style_prompt && (
            <PromptBlock label="Master style prompt" text={rg.master_style_prompt} />
          )}
          {rg.global_negative_prompt && (
            <PromptBlock label="Global negative prompt" text={rg.global_negative_prompt} negative />
          )}
          {rg.assembly_instructions && (
            <div className="bp__row">
              <span className="bp__row-k">Assembly</span>
              <span className="bp__row-v">{rg.assembly_instructions}</span>
            </div>
          )}
          {rg.consistency_notes && (
            <div className="bp__row">
              <span className="bp__row-k">Consistency</span>
              <span className="bp__row-v">{rg.consistency_notes}</span>
            </div>
          )}
        </Section>
      )}

      {/* character sheet */}
      {chars.length > 0 && (
        <Section title="Character sheet" eyebrow="Keep them consistent">
          <div className="bp__chars">
            {chars.map((c) => (
              <div key={c.id} className="bp__char">
                <div className="flex items-center justify-between">
                  <span className="font-display text-[14px]">{c.role}</span>
                  {c.appears_in_shots && c.appears_in_shots.length > 0 && (
                    <span className="eyebrow">shots {c.appears_in_shots.join(", ")}</span>
                  )}
                </div>
                <p className="bp__char-desc">{c.detailed_appearance}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* text-overlay timeline */}
      {overlays.length > 0 && (
        <Section title="Text overlays" eyebrow="On-screen copy · timed">
          <div className="bp__timeline">
            {overlays.map((o, i) => (
              <div key={i} className="bp__overlay">
                <span className="bp__overlay-time font-mono tnum">
                  {o.start_time.toFixed(1)}–{o.end_time.toFixed(1)}s
                </span>
                <span className="bp__overlay-text">{o.text}</span>
                {o.position && <span className="eyebrow">{o.position}</span>}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function ShotRow({ shot: s }: { shot: BlueprintShot }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cx("bp__shot", open && "bp__shot--open")}>
      <button className="bp__shot-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="bp__shot-idx font-mono tnum">{String(s.shot_index).padStart(2, "0")}</span>
        <span className="bp__shot-time font-mono tnum">
          {s.start_time.toFixed(1)}–{s.end_time.toFixed(1)}s
        </span>
        <span className="bp__shot-desc">{s.description ?? s.camera_shot_size ?? "shot"}</span>
        <IconChevron size={14} className={cx("bp__shot-chev", open && "bp__shot-chev--open")} />
      </button>
      {open && (
        <div className="bp__shot-body">
          <div className="bp__shot-facts">
            {fact("Camera", [s.camera_shot_size, s.camera_angle, s.camera_movement])}
            {fact("Lighting", [s.lighting])}
            {fact("Lens", [s.lens_feel])}
            {fact("Mood", [s.mood])}
            {s.on_screen_text && fact("On-screen", [s.on_screen_text])}
          </div>
          {s.color_palette_hex && s.color_palette_hex.length > 0 && (
            <div className="bp__swatches" title="Shot palette">
              {s.color_palette_hex.map((hex, i) => (
                <span key={i} className="bp__swatch" style={{ background: hex }} title={hex} />
              ))}
            </div>
          )}
          <PromptBlock label="Generation prompt" text={s.generation_prompt} />
          {s.negative_prompt && (
            <PromptBlock label="Negative prompt" text={s.negative_prompt} negative />
          )}
        </div>
      )}
    </div>
  );
}

function PromptBlock({
  label,
  text,
  negative,
}: {
  label: string;
  text: string;
  negative?: boolean;
}) {
  return (
    <div className={cx("prompt-block", negative && "prompt-block--neg")}>
      <div className="prompt-block__head">
        <span className="eyebrow">{label}</span>
        <CopyButton value={text} label="Copy" copied="Copied" />
      </div>
      <p className="prompt-block__text font-mono">{text}</p>
    </div>
  );
}

function Section({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
}) {
  return (
    <section className="bp__section">
      <div className="bp__section-head">
        <h4 className="font-display text-[15px]">{title}</h4>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
      </div>
      {children}
    </section>
  );
}

function fact(k: string, vals: (string | undefined)[]) {
  const v = vals.filter(Boolean).join(" · ");
  if (!v) return null;
  return (
    <div className="bp__fact">
      <span className="bp__fact-k">{k}</span>
      <span className="bp__fact-v">{v}</span>
    </div>
  );
}

function renderDevice(device: unknown): string {
  if (device && typeof device === "object") {
    const obj = device as Record<string, unknown>;
    return String(obj.device ?? obj.name ?? obj.type ?? JSON.stringify(obj));
  }
  return String(device);
}
