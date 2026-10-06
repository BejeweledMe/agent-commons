import dagre from "@dagrejs/dagre";
import type { TrackerEdge, TrackerTask } from "./contracts";

export type GraphState = "accepted" | "completed" | "review" | "active" | "ready" | "queued" | "blocked" | "cancelled" | "unknown";
export const GRAPH_STATES: readonly GraphState[] = ["accepted", "completed", "review", "active", "ready", "queued", "blocked", "cancelled", "unknown"];
export const GRAPH_ICONS: Readonly<Record<GraphState, string>> = {
  accepted: "✓✓", completed: "✓", review: "◇", active: "▶", ready: "○", queued: "⌛", blocked: "!", cancelled: "×", unknown: "?"
};
export const MAX_GRAPH_TASKS = 128;
export const NODE_WIDTH = 244;
export const NODE_HEIGHT = 168;
const GAP_X = 32;
const GAP_Y = 62;
const MARGIN = 24;
export type GraphFocus = "all" | "connected" | "upstream" | "downstream";
export type TaskGraphNode = { task: TrackerTask; x: number; y: number; rank: number; state: GraphState; hiddenPrerequisites: number };
export type TaskGraphLayout = {
  nodes: readonly TaskGraphNode[];
  edges: readonly { from: string; to: string; path: string }[];
  width: number; height: number; invalid: boolean; tooLarge: boolean; total: number; focused: boolean;
  selectedTasks: readonly TrackerTask[]; hiddenPrerequisites: number;
};

export function graphTaskState(task: TrackerTask): GraphState {
  if (task.taskState === "accepted" || task.taskState === "cancelled") return task.taskState;
  if (task.taskState === "completed" || task.taskState === "review") return task.taskState;
  if (task.taskState === "blocked" || task.readiness === "terminal_dependency_failure") return "blocked";
  if (task.taskState === "active" || ["launching", "running", "input_needed"].includes(task.phase ?? "")) return "active";
  if (task.freshness !== "fresh" || ["unknown", "policy_unknown"].includes(task.readiness)) return "unknown";
  if (["requested", "reserved"].includes(task.phase ?? "") || task.taskState === "assigned" || task.blockingDependencyIds.length > 0) return "queued";
  return task.readiness === "ready" ? "ready" : "unknown";
}

export function connectedTaskIds(tasks: readonly TrackerTask[], id: string): ReadonlySet<string> {
  const ids = new Set(tasks.map((task) => task.taskId));
  if (!ids.has(id)) return new Set();
  const links = new Map(tasks.map((task) => [task.taskId, new Set<string>()]));
  for (const task of tasks) for (const dependency of task.dependencyTaskIds) {
    if (!ids.has(dependency)) continue;
    links.get(task.taskId)?.add(dependency); links.get(dependency)?.add(task.taskId);
  }
  const found = new Set<string>(); const pending = [id];
  while (pending.length > 0 && found.size <= 512) {
    const current = pending.pop()!;
    if (found.has(current)) continue;
    found.add(current);
    pending.push(...(links.get(current) ?? []));
  }
  return found;
}

/** Focus follows only the server's edges; a dependency is never a parent/component relation. */
export function focusedTaskIds(tasks: readonly TrackerTask[], edges: readonly TrackerEdge[], id: string, direction: Exclude<GraphFocus, "all">): ReadonlySet<string> {
  const ids = new Set(tasks.map((task) => task.taskId));
  if (!ids.has(id)) return new Set();
  const links = new Map(tasks.map((task) => [task.taskId, new Set<string>()]));
  for (const edge of edges) {
    if (!ids.has(edge.prerequisiteTaskId) || !ids.has(edge.dependentTaskId) || edge.prerequisiteMissing) continue;
    if (direction !== "downstream") links.get(edge.dependentTaskId)?.add(edge.prerequisiteTaskId);
    if (direction !== "upstream") links.get(edge.prerequisiteTaskId)?.add(edge.dependentTaskId);
  }
  const found = new Set<string>(), pending = [id];
  while (pending.length > 0) {
    const current = pending.pop()!;
    if (found.has(current)) continue;
    found.add(current); pending.push(...(links.get(current) ?? []));
  }
  return found;
}

export function selectGraphTasks(tasks: readonly TrackerTask[], edges: readonly TrackerEdge[], selectedTaskId: string | null, focus: GraphFocus, search = ""): readonly TrackerTask[] {
  const focused = focus !== "all" && selectedTaskId !== null ? focusedTaskIds(tasks, edges, selectedTaskId, focus) : null;
  const query = search.trim().toLowerCase();
  return tasks.filter((task) => (!focused || focused.size === 0 || focused.has(task.taskId)) &&
    (!query || [task.title, task.taskId, task.roleName ?? "", task.suggestedRoleName ?? ""].some((value) => value.toLowerCase().includes(query))))
    .slice().sort((a, b) => a.taskId.localeCompare(b.taskId));
}

/** Numeric SVG sizing keeps zoom compatible with the no-inline-style CSP. */
export function graphViewport(width: number, height: number, zoom: number): { width: number; height: number; zoom: number } {
  const bounded = Number.isFinite(zoom) ? Math.min(2, Math.max(0.25, zoom)) : 1;
  return { width: Math.round(width * bounded), height: Math.round(height * bounded), zoom: bounded };
}

