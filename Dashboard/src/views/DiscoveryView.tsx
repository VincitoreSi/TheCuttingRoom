import { useMemo } from "react";
import { motion } from "framer-motion";
import { useShell } from "../App";
import {
  useAgentConfig,
  useAgents,
  useCandidates,
  usePendingCandidates,
  useReducedMotion,
  useSaveAgentConfig,
  useSetCandidateStatus,
} from "../lib/hooks";
import { Badge, Button, Card, EmptyState, Eyebrow, SectionHead } from "../components/ui";
import { TapeGauge } from "../components/gauges";
import { IconCheck, IconDiscover, IconExternal, IconPin, IconX } from "../components/icons";
import { compact } from "../lib/format";
import { statusTone } from "../lib/statusTone";
import { safeUrl } from "../lib/url";
import { sectionMotion } from "../lib/motion";
import { cx } from "../lib/cx";
import type { Candidate } from "../lib/types";
import type { ViewKey } from "../components/Sidebar";

/* gate-status semantics (§9.6/§11.4), read off the shared statusTone helper:
   pending = neutral, approved = the signature sage, rejected = muted — the
   same tri-state Studio uses, so a rejected candidate and a rejected
   proposal read the same way across the app. */
function gateClass(status: string): string {
  if (status === "approved") return "gate--approved";
  if (status === "rejected") return "gate--rejected";
  return "gate--proposed";
}

/* the hub hydrates `handle` as the full profile URL — display it the same way
   the Config watchlist already does (§ConfigView pages-list). */
