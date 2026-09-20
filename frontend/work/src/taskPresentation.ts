import type { TrackerSnapshot, TrackerTask } from "./contracts";
import type { MessageKey } from "./i18n";

export type TaskFilter = "all" | "attention" | "ready" | "assigned" | "active" | "blocked"
  | "completed" | "review" | "accepted" | "cancelled";
export const TASK_FILTERS: readonly TaskFilter[] = ["all", "attention", "ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"];
export const taskFilterLabel: Readonly<Record<TaskFilter, MessageKey>> = {
  all: "task_view_all", attention: "task_view_attention", ready: "task_view_ready",
  assigned: "task_view_assigned", active: "task_view_active", blocked: "task_view_blocked",
  completed: "task_view_completed", review: "task_view_review", accepted: "task_view_accepted", cancelled: "task_view_cancelled"
};

export type StateDomain = "task" | "readiness" | "run" | "evidence" | "freshness" | "capacity" | "action";
const glossaries: Readonly<Record<StateDomain, Readonly<Record<string, MessageKey>>>> = {
  task: { ready: "task_view_state_ready", assigned: "tracker_gloss_assigned", active: "tracker_gloss_active",
    blocked: "task_view_state_blocked", completed: "tracker_gloss_complete", review: "tracker_gloss_review",
    accepted: "tracker_gloss_accepted", cancelled: "tracker_gloss_cancelled" },
  readiness: { ready: "tracker_gloss_ready", blocked: "inspector_readiness_blocked",
    terminal_dependency_failure: "tracker_gloss_dependency_failure", policy_unknown: "tracker_gloss_policy_unknown",
    in_progress: "inspector_readiness_in_progress", human_attention: "tracker_gloss_human_attention",
    complete: "inspector_readiness_complete", cancelled: "tracker_gloss_cancelled", unknown: "tracker_gloss_unknown" },
  run: { requested: "tracker_gloss_requested", reserved: "tracker_gloss_reserved", launching: "tracker_gloss_launching",
    running: "tracker_gloss_running", cancellation_requested: "tracker_gloss_cancellation_requested",
    input_needed: "tracker_gloss_input_needed", succeeded: "tracker_gloss_succeeded", failed: "tracker_gloss_failed",
    cancelled: "tracker_gloss_cancelled", timed_out: "tracker_gloss_timed_out", needs_operator: "tracker_gloss_needs_operator",
    unknown: "tracker_gloss_unknown" },
  evidence: { complete: "evidence_complete", partial: "evidence_partial", missing: "evidence_missing", stale: "evidence_stale" },
  freshness: { fresh: "tracker_gloss_fresh", stale: "tracker_gloss_stale", unknown: "tracker_gloss_unknown" },
  capacity: { available: "tracker_gloss_available", saturated: "tracker_gloss_saturated", backpressure: "tracker_gloss_backpressure", unknown: "tracker_gloss_unknown" },
  action: { wait_for_run: "tracker_gloss_wait_for_run", start_ready_work: "tracker_gloss_start_ready_work",
    resolve_dependencies: "tracker_gloss_resolve_dependencies", answer_operator_request: "tracker_gloss_answer_operator_request",
    inspect_failure: "tracker_gloss_inspect_failure", retry_new_run: "tracker_gloss_retry_new_run",
    request_review: "tracker_gloss_request_review", wait_for_review: "tracker_gloss_wait_for_review",
    revise_work: "tracker_gloss_revise_work", accept_task: "tracker_gloss_accept_task",
    inspect_missing_evidence: "tracker_gloss_inspect_missing_evidence", none: "tracker_gloss_none" }
};
export function stateLabel(domain: StateDomain, value: string): MessageKey {
  return glossaries[domain][value] ?? (domain === "evidence" ? "evidence_unknown" : "tracker_gloss_unknown");
}
export function readinessUnconfirmed(task: TrackerTask): boolean {
  return ["unknown", "policy_unknown", "terminal_dependency_failure"].includes(task.readiness);
}
// Run progress can be stale while the canonical task revision was just
// observed. Preserve the run warning while permitting edits of that task.
// Missing source evidence or a stream gap still blocks all task actions.
export function taskObservationCurrent(snapshot: TrackerSnapshot, task: TrackerTask): boolean {
  const usableObservation = ((snapshot.state === "ready" || snapshot.state === "partial")
    && snapshot.freshness.state === "fresh")
    || (snapshot.state === "stale" && snapshot.freshness.state === "stale");
  return usableObservation
    && !snapshot.gaps.some((gap) => ["graph_stale", "graph_malformed",
      "projection_missing", "source_revision_unavailable", "resume_gap"].includes(gap))
    && !snapshot.freshness.resumeGap
    && task.freshness === "fresh"
    && !readinessUnconfirmed(task);
}
/** A role's tasks: those suggested for it plus those any of its runs acted on. */
function agentTaskIds(snapshot: TrackerSnapshot, agentId: string | null): ReadonlySet<string> | null {
  if (agentId === null) return null;
  return new Set([
    ...snapshot.tasks.filter((task) => task.suggestedAgentId === agentId).map((task) => task.taskId),
    ...snapshot.runs.filter((run) => run.agentId === agentId && run.taskId !== null).map((run) => run.taskId as string)
  ]);
}

