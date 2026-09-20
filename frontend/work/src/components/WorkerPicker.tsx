import type { ReactElement, ReactNode } from "react";
import type { MessageKey } from "../i18n.js";
import type {
  WorkerCapability,
  WorkerEligibilityEntry,
  WorkerEligibilityRefusalCode,
  WorkerEligibilityState,
  WorkerRefusal
} from "../contracts.js";

/**
 * What the surface has actually been told about the workers for the chosen
 * specialization. Only `ready` may offer a selection: every other state renders
 * the same workers as unknown, so a failed read can never look like eligibility.
 */
export type WorkerPickerState =
  | { kind: "idle" }
  | { kind: "loading"; workers: readonly WorkerEligibilityEntry[] }
  | { kind: "ready"; workers: readonly WorkerEligibilityEntry[] }
  | { kind: "unavailable"; workers: readonly WorkerEligibilityEntry[] };

export type WorkerChoice = {
  worker: WorkerEligibilityEntry;
  eligibility: WorkerEligibilityState;
  refusal: WorkerRefusal | null;
  selectable: boolean;
};

export const workerRefusalMessage: Readonly<Record<WorkerEligibilityRefusalCode, MessageKey>> = {
  worker_observation_missing: "worker_refusal_observation_missing",
  specialization_unavailable: "worker_refusal_specialization_unavailable",
  review_profile_incompatible: "worker_refusal_review_profile_incompatible",
  provider_installation_unavailable: "provider_availability_installation",
  provider_initialization_failed: "provider_availability_initialization",
  provider_qualification_required: "provider_availability_qualification_required",
  provider_qualification_failed: "provider_availability_qualification_failed",
  provider_authentication_required: "provider_availability_authentication_required",
  provider_authentication_unconfirmed: "provider_availability_authentication_unconfirmed"
};

export const workerCapabilityMessage: Readonly<Record<WorkerCapability, MessageKey>> = {
  tools: "worker_capability_tools",
  skills_projection: "worker_capability_skills_projection",
  trusted_workspace: "worker_capability_trusted_workspace",
  review_only: "worker_capability_review_only"
};

/** Closed remediation tokens. An unknown token is shown verbatim, never guessed at. */
export const workerRemediationMessage: Readonly<Record<string, MessageKey>> = {
  refresh_worker_availability: "worker_remediation_refresh_worker_availability",
  choose_available_specialization: "worker_remediation_choose_available_specialization",
  choose_builder_profile: "worker_remediation_choose_builder_profile",
  verify_provider_installation: "worker_remediation_verify_provider_installation",
  repair_provider_initialization: "worker_remediation_repair_provider_initialization",
  rerun_provider_canary: "worker_remediation_rerun_provider_canary",
  run_provider_canary: "worker_remediation_run_provider_canary",
  inspect_failed_provider_probe: "worker_remediation_inspect_failed_provider_probe",
  authenticate_provider: "worker_remediation_authenticate_provider",
  check_provider_authentication: "worker_remediation_check_provider_authentication"
};

export function remediationLabel(token: string, text: (key: MessageKey) => string): ReactNode {
  const key = workerRemediationMessage[token];
  return key ? text(key) : <code>{token}</code>;
}

/**
 * The rendered list. Outside `ready` every worker is unknown and unselectable,
 * and no refusal is attributed to the server that did not send one.
 */
export function workerChoices(state: WorkerPickerState): readonly WorkerChoice[] {
  if (state.kind === "idle") return [];
  const ready = state.kind === "ready";
  return state.workers.map((worker) => ({
    worker,
    eligibility: ready ? worker.eligibility : "unknown",
    refusal: ready ? worker.refusal : null,
    selectable: ready && worker.eligibility === "eligible"
  }));
}

function WorkerRefusalLines({ refusal, text }: {
  refusal: WorkerRefusal; text: (key: MessageKey) => string;
}): ReactElement {
  return <>
    <p>{text(workerRefusalMessage[refusal.code])}</p>
    {refusal.remediation.length > 0 ? <ul className="small-copy worker-remediation">
      {refusal.remediation.map((token) => <li key={token}>{remediationLabel(token, text)}</li>)}
    </ul> : null}
  </>;
}

export function WorkerPicker({ state, value, refusal, invalid, onChange, onRetry, text }: {
  state: WorkerPickerState;
  value: string;
  /** A hire the server refused; the outer `null` means no refusal was answered. */
  refusal: { refusal: WorkerRefusal | null } | null;
  invalid: boolean;
  onChange: (worker: WorkerEligibilityEntry) => void;
  onRetry: () => void;
  text: (key: MessageKey) => string;
}): ReactElement {
  const choices = workerChoices(state);
  return <fieldset className="worker-picker" disabled={state.kind === "idle"}>
    <legend>{text("worker_step_title")}</legend>
    {state.kind === "idle"
      ? <p className="small-copy" id="worker-step-hint">{text("worker_step_hint")}</p>
      : null}
    {state.kind === "loading"
      ? <p className="small-copy" role="status">{text("worker_loading")}</p>
      : null}
    {state.kind === "unavailable" ? <p className="field-error" role="status">
      {text("worker_read_unavailable")}
      <button type="button" className="notice-link" onClick={onRetry}>{text("check_again")}</button>
    </p> : null}
    {state.kind === "ready" && choices.length === 0
      ? <p className="field-error" role="status">{text("worker_none_offered")}</p>
      : null}
    <ul className="worker-list">
      {choices.map((choice) => <li key={choice.worker.profileId} className="worker-option"
        data-eligibility={choice.eligibility}>
        <label htmlFor={`worker-${choice.worker.profileId}`}>
          <input type="radio" name="hire-worker" id={`worker-${choice.worker.profileId}`}
            value={choice.worker.profileId} disabled={!choice.selectable}
            checked={choice.selectable && value === choice.worker.profileId}
            aria-describedby={choice.selectable ? undefined : `worker-${choice.worker.profileId}-refusal`}
            onChange={() => onChange(choice.worker)} />
          <strong>{choice.worker.profileId}</strong>
        </label>
        <p className="small-copy"><code>{choice.worker.provider}</code>
          {choice.worker.model === null ? null : <> · <code>{choice.worker.model}</code></>}</p>
        {choice.worker.capabilities.length > 0 ? <ul className="worker-chips">
          {choice.worker.capabilities.map((capability) => <li key={capability} className="worker-chip">
            {text(workerCapabilityMessage[capability])}
          </li>)}
        </ul> : null}
        {choice.selectable ? null : <div className="worker-refusal"
          id={`worker-${choice.worker.profileId}-refusal`}>
          {choice.refusal
            ? <WorkerRefusalLines refusal={choice.refusal} text={text} />
            : <p>{text(choice.eligibility === "ineligible"
              ? "worker_state_ineligible" : "worker_state_unknown")}</p>}
        </div>}
      </li>)}
    </ul>
    {choices.length > 0
      ? <p className="small-copy" id="worker-capabilities-help">{text("worker_capabilities_help")}</p>
      : null}
    {invalid ? <p className="field-error" id="worker-choice-error">{text("form_error_worker")}</p> : null}
    {refusal ? <div className="field-error worker-hire-refusal" role="status">
      <p>{text("worker_hire_refused")}</p>
      {refusal.refusal ? <WorkerRefusalLines refusal={refusal.refusal} text={text} /> : null}
    </div> : null}
  </fieldset>;
}
