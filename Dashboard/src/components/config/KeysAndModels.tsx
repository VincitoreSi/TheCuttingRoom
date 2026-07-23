import { useEffect, useState, type ReactNode } from "react";
import { useAgentConfig, useAgents, useRegisterAgent, useSaveAgentConfig } from "../../lib/hooks";
import { useShell } from "../../App";
import { Badge, Button, Card, EmptyState, Eyebrow, Input, SectionHead, Select } from "../ui";
import { IconCheck, IconConfig, IconProducers } from "../icons";
import { cx } from "../../lib/cx";
import { ConfigField } from "./ConfigField";
import { AgentConfigModal } from "./AgentConfigModal";
import { humanizeAgent } from "../../lib/agents";
import type { AgentRosterEntry, JSONSchemaProp, SecretStatus } from "../../lib/types";

/* -------- Keys & models (unified agent config) --------
   One place for (a) each agent's API-key STATUS and (b) each agent's model
   selection. Keys are status-only — the hub never stores a value, so there is
   deliberately NO key-entry field here. Models are editable, sourced from each
   agent's own config_schema (they live in different model spaces, so there is
   no single global model string). */

// The model/provider-ish properties we surface from a config_schema. We only
// render THESE fields, not the whole schema — that lives in AgentConfigForm.
const MODEL_KEYS = ["model", "judge_model", "image_provider"] as const;

