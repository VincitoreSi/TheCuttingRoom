import { useMemo, useState } from "react";
import { useShell } from "../App";
import { useAddReference, useProducers, useReferences, useStudio } from "../lib/hooks";
import { Badge, Button, Card, EmptyState, Eyebrow, Input, SectionHead } from "../components/ui";
import { IconChevron, IconProducers } from "../components/icons";
import { AgentConfigForm } from "../components/agent/AgentConfigForm";
import { SecretsPanel } from "../components/agent/SecretsPanel";
import { agoFrom } from "../lib/format";
import { useNow } from "../lib/hooks";
import { statusTone } from "../lib/statusTone";
import { humanizeAgent } from "../lib/agents";
import { cx } from "../lib/cx";
import type { Producer, Proposal } from "../lib/types";

export function ProducersView() {
  const producersQ = useProducers();
  const producers = producersQ.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <SectionHead
        eyebrow="Registry · self-registered on startup"
        title="The Floor"
        right={
          <Eyebrow>
            {producers.length} producer{producers.length === 1 ? "" : "s"}
          </Eyebrow>
        }
      />

      <p className="text-[13.5px] text-[var(--ink-dim)] max-w-2xl -mt-2">
        Every generation agent registers itself with the hub. New producers appear here
        automatically — that's pluggability made visible. Each card is tunable and reports its
        secret status without ever exposing a value.
      </p>

      {producersQ.isLoading ? (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 150 }} />
          ))}
        </div>
      ) : producers.length === 0 ? (
        <EmptyState
          icon={<IconProducers size={30} />}
          title="No producers registered"
          hint="Start a producer agent (e.g. similar-content) — on boot it POSTs its manifest to the hub and its lane shows up here."
        />
      ) : (
        <div className="flex flex-col gap-4">
          {producers.map((p) => (
            <ProducerCard key={p.name} producer={p} />
          ))}
        </div>
      )}

      <ReferenceIntake needsRef={producers.some((p) => p.needs_reference)} />
    </div>
  );
}

function ProducerCard({ producer: p }: { producer: Producer }) {
  const { platform, openAgent } = useShell();
  const studioQ = useStudio(platform);
  const now = useNow(30_000);
  const [panel, setPanel] = useState<null | "config" | "secrets">(null);

  const outputs = useMemo(
    () => (studioQ.data ?? []).filter((s) => s.agent === p.name).slice(0, 5),
    [studioQ.data, p.name],
  );

  const hasConfig =
    !!p.config_schema?.properties && Object.keys(p.config_schema.properties).length > 0;
  const missingSecret = (p.secrets ?? []).some((s) => s.required && s.present === false);

  return (
    <Card className="p-0 producer">
      <div className="producer__head">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="producer__name font-display">{humanizeAgent(p.name)}</span>
            {p.kind && <Badge tone="brass">{p.kind}</Badge>}
            {p.human_gate && <Badge tone="neutral">human gate</Badge>}
            {p.needs_reference && <Badge tone="oxblood">needs reference</Badge>}
            {missingSecret && <Badge tone="danger">secret missing</Badge>}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => openAgent(p.name)}
              aria-label={`Open board for ${p.name}`}
            >
              Open board →
            </Button>
          </div>
          <div className="producer__consumes">
            <span className="eyebrow">reads</span>
            {p.consumes.map((c) => (
              <span key={c} className="producer__chip font-mono">
                {c}
              </span>
            ))}
          </div>
        </div>
        <div className="producer__meta">
          <div className="eyebrow">produces</div>
          <div className="font-mono text-[12px] text-[var(--ink-2)]">{p.produces ?? "—"}</div>
          {p.output_status && <div className="eyebrow mt-1">→ {p.output_status}</div>}
        </div>
      </div>

      {/* recent outputs by this producer */}
      {outputs.length > 0 && (
        <div className="producer__outputs">
          <div className="eyebrow mb-2">recent outputs</div>
          {outputs.map((o) => (
            <OutputRow key={o.file} out={o} now={now} />
          ))}
        </div>
      )}

      {/* config + secrets live inside the card */}
      <div className="producer__foot">
        {hasConfig && (
          <button
            className={cx("producer__tab", panel === "config" && "producer__tab--active")}
            onClick={() => setPanel((v) => (v === "config" ? null : "config"))}
          >
            Config{" "}
            <IconChevron size={13} className={cx("chev", panel === "config" && "chev--open")} />
          </button>
        )}
        <button
          className={cx("producer__tab", panel === "secrets" && "producer__tab--active")}
          onClick={() => setPanel((v) => (v === "secrets" ? null : "secrets"))}
        >
          Secrets
          {(p.secrets?.length ?? 0) > 0 && (
            <span className={cx("producer__secret-dot", missingSecret ? "is-missing" : "is-ok")} />
          )}
        </button>
      </div>

      {panel === "config" && hasConfig && <AgentConfigForm agent={p.name} />}
      {panel === "secrets" && <SecretsPanel producer={p} />}
    </Card>
  );
}

