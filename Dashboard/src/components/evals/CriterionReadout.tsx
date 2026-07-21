import { criterionEntries, scoreColor } from "../../lib/evalModel";
import { TapeGauge } from "../gauges";
import type { EvalRecord } from "../../lib/types";

/**
 * Per-criterion readout as a stack of TapeGauges — the eval-log analogue of
 * BlueprintPanel's `.bp__criterion` rows (same class names, same reading:
 * key on the left, value on the right), so a blueprint's own drawer and this
 * tab's drill-down render criteria identically. Reads whatever rubric keys
 * are present; a record with no per-criterion detail (the smoke overall-only
 * eval) renders nothing rather than fabricating rows.
 */
export function CriterionReadout({ scores }: { scores: EvalRecord["scores"] }) {
  const entries = criterionEntries({ scores });
  if (!entries.length) return null;
  return (
    <div className="criterion-readout">
      {entries.map(([label, v]) => (
        <div key={label} className="bp__criterion criterion-readout__row">
          <span className="bp__criterion-k">{label}</span>
          <div className="criterion-readout__gauge">
            <TapeGauge value={v} tierColor={scoreColor(v)} height={6} showTicks={false} />
          </div>
          <span className="bp__criterion-v font-mono tnum" style={{ color: scoreColor(v) }}>
            {v}
          </span>
        </div>
      ))}
    </div>
  );
}
