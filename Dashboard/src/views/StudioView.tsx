import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { useShell } from "../App";
import {
  useReducedMotion,
  useRenderProgress,
  useRenderStudioItem,
  useRenders,
  useSetStudioStatus,
  useStudio,
} from "../lib/hooks";
import { AudioCard } from "../components/AudioCard";
import { RenderSwatch } from "../components/RenderSwatch";
import { RenderModal } from "../components/RenderModal";
import { Badge, Button, Card, EmptyState, Eyebrow, SectionHead } from "../components/ui";
import { Markdown } from "../lib/markdown";
import { IconCheck, IconStudio, IconX } from "../components/icons";
import { statusTone } from "../lib/statusTone";
import { sectionMotion } from "../lib/motion";
import { humanizeAgent } from "../lib/agents";
import { extractSection, firstHeading } from "../lib/proposalMarkdown";
import { indexRenders, joinRenders } from "../lib/renderJoin";
import type { RenderRow } from "../lib/renderJoin";
import { cx } from "../lib/cx";
import type { FrameProgress } from "../lib/renderProgress";
import type { Jobs, Proposal } from "../lib/types";

/* gate-status rail (§9.6): proposed = brass, approved = signature sage,
   rejected = muted — the badge tone itself now comes from the shared
   statusTone helper; this only picks the .gate--* rail class. */
function gateClass(status?: string): string {
  if (status === "approved") return "gate--approved";
  if (status === "rejected") return "gate--rejected";
  return "gate--proposed";
}

type Tab = "proposals" | "renders";

