import type { TrackerEdge, TrackerTask } from "./contracts";

export type GraphState = "accepted" | "completed" | "review" | "active" | "ready" | "queued" | "blocked" | "cancelled" | "unknown";
export const GRAPH_STATES: readonly GraphState[] = ["accepted", "completed", "review", "active", "ready", "queued", "blocked", "cancelled", "unknown"];
export const GRAPH_ICONS: Readonly<Record<GraphState, string>> = {
  accepted: "✓✓", completed: "✓", review: "◇", active: "▶", ready: "○", queued: "⌛", blocked: "!", cancelled: "×", unknown: "?"
};
export const MAX_GRAPH_TASKS = 128;
export const NODE_WIDTH = 244;
export const NODE_HEIGHT = 144;
const GAP_X = 32;
const GAP_Y = 62;
const MARGIN = 24;
export type TaskGraphNode = { task: TrackerTask; x: number; y: number; rank: number; state: GraphState };
export type TaskGraphLayout = {
  nodes: readonly TaskGraphNode[];
  edges: readonly { from: string; to: string; path: string }[];
  width: number; height: number; invalid: boolean; tooLarge: boolean; total: number; focused: boolean;
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

export function buildTaskGraph(tasks: readonly TrackerTask[], edges: readonly TrackerEdge[], selectedTaskId: string | null, focusSelected = true): TaskGraphLayout {
  const connected = focusSelected && selectedTaskId !== null ? connectedTaskIds(tasks, selectedTaskId) : null;
  const selected = (connected && connected.size > 0 ? tasks.filter((task) => connected.has(task.taskId)) : tasks).slice().sort((a, b) => a.taskId.localeCompare(b.taskId));
  const base = { nodes: [], edges: [], width: NODE_WIDTH + MARGIN * 2, height: NODE_HEIGHT + MARGIN * 2, invalid: false, tooLarge: selected.length > MAX_GRAPH_TASKS, total: tasks.length, focused: connected !== null && connected.size > 0 };
  if (base.tooLarge || selected.length === 0) return base;
  const ids = new Set(selected.map((task) => task.taskId));
  const children = new Map(selected.map((task) => [task.taskId, new Set<string>()]));
  const incoming = new Map(selected.map((task) => [task.taskId, 0]));
  const selectedEdges = edges.filter((edge) => ids.has(edge.dependentTaskId) && ids.has(edge.prerequisiteTaskId));
  let invalid = edges.some((edge) => ids.has(edge.dependentTaskId) && edge.prerequisiteMissing);
  for (const edge of selectedEdges) {
    const destinations = children.get(edge.prerequisiteTaskId)!;
    if (!destinations.has(edge.dependentTaskId)) {
      destinations.add(edge.dependentTaskId);
      incoming.set(edge.dependentTaskId, incoming.get(edge.dependentTaskId)! + 1);
    }
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
  const nodes: TaskGraphNode[] = [];
  let y = MARGIN; let columns = 1;
  for (let rank = 0; rank <= Math.max(...ranks.values()); rank += 1) {
    const row = selected.filter((task) => ranks.get(task.taskId) === rank);
    const count = Math.min(4, row.length); columns = Math.max(columns, count);
    row.forEach((task, index) => nodes.push({ task, rank, state: graphTaskState(task),
      x: MARGIN + (index % 4) * (NODE_WIDTH + GAP_X), y: y + Math.floor(index / 4) * (NODE_HEIGHT + GAP_Y) }));
    y += Math.ceil(row.length / 4) * (NODE_HEIGHT + GAP_Y);
  }
  const byId = new Map(nodes.map((node) => [node.task.taskId, node]));
  return {
    ...base, nodes, width: MARGIN * 2 + columns * NODE_WIDTH + (columns - 1) * GAP_X,
    height: y - GAP_Y + MARGIN,
    edges: selectedEdges.map((edge) => {
      const from = byId.get(edge.prerequisiteTaskId)!; const to = byId.get(edge.dependentTaskId)!;
      const x1 = from.x + NODE_WIDTH / 2; const y1 = from.y + NODE_HEIGHT;
      const x2 = to.x + NODE_WIDTH / 2; const y2 = to.y;
      return { from: edge.prerequisiteTaskId, to: edge.dependentTaskId,
        path: `M ${x1} ${y1} C ${x1} ${y1 + GAP_Y / 2}, ${x2} ${y2 - GAP_Y / 2}, ${x2} ${y2}` };
    })
  };
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
