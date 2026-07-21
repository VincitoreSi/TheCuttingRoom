import { useMemo, useState } from "react";
import { useShell } from "../App";
import { useAgentBoard, useContent, useProducers } from "../lib/hooks";
import { humanizeAgent } from "../lib/agents";
import { AgentConfigForm } from "../components/agent/AgentConfigForm";
import { SecretsPanel } from "../components/agent/SecretsPanel";
import { AgentIdentity } from "../components/agent/AgentIdentity";
import { RunGroup } from "../components/agent/RunGroup";
import { ReelModal } from "../components/ReelModal";
import { Card, EmptyState, SectionHead } from "../components/ui";
import { SeamStatus } from "../components/Seam";
import { IconProducers } from "../components/icons";

/** The per-agent desk: identity + tunable config/secrets up top, then this
    agent's runs as nested lane boards, live off the SSE `log` channel via
    useAgentBoard. Nav wiring (Sidebar/App/Dashboard strip) is Task 8 — this
    view just needs `name` and to compile/render standalone. */
export function AgentBoardView({ name }: { name: string }) {
  const { platform } = useShell();
  const producersQ = useProducers();
  const producer = useMemo(
    () => (producersQ.data ?? []).find((p) => p.name === name),
    [producersQ.data, name],
  );

  const { board, connected, isLoading } = useAgentBoard(name, platform);

  // Analyzer items open the existing ReelModal on its Blueprint tab (same
  // modal-open mechanism as Corpus.tsx: a selected-content-id state). A
  // producer item's studio-gate destination is Task 8's cross-view nav; for
  // now it's a no-op click here.
  const contentQ = useContent(platform);
  const [openId, setOpenId] = useState<string | null>(null);
  const openReel = useMemo(
    () => (contentQ.data ?? []).find((r) => r.content_id === openId) ?? null,
    [contentQ.data, openId],
  );

  const isAnalyzer = (producer?.kind ?? board?.kind) === "analyzer";
  function onOpenItem(id: string) {
    if (isAnalyzer) setOpenId(id);
  }

  const hasConfig =
    !!producer?.config_schema?.properties &&
    Object.keys(producer.config_schema.properties).length > 0;
  const runs = board?.runs ?? [];
  const liveRuns = runs.filter((r) => r.ended == null);
  const pastRuns = runs.filter((r) => r.ended != null);
  const orderedRuns = [...liveRuns, ...pastRuns];

  return (
    <div className="flex flex-col gap-6">
      <SectionHead
        eyebrow={`Agent desk · ${name}`}
        title={humanizeAgent(name)}
        right={
          <SeamStatus
            state={connected ? "working" : "idle"}
            label={connected ? "live" : "reconnecting…"}
            flowOn={connected}
          />
        }
      />

      {producer ? (
        <Card className="p-0 producer">
          <AgentIdentity producer={producer} live={liveRuns.length > 0} />
          {hasConfig && <AgentConfigForm agent={producer.name} />}
          <SecretsPanel producer={producer} />
        </Card>
      ) : producersQ.isLoading ? (
        <div className="skeleton" style={{ height: 130 }} />
      ) : null}

      {isLoading ? (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 160 }} />
          ))}
        </div>
      ) : orderedRuns.length === 0 ? (
        <EmptyState
          icon={<IconProducers size={28} />}
          title="No runs yet"
          hint="This agent hasn't done work — its board fills in the moment it starts a run."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {orderedRuns.map((run) => (
            <RunGroup
              key={run.run_id}
              run={run}
              stages={board!.workflow_stages}
              live={run.ended == null}
              onOpenItem={onOpenItem}
              agent={board!.agent}
              kind={board!.kind}
            />
          ))}
        </div>
      )}

      {openReel && (
        <ReelModal reel={openReel} onClose={() => setOpenId(null)} initialTab="blueprint" />
      )}
    </div>
  );
}
