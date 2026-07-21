import { useState } from "react";
import { Badge, Button, Tooltip } from "../ui";
import { TapeGauge } from "../gauges";
import { Seam } from "../Seam";
import { IconExternal } from "../icons";
import { useReRunEval } from "../../lib/hooks";
import { statusTone } from "../../lib/statusTone";
import { criterionEntries, recordScore, scoreColor } from "../../lib/evalModel";
import { cx } from "../../lib/cx";
import type { EvalRecord } from "../../lib/types";
import type { ViewKey } from "../Sidebar";

const BLUEPRINT_TYPES = new Set(["blueprint", "reference_blueprint"]);

/** Parse the clone eval's cross-reference note ("clone fidelity to blueprint
    <id>") back into a source blueprint id — no structured field carries this
    today; the note IS the thread back to the source. */
function sourceBlueprintId(notes: string | null | undefined): string | null {
  if (!notes) return null;
  const m = notes.match(/blueprint\s+(\S+)\s*$/i);
  return m ? m[1] : null;
}

/**
 * The Fix Queue's row — the eval-log analogue of BlueprintPanel's readout
 * (same big score number, same TapeGauge, same `.bp__criterion` chips) so
 * the two finally speak one language. Actions are target_type-aware and
 * wired to REAL routes only: a blueprint gets the existing whole-stage
 * re-run; a clone (no re-score endpoint exists) degrades to opening its
 * source; every row can jump to Corpus. No invented routes, no dead ends.
 */
export function FixCard({
  rec,
  onNavigate,
  registerRef,
  flashed,
}: {
  rec: EvalRecord;
  onNavigate: (v: ViewKey) => void;
  registerRef?: (targetId: string, el: HTMLDivElement | null) => void;
  flashed?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const score = recordScore(rec) ?? 0;
  const entries = criterionEntries(rec).sort((a, b) => a[1] - b[1]); // weakest first
  const visible = expanded ? entries : entries.slice(0, 3);
  const rest = entries.length - visible.length;

  const rerun = useReRunEval(rec.platform);
  const isBlueprint = BLUEPRINT_TYPES.has(rec.target_type);
  const sourceId = rec.target_type === "clone" ? sourceBlueprintId(rec.notes) : null;
  const working = rerun.isPending;

  return (
    <div
      ref={(el) => registerRef?.(rec.target_id, el)}
      className={cx("fix-card", working && "fix-card--working", flashed && "data-flash")}
    >
      <span
        className="fix-card__rule"
        style={{ background: scoreColor(score) }}
        aria-hidden="true"
      />
      <div className="fix-card__body">
        <div className="fix-card__head">
          <div className="fix-card__scorecol">
            <div className="fix-card__score font-display" style={{ color: scoreColor(score) }}>
              {score.toFixed(0)}
            </div>
            <TapeGauge value={score} tierColor={scoreColor(score)} height={7} />
          </div>
          <div className="fix-card__meta min-w-0">
            <div className="text-[13px] truncate">
              <span className="font-mono text-[var(--ink-dim)]">{rec.agent}</span>{" "}
              <span className="font-mono">{rec.target_type}</span>
            </div>
            <div
              className="font-mono text-[11px] text-[var(--ink-faint)] truncate"
              title={rec.target_id}
            >
              {rec.target_id}
            </div>
            <div className="flex gap-2 mt-1 flex-wrap">
              {rec.judge && <Badge tone="neutral">{rec.judge}</Badge>}
              {rec.verdict && (
                <Badge tone={statusTone("eval-verdict", rec.verdict)}>{rec.verdict}</Badge>
              )}
            </div>
          </div>
        </div>

        {entries.length > 0 && (
          <div className="fix-card__criteria">
            {visible.map(([label, v]) => (
              <span key={label} className="bp__criterion fix-card__criterion">
                <span className="bp__criterion-k">{label}</span>
                <span className="bp__criterion-v font-mono tnum" style={{ color: scoreColor(v) }}>
                  {v}
                </span>
              </span>
            ))}
            {rest > 0 && (
              <button
                type="button"
                className="fix-card__more"
                onClick={() => setExpanded(true)}
                aria-label={`Show ${rest} more criteria`}
              >
                +{rest} more
              </button>
            )}
          </div>
        )}

        {rec.notes && <p className="fix-card__notes font-mono">{rec.notes}</p>}

        <div className="fix-card__actions">
          {isBlueprint ? (
            rec.platform ? (
              <Button
                variant="primary"
                size="sm"
                disabled={working}
                onClick={() => rerun.mutate("analysis-engine")}
              >
                {working ? "Re-running…" : "Re-run analysis"}
              </Button>
            ) : (
              <Tooltip label="no platform on this record">
                <Button variant="primary" size="sm" disabled>
                  Re-run analysis
                </Button>
              </Tooltip>
            )
          ) : sourceId ? (
            <Button variant="outline" size="sm" onClick={() => onNavigate("corpus")}>
              Open source blueprint
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => onNavigate("corpus")}>
            Open <IconExternal size={13} />
          </Button>
        </div>
        {working && (
          <div className="fix-card__working">
            <Seam state="working" width={110} />
            <span className="eyebrow">re-running analysis-engine…</span>
          </div>
        )}
      </div>
    </div>
  );
}