export function filterTrackerTasks(snapshot: TrackerSnapshot, filter: TaskFilter, search: string, agentId: string | null = null): readonly TrackerTask[] {
  const attentionIds = new Set(snapshot.attention.map((item) => item.taskId));
  const query = search.trim().toLocaleLowerCase();
  const roleTaskIds = agentTaskIds(snapshot, agentId);
  return snapshot.tasks.filter((task) => (filter === "all"
    || (filter === "attention" ? task.awaitsHuman || attentionIds.has(task.taskId) : task.taskState === filter))
    && (roleTaskIds === null || roleTaskIds.has(task.taskId))
    && (!query || [task.title, task.roleName ?? "", task.taskId].some((value) => value.toLocaleLowerCase().includes(query))));
}

// The «Сейчас» / Now view. Three fixed columns answer one question each: what is
// waiting for the person, what is moving, what comes next. The owner's column
// definitions (plan section D, 8.1) say nothing about finished work, so accepted
// and cancelled tasks are kept out of the columns and stay in "All tasks";
// `settled` names them so the partition below is still total over the snapshot.
export type NowColumn = "needs_you" | "in_progress" | "next";
export const NOW_COLUMNS: readonly NowColumn[] = ["needs_you", "in_progress", "next"];
export const nowColumnLabel: Readonly<Record<NowColumn, MessageKey>> = {
  needs_you: "task_view_now_needs_you", in_progress: "task_view_now_in_progress", next: "task_view_now_next"
};
export const nowColumnEmpty: Readonly<Record<NowColumn, MessageKey>> = {
  needs_you: "task_view_now_needs_you_empty", in_progress: "task_view_now_in_progress_empty", next: "task_view_now_next_empty"
};
export type NowBuckets = Readonly<Record<NowColumn, readonly TrackerTask[]>> & {
  /** Accepted and cancelled work: nothing is waiting now, so no column claims it. */
  readonly settled: readonly TrackerTask[];
};
/** Canonical task states that are finished: they leave the Now columns entirely. */
const SETTLED_TASK_STATES: readonly string[] = ["accepted", "cancelled"];
/** Canonical task states whose next step is a human decision. */
const HUMAN_TASK_STATES: readonly string[] = ["completed", "review"];
/** Run phases that stopped and cannot continue without the person. */
const HUMAN_RUN_PHASES: readonly string[] = ["input_needed", "needs_operator", "failed", "timed_out"];
/** Run phases that are queued or moving; neither needs the person right now. */
const BUSY_RUN_PHASES: readonly string[] = ["requested", "reserved", "launching", "running", "cancellation_requested"];

/**
 * Partition the snapshot's tasks for the Now view. Pure and deterministic: the
 * first matching rule wins, so every task lands in exactly one group, and each
 * group keeps the snapshot's own task order.
 */
export function bucketTasksForNow(snapshot: TrackerSnapshot, agentId: string | null = null): NowBuckets {
  const attentionIds = new Set(snapshot.attention.map((item) => item.taskId));
  const roleTaskIds = agentTaskIds(snapshot, agentId);
  const needsYou: TrackerTask[] = [];
  const inProgress: TrackerTask[] = [];
  const next: TrackerTask[] = [];
  const settled: TrackerTask[] = [];
  for (const task of snapshot.tasks) {
    if (roleTaskIds !== null && !roleTaskIds.has(task.taskId)) continue;
    const phase = task.phase ?? "";
    if (SETTLED_TASK_STATES.includes(task.taskState)) settled.push(task);
    else if (task.awaitsHuman || attentionIds.has(task.taskId)
      || HUMAN_TASK_STATES.includes(task.taskState) || HUMAN_RUN_PHASES.includes(phase)) needsYou.push(task);
    else if (task.taskState === "active" || BUSY_RUN_PHASES.includes(phase)) inProgress.push(task);
    else next.push(task);
  }
  return { needs_you: needsYou, in_progress: inProgress, next, settled };
}

/** Optional capacity telemetry does not imply missing task or run evidence. */
export function trackerCapacityOnlyGap(snapshot: TrackerSnapshot): boolean {
  return snapshot.state === "partial" && !snapshot.truncated
    && snapshot.gaps.length === 1 && snapshot.gaps[0] === "capacity_missing"
    && snapshot.capacity.state === "unknown"
    && snapshot.tasks.every((task) => task.gaps.length === 0 && task.evidenceState === "complete")
    && snapshot.runs.every((run) => run.evidenceState === "complete");
}
