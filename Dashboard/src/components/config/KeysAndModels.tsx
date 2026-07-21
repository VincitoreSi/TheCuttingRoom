import { useEffect, useState, type ReactNode } from "react";
import { useAgentConfig, useProducers, useSaveAgentConfig } from "../../lib/hooks";
import { Badge, Card, EmptyState, Eyebrow, Input, SectionHead, Select } from "../ui";
import { IconCheck, IconProducers } from "../icons";
import { cx } from "../../lib/cx";
import { ConfigField } from "./ConfigField";
import { humanizeAgent } from "../../lib/agents";
import type { JSONSchemaProp, Producer, SecretStatus } from "../../lib/types";

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
  const producersQ = useProducers();
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
          hint="Agents self-register with the hub on startup. Start one (e.g. analysis-engine) and its keys & model controls appear here automatically."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {producers.map((p) => (
            <AgentKeysModels key={p.name} producer={p} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AgentKeysModels({ producer: p }: { producer: Producer }) {
  const cfgQ = useAgentConfig(p.name);
  const save = useSaveAgentConfig(p.name);
  // Full merged config (defaults <- stored), matching AgentConfigForm's PUT
  // semantics: we persist the whole map, only ever mutating a model field.
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (cfgQ.data) setValues({ ...cfgQ.data.defaults, ...cfgQ.data.config });
  }, [cfgQ.data]);

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
        {saved && (
          <span className="km-saved">
            <IconCheck size={12} /> saved
          </span>
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
          {cfgQ.isLoading ? (
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