export function StudioView() {
  const { platform, jobs } = useShell();
  const studioQ = useStudio(platform);
  const rendersQ = useRenders(platform);
  const setStatus = useSetStudioStatus(platform);
  const runRender = useRenderStudioItem(platform);
  const reduced = useReducedMotion();
  // live "frame n/total" per studio file, folded off the shared log SSE channel
  const progress = useRenderProgress(platform, jobs);
  const [tab, setTab] = useState<Tab>("proposals");
  const [open, setOpen] = useState<Proposal | null>(null);

  const proposals = useMemo(() => studioQ.data ?? [], [studioQ.data]);
  const approved = useMemo(() => proposals.filter((p) => p.status === "approved"), [proposals]);

  // rendered-first, newest-first — the pure join owns the ordering rule
  const rows = useMemo(
    () => joinRenders(approved, indexRenders(rendersQ.data ?? [])),
    [approved, rendersQ.data],
  );
  const renderedCount = rows.filter((r) => r.render).length;

  // group by producing agent so the studio reads as lanes of work
  const byAgent = useMemo(() => {
    const m = new Map<string, Proposal[]>();
    for (const p of proposals) {
      const a = p.agent || "unattributed";
      (m.get(a) ?? m.set(a, []).get(a)!).push(p);
    }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [proposals]);

  return (
    <div className="flex flex-col gap-6">
      <SectionHead
        eyebrow={`${platform} · studio/${platform}`}
        title="The Studio"
        right={
          tab === "renders" ? (
            <Eyebrow>
              {renderedCount} rendered · {approved.length - renderedCount} awaiting
            </Eyebrow>
          ) : (
            <Eyebrow>
              {proposals.length} proposal{proposals.length === 1 ? "" : "s"} · {approved.length}{" "}
              approved
            </Eyebrow>
          )
        }
      />

      <div className="segmented" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "proposals"}
          className={cx("segmented__btn", tab === "proposals" && "segmented__btn--active")}
          onClick={() => setTab("proposals")}
        >
          Proposals
        </button>
        <button
          role="tab"
          aria-selected={tab === "renders"}
          className={cx("segmented__btn", tab === "renders" && "segmented__btn--active")}
          onClick={() => setTab("renders")}
        >
          Renders
        </button>
      </div>

      {tab === "renders" ? (
        <RendersTab
          rows={rows}
          platform={platform}
          jobs={jobs}
          progress={progress}
          loading={rendersQ.isLoading || studioQ.isLoading}
          busy={runRender.isPending}
          onRender={(file, force) => runRender.mutate({ file, force })}
          onUnapprove={(file) => setStatus.mutate({ file, status: "proposed" })}
        />
      ) : /* the human gate is the point of this view */
      studioQ.isLoading ? (
        <div className="proposal-grid">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 220 }} />
          ))}
        </div>
      ) : proposals.length === 0 ? (
        <EmptyState
          icon={<IconStudio size={30} />}
          title="The table is clear"
          hint="No proposals yet. A producer agent reads the corpus through the hub and writes drafts here — then you approve the one to render."
        />
      ) : (
        <div className="flex flex-col gap-6">
          {byAgent.map(([agent, items], i) => (
            <motion.div key={agent} {...sectionMotion(i, reduced)}>
              <div className="studio-lane__head">
                <span className="studio-lane__agent font-display">{humanizeAgent(agent)}</span>
                <span className="studio-lane__count eyebrow">
                  {items[0]?.kind ? `${items[0].kind} · ` : ""}
                  {items.length} item{items.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="proposal-grid">
                {items.map((p) => (
                  <ProposalCard
                    key={p.file}
                    proposal={p}
                    onOpen={() => setOpen(p)}
                    onDecide={(status) => setStatus.mutate({ file: p.file, status })}
                    busy={setStatus.isPending}
                  />
                ))}
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {open && (
        <ProposalModal
          proposal={open}
          onClose={() => setOpen(null)}
          onDecide={(status) => {
            setStatus.mutate({ file: open.file, status });
            setOpen(null);
          }}
        />
      )}
    </div>
  );
}

function ProposalCard({
  proposal,
  onOpen,
  onDecide,
  busy,
}: {
  proposal: Proposal;
  onOpen: () => void;
  onDecide: (status: string) => void;
  busy: boolean;
}) {
  const title = firstHeading(proposal.text) ?? proposal.file.replace(/\.md$/, "");
  const preview = proposal.text
    .replace(/[#*`>-]/g, "")
    .split("\n")
    .filter(Boolean)
    .slice(0, 3)
    .join(" ");
  const status = proposal.status ?? "draft";
  return (
    <Card className={cx("p-5 flex flex-col gap-3 gate", gateClass(status))}>
      <div className="flex items-center justify-between gap-2">
        <Badge tone={statusTone("proposal", status)}>{status}</Badge>
        <span className="font-mono text-[10.5px] text-[var(--ink-faint)] truncate min-w-0">
          {proposal.file}
        </span>
      </div>
      <button className="text-left" onClick={onOpen}>
        <h3 className="font-display text-[17px] leading-tight chalk-underline inline break-words">
          {title}
        </h3>
        <p className="text-[13px] text-[var(--ink-dim)] line-clamp-3 mt-1.5">
          {preview.length > 180 ? `${preview.slice(0, 180)}…` : preview}
        </p>
      </button>
      <div className="mt-auto pt-1 flex gap-2">
        {status === "approved" ? (
          <Button variant="outline" className="flex-1" onClick={onOpen}>
            Open
          </Button>
        ) : (
          <>
            <Button
              variant="danger"
              className="flex-1"
              disabled={busy}
              onClick={() => onDecide("rejected")}
            >
              <IconX size={14} /> Reject
            </Button>
            <Button
              variant="sage"
              className="flex-1"
              disabled={busy}
              onClick={() => onDecide("approved")}
            >
              <IconCheck size={14} /> Approve
            </Button>
          </>
        )}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------ Renders tab
   A grid of compact swatches, one per APPROVED item, in the Corpus card's
   visual language; clicking a swatch's media opens the detail modal, which
   holds everything needed to post the reel to Instagram by hand. The live
   render job for an item is keyed `${platform}:render:${file}` by the hub — a
   single map read, no scan.

   Not virtualized: the rows are single-digit, and VirtualReelGrid's row math
   (`cardW * 4/3 + 92`) assumes the Corpus 3:4 card, not a 9:16 tower. */
function RendersTab({
  rows,
  platform,
  jobs,
  progress,
  loading,
  busy,
  onRender,
  onUnapprove,
}: {
  rows: RenderRow[];
  platform: string;
  jobs: Jobs;
  /** studio file -> live frame count, empty unless something is rendering */
  progress: Map<string, FrameProgress>;
  loading: boolean;
  busy: boolean;
  onRender: (file: string, force: boolean) => void;
  onUnapprove: (file: string) => void;
}) {
  const [detail, setDetail] = useState<string | null>(null);
  const openRow = rows.find((r) => r.proposal.file === detail);

  if (loading)
    return (
      <div className="render-grid">
        {Array.from({ length: 3 }).map((_, i) => (
          // media (9/16) plus the meta band beneath it
          <div key={i} className="skeleton" style={{ aspectRatio: "9 / 21" }} />
        ))}
      </div>
    );

  if (rows.length === 0)
    return (
      <EmptyState
        icon={<IconStudio size={30} />}
        title="Nothing cleared the gate yet"
        hint="Approve a proposal and it becomes renderable here."
      />
    );

  return (
    <>
      <div className="render-grid">
        {rows.map(({ proposal, render }) => (
          <RenderSwatch
            key={proposal.file}
            proposal={proposal}
            render={render}
            job={jobs[`${platform}:render:${proposal.file}`]}
            progress={progress.get(proposal.file)}
            busy={busy}
            onRender={(force) => onRender(proposal.file, force)}
            onSelect={() => setDetail(proposal.file)}
          />
        ))}
      </div>

      {openRow && (
        <RenderModal
          proposal={openRow.proposal}
          render={openRow.render}
          job={jobs[`${platform}:render:${openRow.proposal.file}`]}
          progress={progress.get(openRow.proposal.file)}
          busy={busy}
          onClose={() => setDetail(null)}
          onRender={(force) => onRender(openRow.proposal.file, force)}
          onUnapprove={() => {
            onUnapprove(openRow.proposal.file);
            setDetail(null);
          }}
        />
      )}
    </>
  );
}

function ProposalModal({
  proposal,
  onClose,
  onDecide,
}: {
  proposal: Proposal;
  onClose: () => void;
  onDecide: (status: string) => void;
}) {
  const status = proposal.status ?? "draft";
  const audioBlock = extractSection(proposal.text, "Audio");
  const title = firstHeading(proposal.text) ?? proposal.file.replace(/\.md$/, "");

  // dismiss on Escape and lock the background scroll, matching the ReelModal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div
      className="modal-scrim"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Proposal: ${title}`}
    >
      <div
        className="modal"
        style={{ gridTemplateColumns: "1fr", width: "min(760px,100%)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal__body">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              {proposal.agent && (
                <Eyebrow>
                  {proposal.agent}
                  {proposal.kind ? ` · ${proposal.kind}` : ""}
                </Eyebrow>
              )}
              <div className="font-mono text-[11px] text-[var(--ink-faint)] truncate">
                {proposal.file}
              </div>
            </div>
            <Badge tone={statusTone("proposal", status)}>{status}</Badge>
          </div>
          <div style={{ maxHeight: "46vh", overflowY: "auto" }}>
            <Markdown text={proposal.text} />
          </div>
          {audioBlock && <AudioCard rawMarkdown={audioBlock} compact />}
          <div className="flex gap-3 pt-2 border-t border-[var(--line)]">
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <div className="flex-1" />
            {status !== "rejected" && (
              <Button variant="danger" onClick={() => onDecide("rejected")}>
                <IconX size={15} /> Reject
              </Button>
            )}
            {status !== "approved" && (
              <Button variant="sage" onClick={() => onDecide("approved")}>
                <IconCheck size={15} /> Approve
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
