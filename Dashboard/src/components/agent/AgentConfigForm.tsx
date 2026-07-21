import { useEffect, useState, type ReactNode } from "react";
import { useAgentConfig, useSaveAgentConfig } from "../../lib/hooks";
import { Button, Input, Select, Switch } from "../ui";
import { IconCheck } from "../icons";
import { ConfigField } from "../config/ConfigField";
import type { JSONSchemaProp } from "../../lib/types";

/* -------- schema-driven config form (§10.3) --------
   Extracted verbatim from ProducersView.tsx (Task 7 Step 1) so AgentBoardView
   can share the same panel — same props, same behavior. */
export function AgentConfigForm({ agent }: { agent: string }) {
  const cfgQ = useAgentConfig(agent);
  const save = useSaveAgentConfig(agent);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (cfgQ.data) setValues({ ...cfgQ.data.defaults, ...cfgQ.data.config });
  }, [cfgQ.data]);

  if (cfgQ.isLoading) return <div className="skeleton" style={{ height: 120, margin: 16 }} />;
  if (!cfgQ.data) return <div className="producer__panel eyebrow">No config available.</div>;

  const props = cfgQ.data.config_schema?.properties ?? {};
  const keys = Object.keys(props);

  function set(k: string, v: unknown) {
    setValues((prev) => ({ ...prev, [k]: v }));
    setSaved(false);
  }
  async function onSave() {
    await save.mutateAsync(values);
    setSaved(true);
    setTimeout(() => setSaved(false), 2200);
  }

  return (
    <div className="producer__panel">
      <div className="config-grid">
        {keys.map((k) => (
          <Field key={k} name={k} schema={props[k]} value={values[k]} onChange={(v) => set(k, v)} />
        ))}
      </div>
      <div className="flex items-center gap-3 mt-3">
        <Button variant="primary" onClick={onSave} disabled={save.isPending}>
          {saved ? (
            <>
              <IconCheck size={15} /> Saved
            </>
          ) : save.isPending ? (
            "Saving…"
          ) : (
            "Save config"
          )}
        </Button>
        <span className="eyebrow">stored on the hub · read at the agent's next run</span>
      </div>
    </div>
  );
}

function Field({
  name,
  schema,
  value,
  onChange,
}: {
  name: string;
  schema: JSONSchemaProp;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = schema.title ?? name.replace(/_/g, " ");
  const type = schema.type;

  let control: ReactNode;
  if (schema.enum && schema.enum.length > 0) {
    control = (
      <Select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
      >
        {schema.enum.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </Select>
    );
  } else if (type === "boolean") {
    control = <Switch checked={!!value} onChange={onChange} label={label} />;
  } else if (type === "integer" || type === "number") {
    control = (
      <Input
        type="number"
        value={value == null ? "" : String(value)}
        min={schema.minimum}
        max={schema.maximum}
        step={type === "integer" ? 1 : "any"}
        onChange={(e) =>
          onChange(type === "integer" ? parseInt(e.target.value, 10) : parseFloat(e.target.value))
        }
        aria-label={label}
      />
    );
  } else {
    control = (
      <Input
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
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
