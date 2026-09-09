import type { TrackerSnapshot, TrackerTask } from "./contracts.js";
import type { BlueprintApplication } from "./libraryTypes.js";
import { taskObservationCurrent } from "./taskPresentation.js";
import type { TrackerViewState } from "./trackerState.js";

/**
 * Where a finished blueprint application may send the operator. Applying a
 * blueprint creates roles and tasks only, so neither intent starts a run: one
 * opens the existing Prepare run draft, the other opens the created plan.
 */
export type BlueprintApplyIntent =
  | Readonly<{ kind: "prepare_run"; taskId: string; agentId: string }>
  | Readonly<{ kind: "view_plan"; taskId: string | null }>;

export type BlueprintApplyNextAction = "start_ready_task" | "no_ready_task" | "unconfirmed";

export type BlueprintApplyOutcome = Readonly<{
  roleCount: number;
  taskCount: number;
  createdTaskIds: readonly string[];
  /** The one created task a refreshed snapshot confirms as startable. */
  start: Readonly<{ taskId: string; agentId: string }> | null;
  nextAction: BlueprintApplyNextAction;
}>;

function startable(snapshot: TrackerSnapshot, task: TrackerTask): boolean {
  return task.taskState === "ready" && task.readiness === "ready"
    && task.blockingDependencyIds.length === 0
    && taskObservationCurrent(snapshot, task);
}

/**
 * Read the applied plan against a refreshed tracker snapshot. Position in the
 * returned mapping never implies readiness, and a snapshot that has not yet
 * observed every created task reports unknown rather than ready or blocked.
 */
export function blueprintApplyOutcome(
  application: BlueprintApplication,
  tracker: TrackerViewState
): BlueprintApplyOutcome {
  const createdTaskIds = application.tasks.map((task) => task.taskId);
  const counts = {
    roleCount: application.roles.length,
    taskCount: application.tasks.length,
    createdTaskIds
  };
  if (tracker.kind !== "ready") {
    return { ...counts, start: null, nextAction: "unconfirmed" };
  }
  const snapshot = tracker.snapshot;
  const observed = new Map(snapshot.tasks.map((task) => [task.taskId, task]));
  if (createdTaskIds.some((taskId) => !observed.has(taskId))) {
    return { ...counts, start: null, nextAction: "unconfirmed" };
  }
  const created = application.tasks.map((task) => ({ ...task, observed: observed.get(task.taskId) }));
  for (const task of created) {
    if (task.observed && startable(snapshot, task.observed)) {
      return {
        ...counts,
        start: { taskId: task.taskId, agentId: task.agentId },
        nextAction: "start_ready_task"
      };
    }
  }
  // Only a snapshot that carries a current observation of every created task
  // can say they are all waiting; anything less stays unknown.
  const confirmed = created.every((task) => task.observed && taskObservationCurrent(snapshot, task.observed));
  return { ...counts, start: null, nextAction: confirmed ? "no_ready_task" : "unconfirmed" };
}

/** Opening Prepare run is offered only for a confirmed startable task. */
export function blueprintStartIntent(outcome: BlueprintApplyOutcome): BlueprintApplyIntent | null {
  return outcome.start === null
    ? null
    : { kind: "prepare_run", taskId: outcome.start.taskId, agentId: outcome.start.agentId };
}

/** Viewing the plan selects a created task; it never selects or starts a run. */
export function blueprintPlanIntent(outcome: BlueprintApplyOutcome): BlueprintApplyIntent {
  return { kind: "view_plan", taskId: outcome.createdTaskIds[0] ?? null };
}
