import { type TrackerSnapshot } from "./contracts";

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
