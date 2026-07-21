import { Badge, Eyebrow } from "../ui";
import { cx } from "../../lib/cx";
import { humanizeAgent } from "../../lib/agents";
import type { Producer } from "../../lib/types";

/** The agent-desk header strip — same head markup as ProducerCard's
    producer__head (ProducersView.tsx), plus a live/idle status dot. */
export function AgentIdentity({ producer, live }: { producer: Producer; live: boolean }) {
  const missingSecret = (producer.secrets ?? []).some((s) => s.required && s.present === false);

  return (
    <div className="producer__head">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5 flex-wrap">
          <span
            className={cx("agent-dot", live && "agent-dot--live")}
            aria-hidden="true"
            title={live ? "live" : "idle"}
          />
          <span className="producer__name font-display">{humanizeAgent(producer.name)}</span>
          {producer.kind && <Badge tone="brass">{producer.kind}</Badge>}
          {producer.human_gate && <Badge tone="neutral">human gate</Badge>}
          {producer.needs_reference && <Badge tone="oxblood">needs reference</Badge>}
          {missingSecret && <Badge tone="danger">secret missing</Badge>}
        </div>
        <div className="producer__consumes">
          <Eyebrow>reads</Eyebrow>
          {producer.consumes.map((c) => (
            <span key={c} className="producer__chip font-mono">
              {c}
            </span>
          ))}
        </div>
      </div>
      <div className="producer__meta">
        <div className="eyebrow">produces</div>
        <div className="font-mono text-[12px] text-[var(--ink-2)]">{producer.produces ?? "—"}</div>
        {producer.output_status && <div className="eyebrow mt-1">→ {producer.output_status}</div>}
      </div>
    </div>
  );
}
