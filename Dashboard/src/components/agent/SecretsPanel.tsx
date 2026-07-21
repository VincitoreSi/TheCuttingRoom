import { cx } from "../../lib/cx";
import type { Producer } from "../../lib/types";

/* -------- secrets status (§10.4) — present/absent only, never values --------
   Extracted verbatim from ProducersView.tsx (Task 7 Step 1) — same props. */
export function SecretsPanel({ producer: p }: { producer: Producer }) {
  const secrets = p.secrets ?? [];
  return (
    <div className="producer__panel">
      {secrets.length === 0 ? (
        <span className="eyebrow">This agent declares no secrets.</span>
      ) : (
        <div className="flex flex-col gap-2">
          {secrets.map((s) => (
            <div key={s.name} className="secret-row">
              <span
                className={cx(
                  "secret-chip",
                  s.present
                    ? "secret-chip--ok"
                    : s.required
                      ? "secret-chip--missing"
                      : "secret-chip--opt",
                )}
              >
                {s.present ? "present" : "absent"}
              </span>
              <span className="secret-name font-mono">{s.name}</span>
              <span className="secret-env font-mono">${s.env_var}</span>
              {s.required && <span className="eyebrow">required</span>}
            </div>
          ))}
          <p className="eyebrow mt-1">
            Status only — secrets stay in each agent's local .env. The hub never stores a value.
          </p>
        </div>
      )}
    </div>
  );
}