export function KeysAndModels() {
  /* The full roster, not just registered producers. Registration is lazy — an agent
     registers when its CLI first runs — so on a clean install this panel used to be empty
     and said nothing about the key that gates the Blueprint stage. */
  const producersQ = useAgents();
  const producers = producersQ.data ?? [];

  const missingCount = producers.filter((p) =>
    (p.secrets ?? []).some((s) => s.required && s.present === false),
  ).length;

  return (
    <Card className="p-5">
      <SectionHead
        eyebrow="Agents · keys & models"
        title="Keys & models"
        right={
          producers.length > 0 ? (
            missingCount > 0 ? (
              <Badge tone="danger">
                {missingCount} agent{missingCount === 1 ? "" : "s"} missing keys
              </Badge>
            ) : (
              <Badge tone="sage">all required keys present</Badge>
            )
          ) : undefined
        }
      />

      <p className="text-[13px] text-[var(--ink-dim)] mb-4 max-w-2xl">
        Every agent's API-key status and model live here. Keys are <b>status only</b> — they stay in
        each agent's local <span className="font-mono">.env</span>; the hub never sees a value.
        Models are editable and each agent uses its own model space, so sensible defaults already
        ship — a smooth start just means filling the missing keys below.
      </p>

      {producersQ.isLoading ? (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 120 }} />
          ))}
        </div>
      ) : producers.length === 0 ? (
        <EmptyState
          icon={<IconProducers size={28} />}
          title="No agents registered"
          hint="The hub could not resolve any agent directories. A full checkout ships AnalysisEngine, AutoSearch and SimilarContent alongside ReelScraper."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {producers.map((p) => (
            <AgentKeysModels key={p.name} agent={p} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AgentKeysModels({ agent: p }: { agent: AgentRosterEntry }) {
  const { configAgent, clearConfigAgent } = useShell();
  /* The hub returns config_schema for built-in agents even before registration
     (from KNOWN_AGENT_MANIFESTS), so we always fetch. For unknown/custom agents
     without a known manifest the endpoint returns null schema, same as before. */
  const cfgQ = useAgentConfig(p.name);
  const save = useSaveAgentConfig(p.name);
  const registerAgent = useRegisterAgent();
  // Full merged config (defaults <- stored), matching AgentConfigForm's PUT
  // semantics: we persist the whole map, only ever mutating a model field.
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);
  // Which agent's full-config modal is open (this row's name, or null).
  const [openFor, setOpenFor] = useState<string | null>(null);
  // Which agent's register dialog is shown (this row's name, or null).
  const [registerTarget, setRegisterTarget] = useState<string | null>(null);

  useEffect(() => {
    if (cfgQ.data) setValues({ ...cfgQ.data.defaults, ...cfgQ.data.config });
  }, [cfgQ.data]);

  // Cross-view nav: a board's Config button stashed a pending target and switched
  // here. If it names this agent, pop its modal once and clear the intent so a
  // later visit to Config does not re-open it. Guarded on registration — an
  // unregistered agent has no stored config to edit.
  useEffect(() => {
    if (configAgent !== p.name) return;
    clearConfigAgent();
    if (p.registered) setOpenFor(p.name);
  }, [configAgent, p.name, p.registered, clearConfigAgent]);

  const secrets = p.secrets ?? [];
  const props = cfgQ.data?.config_schema?.properties ?? {};
  const modelKeys = MODEL_KEYS.filter((k) => k in props);

  // Persist the FULL merged config with `k` set to `v` (mirrors AgentConfigForm).
  async function commit(k: string, v: unknown) {
    const next = { ...values, [k]: v };
    setValues(next);
    await save.mutateAsync(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 2200);
  }

  return (
    <div className="km-agent">
      <div className="km-agent__head">
        <span className="producer__name font-display">{humanizeAgent(p.name)}</span>
        {p.kind && <Badge tone="brass">{p.kind}</Badge>}
        {!p.registered && <Badge tone="neutral">not started yet</Badge>}
        {saved && (
          <span className="km-saved">
            <IconCheck size={12} /> saved
          </span>
        )}
        {/* Full-config editor lives in a modal. For unregistered agents the button
            opens a register dialog that lets the user register first, then configure. */}
        <Button
          variant="ghost"
          size="sm"
          style={{ marginLeft: "auto" }}
          onClick={() => (p.registered ? setOpenFor(p.name) : setRegisterTarget(p.name))}
          aria-label={`Configure ${p.name}`}
        >
          <IconConfig size={14} /> Configure
        </Button>
        {registerTarget === p.name && (
          <div className="modal-scrim fixed inset-0 z-50 flex items-center justify-center"
               onClick={() => setRegisterTarget(null)}>
            <div className="p-6 rounded-lg shadow-xl max-w-sm w-full"
                 style={{ background: "var(--surface)", border: "1px solid var(--line-strong)", borderRadius: "var(--r-lg)" }}
                 onClick={(e) => e.stopPropagation()}>
              <h3 className="font-display text-lg mb-2">{humanizeAgent(p.name)}</h3>
              <p className="text-sm mb-4" style={{ color: "var(--ink-2)" }}>
                This agent hasn't registered yet. Register it now to adjust its settings
                before the first run.
              </p>
              <div className="flex gap-2 justify-end">
                <Button variant="ghost" size="sm"
                        onClick={() => setRegisterTarget(null)}>Cancel</Button>
                <Button size="sm"
                        onClick={async () => {
                          await registerAgent.mutateAsync(p.name);
                          setRegisterTarget(null);
                          setOpenFor(p.name);
                        }}>Register {'\u0026'} Configure</Button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="km-agent__grid">
        {/* -------- API keys: status only, no value ever entered here -------- */}
        <div className="km-col">
          <Eyebrow className="mb-2">API keys · status only</Eyebrow>
          {secrets.length === 0 ? (
            <span className="eyebrow">This agent declares no keys.</span>
          ) : (
            <div className="flex flex-col gap-2">
              {secrets.map((s) => (
                <KeyRow key={s.name} secret={s} agent={p.name} />
              ))}
            </div>
          )}
        </div>

        {/* -------- Model / provider selectors (editable) -------- */}
        <div className="km-col">
          <Eyebrow className="mb-2">Model</Eyebrow>
          {!cfgQ.data?.config_schema ? (
            <span className="eyebrow">
              Known once this agent runs — it publishes its model options to the hub the first time
              it starts.
            </span>
          ) : cfgQ.isLoading ? (
            <div className="skeleton" style={{ height: 48 }} />
          ) : modelKeys.length === 0 ? (
            <span className="eyebrow">Uses its built-in default model.</span>
          ) : (
            <div className="flex flex-col gap-3">
              {modelKeys.map((k) => (
                <ModelField
                  key={k}
                  name={k}
                  schema={props[k]}
                  value={values[k]}
                  disabled={save.isPending}
                  onCommit={(v) => commit(k, v)}
                />
              ))}
            </div>
          )}
        </div>

      </div>

      {openFor && <AgentConfigModal agent={openFor} onClose={() => setOpenFor(null)} />}
    </div>
  );
}

function KeyRow({ secret: s, agent }: { secret: SecretStatus; agent: string }) {
  const state = s.present ? "ok" : s.required ? "missing" : "opt";
  const label = s.present ? "present" : s.required ? "missing" : "optional";
  return (
    <div className="secret-row">
      <span className={cx("secret-chip", `secret-chip--${state}`)}>{label}</span>
      <span className="secret-env font-mono">${s.env_var}</span>
      {!s.present && (
        <span className="eyebrow">
          set in <span className="font-mono">{agent}/.env</span>
        </span>
      )}
    </div>
  );
}

/* One model/provider control. enum → Select (options from schema.enum),
   otherwise a free-text Input. Selects commit on change; text commits on blur
   (so we don't PUT on every keystroke). Reuses AgentConfigForm's Field shape. */
function ModelField({
  name,
  schema,
  value,
  disabled,
  onCommit,
}: {
  name: string;
  schema: JSONSchemaProp;
  value: unknown;
  disabled?: boolean;
  onCommit: (v: unknown) => void;
}) {
  const label = schema.title ?? name.replace(/_/g, " ");
  const [draft, setDraft] = useState(value == null ? "" : String(value));

  useEffect(() => {
    setDraft(value == null ? "" : String(value));
  }, [value]);

  let control: ReactNode;
  if (schema.enum && schema.enum.length > 0) {
    control = (
      <Select
        value={String(value ?? "")}
        disabled={disabled}
        onChange={(e) => onCommit(e.target.value)}
        aria-label={label}
      >
        {schema.enum.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </Select>
    );
  } else {
    control = (
      <Input
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== String(value ?? "") && onCommit(draft)}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        aria-label={label}
      />
    );
  }

  return (
    <ConfigField label={label} description={schema.description}>
      {control}
    </ConfigField>
  );
}