function OutputRow({ out: o, now }: { out: Proposal; now: number }) {
  const title = o.text.match(/^#{1,3}\s+(.+)$/m)?.[1]?.trim() ?? o.file.replace(/\.md$/, "");
  const tone = statusTone("proposal", o.status);
  return (
    <div className="producer__output">
      <Badge tone={tone}>{o.status ?? "draft"}</Badge>
      <span className="truncate flex-1 min-w-0 text-[13px]">{title}</span>
      {o.created_at && (
        <span className="font-mono text-[10.5px] text-[var(--ink-faint)]">
          {agoFrom(new Date(o.created_at * 1000).toISOString(), now)}
        </span>
      )}
    </div>
  );
}

/* -------- reference / template intake (§2, §8) -------- */
function ReferenceIntake({ needsRef }: { needsRef: boolean }) {
  const { platform } = useShell();
  const refsQ = useReferences(platform);
  const add = useAddReference(platform);
  const now = useNow(30_000);
  const [url, setUrl] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const refs = refsQ.data ?? [];

  async function submit() {
    const u = url.trim();
    if (!u) return;
    setErr(null);
    try {
      await add.mutateAsync({ url: u });
      setUrl("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <Card className="p-5 reference">
      <SectionHead
        eyebrow="Template intake · only the template agent needs this"
        title="Reference Bench"
      />
      <p className="text-[13px] text-[var(--ink-dim)] mb-3 max-w-2xl">
        Paste a template/reference reel to model structure and style from. The hub downloads it and
        AnalysisEngine turns it into a blueprint — so a reference costs <b>one analysis pass</b>{" "}
        before the template agent can use it. It waits in <span className="font-mono">pending</span>{" "}
        until that pass finishes.
      </p>

      <div className="flex gap-2 max-w-xl">
        <Input
          placeholder="https://www.instagram.com/reel/…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          aria-label="Reference URL"
        />
        <Button variant="primary" onClick={submit} disabled={add.isPending || !url.trim()}>
          {add.isPending ? "Sending…" : "Add reference"}
        </Button>
      </div>
      {err && <p className="text-[12px] text-[var(--danger)] mt-2">{err}</p>}
      {!needsRef && (
        <p className="eyebrow mt-2">
          No registered producer consumes references yet — this feeds the template agent once it's
          running.
        </p>
      )}

      <div className="reference__list mt-4">
        {refsQ.isLoading ? (
          <div className="skeleton" style={{ height: 80 }} />
        ) : refs.length === 0 ? (
          <p className="eyebrow">No references submitted yet.</p>
        ) : (
          refs.map((r, i) => {
            const id = r.ref_id ?? r.content_id ?? r.id ?? `ref-${i}`;
            const ready = r.analyzed === true || (r.status ?? "").toLowerCase() === "analyzed";
            return (
              <div key={id} className="reference__item">
                <Badge tone={ready ? "sage" : "neutral"}>
                  {ready ? "blueprint ready" : (r.status ?? "pending")}
                </Badge>
                <span className="font-mono text-[11px] text-[var(--ink-2)] truncate flex-1 min-w-0">
                  {r.url ?? String(id)}
                </span>
                {!ready && <span className="eyebrow">one analysis pass</span>}
                {r.created_at && (
                  <span className="font-mono text-[10.5px] text-[var(--ink-faint)]">
                    {agoFrom(new Date(r.created_at * 1000).toISOString(), now)}
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </Card>
  );
}
