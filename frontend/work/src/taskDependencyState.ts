import type { TaskOption } from "./contracts";
import type { TrackerViewState } from "./trackerState.js";

export type DependencyCatalog = { tasks: readonly TaskOption[]; status: "loading" | "unavailable" | "partial" | "current" };

export function taskDependencyCatalog(observation: TrackerViewState): DependencyCatalog {
  if (observation.kind !== "ready") return { tasks: [], status: observation.kind === "loading" ? "loading" : "unavailable" };
  const { snapshot, connection } = observation;
  if (snapshot.state === "error" || snapshot.state === "loading") return { tasks: [], status: "unavailable" };
  return {
    tasks: snapshot.tasks.map((task) => ({ id: task.taskId, title: task.title, state: task.taskState })),
    status: snapshot.truncated || snapshot.state === "partial" || snapshot.state === "stale"
      || snapshot.freshness.state !== "fresh" || snapshot.freshness.resumeGap || connection === "disconnected"
      ? "partial" : "current"
  };
}
