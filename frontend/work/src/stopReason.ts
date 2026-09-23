import type { StopReason, TrackerRun } from "./contracts";
import type { MessageKey } from "./i18n";

/**
 * The server's closed reason set for a refused launch or a stopped run
 * (`runtime/refusals.py`). The client renders exactly what the server sent and
 * never derives a code from process output; a code outside this set keeps its
 * object and reads as the neutral sentence.
 */
export const STOP_REASON_CODES: readonly string[] = ["workspace_snapshot_unreadable",
  "executor_access_missing", "provider_mcp_handshake_failed", "executor_stopped_by_environment",
  "provider_reported_error", "unknown"];
/** Codes a refusal may carry before any attempt exists. */
export const LAUNCH_REFUSAL_CODES: readonly string[] = ["workspace_snapshot_unreadable",
  "executor_access_missing", "provider_mcp_handshake_failed", "unknown"];

const SENTENCES: Readonly<Record<string, MessageKey>> = {
  workspace_snapshot_unreadable: "stop_reason_workspace_snapshot_unreadable",
  executor_access_missing: "stop_reason_executor_access_missing",
  provider_mcp_handshake_failed: "stop_reason_provider_mcp_handshake_failed",
  executor_stopped_by_environment: "stop_reason_executor_stopped_by_environment",
  provider_reported_error: "stop_reason_provider_reported_error",
  unknown: "stop_reason_unknown"
};

/** One plain sentence for the code. An unlisted code is neutral, never empty. */
export function stopReasonSentence(code: string): MessageKey {
  return SENTENCES[code] ?? "stop_reason_unknown";
}

/** The controls this UI can actually offer for a next action; `null` is a hint. */
export type StopReasonControl = "refresh" | "conversation" | "prepare_run" | null;
export type StopReasonAction = { key: MessageKey | null; control: StopReasonControl };

// Closed next-action tokens: the ones the runtime emits with a refusal or a stop
// reason, plus the two canonical task actions the card itself can perform. A
// token outside the table is shown verbatim in <code> with a neutral lead-in.
const ACTIONS: Readonly<Record<string, StopReasonAction>> = {
  select_checkout: { key: "stop_action_select_checkout", control: null },
  select_git_worktree: { key: "stop_action_select_git_worktree", control: null },
  repair_control_files: { key: "stop_action_repair_control_files", control: null },
  repair_runtime_profile: { key: "stop_action_repair_runtime_profile", control: null },
  repair_mcp_configuration: { key: "stop_action_repair_mcp_configuration", control: null },
  run_provider_canary: { key: "stop_action_run_provider_canary", control: null },
  inspect_provider_diagnostic: { key: "stop_action_inspect_provider_diagnostic", control: null },
  inspect_canonical_outcome: { key: "stop_action_inspect_canonical_outcome", control: "refresh" },
  answer_operator_request: { key: "tracker_gloss_answer_operator_request", control: "conversation" },
  retry_new_run: { key: "tracker_gloss_retry_new_run", control: "prepare_run" }
};

export function stopReasonAction(token: string): StopReasonAction {
  return ACTIONS[token] ?? { key: null, control: null };
}

/** Run phases with a provider process still on them: a run is live. */
export const LIVE_PROCESS_PHASES: readonly string[] = ["requested", "reserved", "launching",
  "running", "cancellation_requested"];

/** UX-20: while this is non-null the card must not offer to prepare another run. */
export function runInProgress(runs: readonly TrackerRun[]): TrackerRun | null {
  return runs.find((run) => LIVE_PROCESS_PHASES.includes(run.phase)) ?? null;
}

/** Phases whose generic copy the structured reason replaces when one exists. */
export const AWAITING_HUMAN_PHASES: readonly string[] = ["needs_operator", "input_needed"];

function runOrder(run: TrackerRun): string {
  return run.finishedAt ?? run.updatedAt ?? run.startedAt ?? "";
}

/** The run whose outcome the card is describing: the most recently moved one. */
export function newestRun(runs: readonly TrackerRun[]): TrackerRun | null {
  return runs.reduce<TrackerRun | null>((latest, run) =>
    latest === null || runOrder(run) >= runOrder(latest) ? run : latest, null);
}

/**
 * The reason to show on the card. Only the newest run can explain the state the
 * card is describing, so an older run's stop reason is left in the run history.
 */
export function currentStopReason(runs: readonly TrackerRun[]): StopReason | null {
  return newestRun(runs)?.stopReason ?? null;
}
