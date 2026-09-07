import type { WorkApi } from "./api";
import { type TaskDetail, type TrackerSnapshot } from "./contracts";

export type TrackerViewState =
  | { kind: "loading" }
  | { kind: "failure" }
  | {
      kind: "ready";
      connection: "connecting" | "connected" | "disconnected";
      snapshot: TrackerSnapshot;
    };

export function trackerLoadSucceeded(
  current: TrackerViewState,
  snapshot: TrackerSnapshot
): TrackerViewState {
  if (current.kind === "ready" && current.snapshot.sequence >= snapshot.sequence) {
    return current;
  }
  return {
    kind: "ready",
    connection: current.kind === "ready" ? current.connection : "connecting",
    snapshot
  };
}

export function trackerStreamSucceeded(
  current: TrackerViewState,
  snapshot: TrackerSnapshot
): TrackerViewState {
  if (current.kind === "ready" && current.snapshot.sequence > snapshot.sequence) {
    return current;
  }
  return { kind: "ready", connection: "connected", snapshot };
}

export function trackerLoadFailed(current: TrackerViewState): TrackerViewState {
  return current.kind === "ready"
    ? { ...current, connection: "disconnected" }
    : { kind: "failure" };
}

export type TaskDetailState =
  | { kind: "loading"; taskId: string }
  | { kind: "failure"; taskId: string }
  | { kind: "ready"; taskId: string; detail: TaskDetail };

export async function loadTaskDetailState(
  api: Pick<WorkApi, "loadTaskDetail">,
  taskId: string,
  signal: AbortSignal,
  receive: (state: TaskDetailState) => void
): Promise<void> {
  if (signal.aborted) return;
  receive({ kind: "loading", taskId });
  try {
    const detail = await api.loadTaskDetail(taskId, signal);
    if (signal.aborted) return;
    receive(detail.taskId === taskId ? { kind: "ready", taskId, detail } : { kind: "failure", taskId });
  } catch {
    if (!signal.aborted) receive({ kind: "failure", taskId });
  }
}