export function fitGraphZoom(width: number, availableWidth: number): number {
  return width > 0 && availableWidth > 0 ? Math.min(1, Math.max(0.25, availableWidth / width)) : 1;
}

export function buildTaskGraph(tasks: readonly TrackerTask[], edges: readonly TrackerEdge[], selectedTaskId: string | null, focusSelected: boolean | GraphFocus = true, search = ""): TaskGraphLayout {
  const focus = typeof focusSelected === "boolean" ? (focusSelected ? "connected" : "all") : focusSelected;
  const selected = selectGraphTasks(tasks, edges, selectedTaskId, focus, search);
  const ids = new Set(selected.map((task) => task.taskId));
  // De-duplicate server pairs without synthesizing edges from titles or assignments.
  const byPair = new Map<string, TrackerEdge>();
  for (const edge of edges) {
    const key = `${edge.prerequisiteTaskId}:${edge.dependentTaskId}`;
    byPair.set(key, { ...edge, prerequisiteMissing: edge.prerequisiteMissing || (byPair.get(key)?.prerequisiteMissing ?? false) });
  }
  const uniqueEdges = [...byPair.values()]
    .sort((a, b) => a.prerequisiteTaskId.localeCompare(b.prerequisiteTaskId) || a.dependentTaskId.localeCompare(b.dependentTaskId));
  const hiddenByTask = new Map(selected.map((task) => [task.taskId, 0]));
  for (const edge of uniqueEdges) if (ids.has(edge.dependentTaskId) && !ids.has(edge.prerequisiteTaskId) && !edge.prerequisiteMissing) {
    hiddenByTask.set(edge.dependentTaskId, hiddenByTask.get(edge.dependentTaskId)! + 1);
  }
  const base: TaskGraphLayout = { nodes: [], edges: [], width: NODE_WIDTH + MARGIN * 2, height: NODE_HEIGHT + MARGIN * 2,
    invalid: false, tooLarge: selected.length > MAX_GRAPH_TASKS, total: tasks.length,
    focused: focus !== "all" && selectedTaskId !== null && tasks.some((task) => task.taskId === selectedTaskId),
    selectedTasks: selected, hiddenPrerequisites: [...hiddenByTask.values()].reduce((sum, count) => sum + count, 0) };
  if (base.tooLarge || selected.length === 0) return base;
  const children = new Map(selected.map((task) => [task.taskId, new Set<string>()]));
  const incoming = new Map(selected.map((task) => [task.taskId, 0]));
  const selectedEdges = uniqueEdges.filter((edge) => ids.has(edge.dependentTaskId) && ids.has(edge.prerequisiteTaskId));
  let invalid = uniqueEdges.some((edge) => ids.has(edge.dependentTaskId) && edge.prerequisiteMissing);
  for (const edge of selectedEdges) {
    children.get(edge.prerequisiteTaskId)!.add(edge.dependentTaskId);
    incoming.set(edge.dependentTaskId, incoming.get(edge.dependentTaskId)! + 1);
  }
  const ranks = new Map(selected.map((task) => [task.taskId, 0]));
  const pending = selected.filter((task) => incoming.get(task.taskId) === 0).map((task) => task.taskId);
  let visited = 0;
  while (pending.length) {
    const id = pending.shift()!; visited += 1;
    for (const child of children.get(id) ?? []) {
      ranks.set(child, Math.max(ranks.get(child)!, ranks.get(id)! + 1));
      incoming.set(child, incoming.get(child)! - 1);
      if (incoming.get(child) === 0) pending.push(child);
    }
  }
  invalid ||= visited !== selected.length;
  if (invalid) return { ...base, invalid: true };
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: GAP_X, ranksep: GAP_Y, marginx: MARGIN, marginy: MARGIN });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const task of selected) graph.setNode(task.taskId, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of selectedEdges) graph.setEdge(edge.prerequisiteTaskId, edge.dependentTaskId);
  dagre.layout(graph);
  const nodes = selected.map((task): TaskGraphNode => {
    const position = graph.node(task.taskId);
    return { task, x: position.x - NODE_WIDTH / 2, y: position.y - NODE_HEIGHT / 2, rank: ranks.get(task.taskId)!,
      state: graphTaskState(task), hiddenPrerequisites: hiddenByTask.get(task.taskId)! };
  }).sort((a, b) => a.y - b.y || a.x - b.x || a.task.taskId.localeCompare(b.task.taskId));
  return { ...base, nodes, width: graph.graph().width ?? base.width, height: graph.graph().height ?? base.height,
    edges: selectedEdges.map((edge) => {
      const points: { x: number; y: number }[] = graph.edge(edge.prerequisiteTaskId, edge.dependentTaskId).points;
      return { from: edge.prerequisiteTaskId, to: edge.dependentTaskId,
        path: points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ") };
    }) };
}

export function wouldCreateTaskCycle(tasks: readonly TrackerTask[], id: string, dependencies: readonly string[]): boolean {
  const byId = new Map(tasks.map((task) => [task.taskId, task]));
  const pending = [...dependencies]; const visited = new Set<string>();
  while (pending.length > 0 && visited.size <= 512) {
    const current = pending.pop()!;
    if (current === id) return true;
    if (visited.has(current)) continue;
    visited.add(current); pending.push(...(byId.get(current)?.dependencyTaskIds ?? []));
  }
  return visited.size > 512;
}