function handleName(h: string): string {
  return h.replace(/^https?:\/\/(www\.)?instagram\.com\//, "@").replace(/\/+$/, "");
}
function handleUrl(h: string): string {
  return /^https?:\/\//.test(h) ? h : `https://www.instagram.com/${h.replace(/^@/, "")}`;
}
/* relevance tiers reuse the app's score→color truth (evalModel scoreColor):
   sage = strong, amber = middling, danger = weak. oxblood is reserved for the
   live working-thread state, so it must not double as a static quality tier. */
function relevanceColor(score: number): string {
  if (score >= 0.75) return "var(--sage)";
  if (score >= 0.5) return "var(--amber)";
  return "var(--danger)";
}

export function DiscoveryView({ onNavigate }: { onNavigate?: (v: ViewKey) => void }) {
  const { platform } = useShell();
  const pendingQ = usePendingCandidates(platform);
  const approvedQ = useCandidates(platform, "approved");
  const setStatus = useSetCandidateStatus(platform);
  const reduced = useReducedMotion();

  const pending = pendingQ.data ?? [];
  const approved = useMemo(
    () => (approvedQ.data ?? []).slice().sort((a, b) => b.updated_at - a.updated_at),
    [approvedQ.data],
  );

  return (
    <div className="flex flex-col gap-6">
      <SectionHead
        eyebrow={`${platform} · discovery/${platform}`}
        title="The Scouting Bench"
        right={
          <Eyebrow>
            {pending.length} pending · {approved.length} approved
          </Eyebrow>
        }
      />

      <CadencePanel onNavigate={onNavigate} />

      {/* the human gate is the point of this view, same pattern as Studio */}
      {pendingQ.isLoading ? (
        <div className="candidate-grid">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 260 }} />
          ))}
        </div>
      ) : pending.length === 0 ? (
        <EmptyState
          icon={<IconDiscover size={28} />}
          title="Nothing pinned for review"
          hint="AutoSearch scouts Instagram on a slow, randomized cadence and pins what it finds here for a decision. Run Discover from the Board, or wait for the next heartbeat."
        />
      ) : (
        <div className="candidate-grid">
          {pending.map((c, i) => (
            <CandidateCard
              key={c.candidate_id}
              candidate={c}
              index={i}
              reduced={reduced}
              busy={setStatus.isPending}
              onDecide={(status) => setStatus.mutate({ candidateId: c.candidate_id, status })}
            />
          ))}
        </div>
      )}

      {/* Recently approved — cleared the gate, the hub already appended the
          handle to pages.txt so the next Scrape run picks it up. */}
      {approved.length > 0 && (
        <Card className="p-5 ready">
          <SectionHead
            eyebrow="Human-gated · appended to pages.txt"
            title="Recently approved (now scrapable)"
            right={<Badge tone="sage">{approved.length} pinned</Badge>}
          />
          <div className="ready__grid">
            {approved.slice(0, 8).map((c) => (
              <ApprovedRow
                key={c.candidate_id}
                candidate={c}
                busy={setStatus.isPending}
                onUnapprove={() =>
                  setStatus.mutate({ candidateId: c.candidate_id, status: "pending" })
                }
              />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/* Cadence panel (§11.1): reads the AutoSearch config generically — the same
   `GET /api/config/agent/auto-search` the schema-driven ProducersView form
   edits — and surfaces the kill-switch + weekly plan without duplicating the
   full form here. */
function CadencePanel({ onNavigate }: { onNavigate?: (v: ViewKey) => void }) {
  void onNavigate;
  const { openAgent } = useShell();
  const cfgQ = useAgentConfig("auto-search");
  const cfg = cfgQ.data?.config;
  const save = useSaveAgentConfig("auto-search");
  const agent = useAgents().data?.find((a) => a.name === "auto-search");

  /* The key note is a CAPABILITY line, never a warning. Discovery runs keyword-only without
     a key and the hub marks the stage unconditionally ready; a red "missing key" here would
     re-introduce a bug this repo already shipped and rolled back once. See the secrets
     comment in AutoSearch/cli.py. */
  const hasGemini = (agent?.secrets ?? []).some((s) => s.env_var === "GEMINI_API_KEY" && s.present);
  /* Term expansion is the SPEND SWITCH, separate from the discovery kill-switch: one Gemini
     call per run to widen the search terms. ./init and ./cr keys --set now write the key to
     AutoSearch/.env too, so it is sitting here ready and deliberately unused until opted in
     (AutoSearch/cli.py:272 returns early while this is false). */
  const expansionOn = (cfg as Record<string, unknown> | undefined)?.term_expansion_enabled === true;
  const keyNote = !hasGemini
    ? "Running keyword-only. Adding GEMINI_API_KEY (optional) lets it widen search terms; discovery works fully without one."
    : expansionOn
      ? "Term expansion is on — each run spends one Gemini call to widen its search terms."
      : "GEMINI_API_KEY is set but unused here: term expansion is off, so discovery spends nothing. Opt in below to widen search terms.";

  /* NOT Config. The full cadence form (weekly budget, active hours, and discovery_enabled
     itself) is AgentConfigForm, which renders on the agent board — Config only carries keys
     and model pickers. The old copy here sent people to "Config (The Bench)", where no such
     control exists. */
  /* PUT the FULL merged config with one flag flipped, mirroring AgentConfigForm's semantics —
     a partial PUT would drop every other knob.

     TAKES THE VALUE. This used to hardcode `true`, and the two buttons below rendered only
     while their flag was false — so each was a one-way latch: the panel that offered
     "Use Gemini to widen terms" had nothing to click once you had. The switch-off did exist,
     as a <Switch> in AgentConfigForm behind "Budget & cadence", but a spend switch you can
     arm here and can only disarm somewhere else is a trap — and it is worse for THIS pair
     than for most, because one of them is the kill-switch and the other is the only control
     in the product that starts spending money on a schedule. */
  function setFlag(key: string, value: boolean) {
    save.mutate({
      ...(cfgQ.data?.defaults ?? {}),
      ...(cfgQ.data?.config ?? {}),
      [key]: value,
    });
  }

  function openCadenceForm() {
    openAgent("auto-search");
  }

  if (cfgQ.isLoading) return <div className="skeleton cadence-panel" style={{ height: 84 }} />;

  if (!cfg) {
    return (
      <Card className="p-4 cadence-panel">
        <Eyebrow>Cadence · auto-search</Eyebrow>
        <p className="text-[13px] text-[var(--ink-dim)] mt-1">
          AutoSearch hasn't run yet, so its cadence plan is unknown. It publishes one the first time
          it starts:
        </p>
        <pre className="cadence-panel__cmd">./cr agent auto-search discover --dry-run</pre>
        <p className="text-[13px] text-[var(--ink-dim)]">
          On the native lane that is{" "}
          <span className="font-mono">
            cd AutoSearch &amp;&amp; uv run cli.py discover --dry-run
          </span>
          .
        </p>
        <p className="cadence-panel__keynote">{keyNote}</p>
      </Card>
    );
  }

  const enabled = cfg.discovery_enabled === true;
  const weekly = Number(cfg.weekly_search_budget ?? 0);
  const activeDays = Number(cfg.active_days_per_week ?? 0);
  const heartbeat = Number(cfg.heartbeat_minutes ?? 0);
  const hours = Array.isArray(cfg.active_hours) ? (cfg.active_hours as number[]) : null;

  return (
    <Card className={cx("p-4 cadence-panel", !enabled && "cadence-panel--off")}>
      <div className="cadence-panel__row">
        <div className="cadence-panel__switch">
          <Badge tone={enabled ? "sage" : "danger"}>
            {enabled ? "Discovery on" : "Discovery off"}
          </Badge>
          <span className="eyebrow">kill-switch · discovery_enabled</span>
        </div>
        <div className="cadence-panel__stats">
          <CadenceStat
            label="Weekly budget"
            value={`${weekly} ${weekly === 1 ? "unit" : "units"}`}
          />
          <CadenceStat label="Active days" value={`${activeDays}/7`} />
          <CadenceStat label="Heartbeat" value={`every ${heartbeat}m`} />
          {hours && hours.length === 2 && (
            <CadenceStat label="Window" value={`${hours[0]}:00–${hours[1]}:00`} />
          )}
        </div>
      </div>
      <p className="cadence-panel__posture">
        {enabled
          ? `Today's posture: scattered across ~${activeDays} of 7 days, thin-trickled through heartbeat ticks in the active window — a weekly budget, never a burst.`
          : "Today's posture: idle. The agent and the hub's heartbeat scheduler both fail closed while the kill-switch is off, so nothing is scouted and nothing is spent."}
      </p>

      <p className="cadence-panel__keynote">{keyNote}</p>

      {/* Both switches were previously named in prose only ("turn discovery on in Config"),
          with nothing to click — and Config was the wrong place besides. Each is one write to
          the same agent config AgentConfigForm edits, so offer them here and keep the full
          budget/window form one click away. */}
      <div className="cadence-panel__actions">
        {/* Both buttons stay MOUNTED once their flag is on, and say how to undo it. Rendering
            them only while the flag was false is what made each switch one-way. */}
        <Button
          variant={enabled ? "outline" : "primary"}
          size="sm"
          disabled={save.isPending}
          onClick={() => setFlag("discovery_enabled", !enabled)}
        >
          {save.isPending ? "Saving…" : enabled ? "Turn discovery off" : "Turn discovery on"}
        </Button>
        {/* Gated on hasGemini, not on the flag: without a key there is nothing to opt into.
            Still shown while ON so the spend can be stopped from where it was started. */}
        {hasGemini && (
          <Button
            variant="outline"
            size="sm"
            disabled={save.isPending}
            title={
              expansionOn
                ? "Stop spending a Gemini call per run; discovery stays on, keyword-only"
                : "Spends one Gemini call per run to widen the search terms"
            }
            onClick={() => setFlag("term_expansion_enabled", !expansionOn)}
          >
            {expansionOn ? "Stop widening with Gemini" : "Use Gemini to widen terms"}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={openCadenceForm}>
          Budget &amp; cadence
        </Button>
      </div>
    </Card>
  );
}

function CadenceStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="cadence-panel__stat">
      <span className="eyebrow">{label}</span>
      <span className="font-mono tnum text-[13px] text-[var(--ink)]">{value}</span>
    </div>
  );
}

function CandidateCard({
  candidate: c,
  index,
  reduced,
  busy,
  onDecide,
}: {
  candidate: Candidate;
  index: number;
  reduced: boolean;
  busy: boolean;
  onDecide: (status: string) => void;
}) {
  const name = handleName(c.handle);
  const initial = name.replace(/^@/, "").slice(0, 1).toUpperCase() || "?";
  const relScore = c.relevance?.score ?? 0;
  const reasons = c.relevance?.reasons ?? [];
  const samples = c.sample_reels ?? [];
  const pct = Math.round(relScore * 100);

  return (
    <motion.div className="h-full" {...sectionMotion(index, reduced)}>
      <Card className={cx("p-4 flex flex-col gap-3 gate candidate h-full", gateClass(c.status))}>
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="candidate__avatar font-display" aria-hidden="true">
              {initial}
            </span>
            <div className="min-w-0">
              <a
                href={handleUrl(c.handle)}
                target="_blank"
                rel="noreferrer"
                className="candidate__handle font-display chalk-underline inline-block truncate"
              >
                {name}
              </a>
              <div className="flex items-center gap-1.5 mt-0.5">
                <IconPin size={11} className="text-[var(--brass)]" />
                <span className="eyebrow">{c.discovered_via}</span>
              </div>
            </div>
          </div>
          <Badge tone={statusTone("discovery", c.status)}>{c.status}</Badge>
        </div>

        <div className="candidate__stats">
          <div className="candidate__stat">
            <span className="eyebrow">Followers</span>
            <span className="font-mono tnum">{compact(c.followers)}</span>
          </div>
          <div className="candidate__stat">
            <span className="eyebrow">Median plays</span>
            <span className="font-mono tnum">{compact(c.median_plays)}</span>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="eyebrow">Relevance</span>
            <span className="font-mono tnum text-[12px]">{pct}</span>
          </div>
          <TapeGauge
            value={pct}
            tierColor={relevanceColor(relScore)}
            height={6}
            showTicks={false}
          />
          {reasons.length > 0 && (
            <ul className="candidate__reasons">
              {reasons.slice(0, 3).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>

        {samples.length > 0 && (
          <div className="candidate__samples">
            <span className="eyebrow">Samples</span>
            <div className="flex flex-wrap gap-2 mt-1">
              {samples.slice(0, 3).map((url, i) => (
                <a
                  key={i}
                  href={safeUrl(url)}
                  target="_blank"
                  rel="noreferrer"
                  className="candidate__sample"
                  title={url}
                >
                  <IconExternal size={11} /> {i + 1}
                </a>
              ))}
            </div>
          </div>
        )}

        <div className="mt-auto">
          {c.in_pages ? (
            <Badge tone="sage">already in pages.txt</Badge>
          ) : (
            <span className="eyebrow">not yet in pages.txt</span>
          )}
        </div>

        {c.status === "pending" && (
          <div className="flex gap-2">
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
          </div>
        )}
      </Card>
    </motion.div>
  );
}

function ApprovedRow({
  candidate: c,
  busy,
  onUnapprove,
}: {
  candidate: Candidate;
  busy: boolean;
  onUnapprove: () => void;
}) {
  const name = handleName(c.handle);
  return (
    <div className="ready__item">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Eyebrow>
            {c.discovered_via}
            {c.source_term ? ` · "${c.source_term}"` : ""}
          </Eyebrow>
          <a
            href={handleUrl(c.handle)}
            target="_blank"
            rel="noreferrer"
            className="font-display text-[16px] chalk-underline inline-block truncate"
          >
            {name}
          </a>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onUnapprove}
          disabled={busy}
          title="Send back to pending"
        >
          Un-approve
        </Button>
      </div>
      <div className="flex items-center gap-3 text-[12.5px] text-[var(--ink-dim)] flex-wrap">
        <span className="font-mono tnum">{compact(c.followers)} followers</span>
        <span className="font-mono tnum">{compact(c.median_plays)} median plays</span>
        {c.in_pages ? (
          <Badge tone="sage">in pages.txt</Badge>
        ) : (
          <Badge tone="neutral">append pending</Badge>
        )}
      </div>
    </div>
  );
}
