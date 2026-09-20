import type { ReactElement } from "react";

import type { Instrumentation } from "../contracts";
import type { MessageKey } from "../i18n";
import {
  INSTRUMENTATION_KINDS,
  INSTRUMENTATION_OUTCOMES,
  type InstrumentationHint,
  type InstrumentationKind,
  type InstrumentationOutcome
} from "../instrumentation.js";

export type InstrumentationSettingsProps = {
  /** Server truth, or `null` while it is being read or after a failed read. */
  instrumentation: Instrumentation | null;
  unavailable: boolean;
  busy: boolean;
  /** A read-only panel registers no instrumentation writes, so it offers none. */
  writesEnabled: boolean;
  hint: InstrumentationHint | null;
  clearAsked: boolean;
  onToggle: (enabled: boolean) => void;
  onAskClear: () => void;
  onCancelClear: () => void;
  onConfirmClear: () => void;
  text: (key: MessageKey) => string;
};

const hintMessage: Readonly<Record<InstrumentationHint, MessageKey>> = {
  stale: "instrumentation_hint_stale",
  rejected: "instrumentation_hint_rejected"
};

/** Totals per kind and outcome: the buckets are summed, never shown as timings. */
function total(instrumentation: Instrumentation, kind: InstrumentationKind, outcome: InstrumentationOutcome): number {
  const buckets = instrumentation.counters[kind]?.[outcome];
  if (buckets === undefined) return 0;
  return Object.values(buckets).reduce<number>((sum, count) => sum + (count ?? 0), 0);
}

export function InstrumentationSettings({
  instrumentation, unavailable, busy, writesEnabled, hint, clearAsked,
  onToggle, onAskClear, onCancelClear, onConfirmClear, text
}: InstrumentationSettingsProps): ReactElement {
  const controlsDisabled = !writesEnabled || busy || instrumentation === null;
  const counted = instrumentation === null
    ? []
    : INSTRUMENTATION_KINDS
      .map((kind) => ({ kind, totals: INSTRUMENTATION_OUTCOMES.map((outcome) => total(instrumentation, kind, outcome)) }))
      .filter((row) => row.totals.some((value) => value > 0));
  return (
    <section aria-labelledby="instrumentation-title" className="settings-panel instrumentation-panel">
      <h2 id="instrumentation-title">{text("instrumentation_title")}</h2>
      <p className="small-copy">{text("instrumentation_intro")}</p>
      {unavailable ? <p className="field-error" role="status">{text("instrumentation_unavailable")}</p> : null}
      <label className="instrumentation-toggle" htmlFor="instrumentation-enabled">
        <input
          aria-describedby="instrumentation-toggle-help"
          checked={instrumentation?.enabled === true}
          disabled={controlsDisabled}
          id="instrumentation-enabled"
          onChange={(event) => onToggle(event.currentTarget.checked)}
          type="checkbox"
        />
        <span>{text("instrumentation_toggle_label")}</span>
      </label>
      <p className="small-copy" id="instrumentation-toggle-help">{text("instrumentation_toggle_help")}</p>
      {hint !== null ? <p className="field-error" role="status">{text(hintMessage[hint])}</p> : null}
      {!writesEnabled ? <p className="small-copy instrumentation-readonly" role="status">{text("instrumentation_readonly")}</p> : null}
      {/* Closed by default: the plain words are here for whoever opens them, and
          nothing in this panel depends on having read them. */}
      <details className="instrumentation-disclosure">
        <summary>{text("instrumentation_disclosure_summary")}</summary>
        <h3>{text("instrumentation_counted_title")}</h3>
        <p>{text("instrumentation_counted")}</p>
        <h3>{text("instrumentation_never_title")}</h3>
        <p>{text("instrumentation_never")}</p>
        <h3>{text("instrumentation_storage_title")}</h3>
        <p>{text("instrumentation_storage")}</p>
        <p>{text("instrumentation_stays_here")}</p>
      </details>
      <h3 id="instrumentation-counters-title">{text("instrumentation_counters_title")}</h3>
      {instrumentation === null || counted.length === 0 ? (
        <p className="small-copy">{text("instrumentation_counters_empty")}</p>
      ) : (
        <table className="instrumentation-counters" aria-labelledby="instrumentation-counters-title">
          <thead>
            <tr>
              <th scope="col">{text("instrumentation_column_kind")}</th>
              {INSTRUMENTATION_OUTCOMES.map((outcome) => (
                <th key={outcome} scope="col">{text(`instrumentation_outcome_${outcome}` as MessageKey)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {counted.map((row) => (
              <tr key={row.kind}>
                <th scope="row">{text(`instrumentation_kind_${row.kind}` as MessageKey)}</th>
                {row.totals.map((value, index) => (
                  <td key={INSTRUMENTATION_OUTCOMES[index]}>{value}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="button-row">
        <button
          className="button button-secondary"
          disabled={controlsDisabled || clearAsked}
          onClick={onAskClear}
          type="button"
        >
          {text("instrumentation_clear")}
        </button>
      </div>
      {clearAsked ? (
        <section
          aria-describedby="instrumentation-clear-details"
          aria-labelledby="instrumentation-clear-title"
          className="notice instrumentation-clear-confirm"
          role="dialog"
        >
          <h3 id="instrumentation-clear-title">{text("instrumentation_clear_title")}</h3>
          <p id="instrumentation-clear-details">{text("instrumentation_clear_details")}</p>
          <div className="button-row">
            <button className="button button-primary" disabled={controlsDisabled} onClick={onConfirmClear} type="button">
              {busy ? text("working") : text("instrumentation_clear_confirm")}
            </button>
            {/* Dismissing the question changes nothing at all. */}
            <button className="button button-secondary" onClick={onCancelClear} type="button">
              {text("instrumentation_clear_cancel")}
            </button>
          </div>
        </section>
      ) : null}
    </section>
  );
}
