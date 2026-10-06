import dagre from "@dagrejs/dagre";
import { MAX_GRAPH_TASKS, NODE_HEIGHT, NODE_WIDTH } from "./taskGraph.js";
import type { TrackerTask } from "./contracts.js";

type Node = Pick<TrackerTask, "taskId" | "parentTaskId">;
export function hierarchyWouldCycle(tasks: readonly Node[], taskId: string, parentId: string | null): boolean {
  const parents = new Map(tasks.map((task) => [task.taskId, task.parentTaskId]));
  const seen = new Set([taskId]);
  let current = parentId;
  while (current) {
    if (seen.has(current)) return true;
    seen.add(current);
    current = parents.get(current) ?? null;
  }
  return false;
}

/** Iterative traversal keeps deeply nested data bounded by the supplied view. */
export function hierarchyRows<T extends Node>(tasks: readonly T[]): { rows: { task: T; depth: number; parentOutside: boolean }[]; invalid: boolean } {
  const ids = new Set(tasks.map((task) => task.taskId));
  const children = new Map<string | null, T[]>();
  for (const task of tasks) {
    const parent = task.parentTaskId && ids.has(task.parentTaskId) ? task.parentTaskId : null;
    children.set(parent, [...(children.get(parent) ?? []), task]);
  }
  const pending = [...(children.get(null) ?? [])].reverse().map((task) => ({ task, depth: 0 }));
  const seen = new Set<string>();
  const rows: { task: T; depth: number; parentOutside: boolean }[] = [];
  while (pending.length) {
    const row = pending.pop()!;
    if (seen.has(row.task.taskId)) continue;
    seen.add(row.task.taskId);
    rows.push({ ...row, parentOutside: Boolean(row.task.parentTaskId && !ids.has(row.task.parentTaskId)) });
    for (const task of [...(children.get(row.task.taskId) ?? [])].reverse()) pending.push({ task, depth: row.depth + 1 });
  }
  const invalid = rows.length !== tasks.length;
  for (const task of tasks) if (!seen.has(task.taskId)) rows.push({ task, depth: 0, parentOutside: false });
  return { rows, invalid };
}

export type HierarchyLayout = {
  nodes: { id: string; task: TrackerTask | null; x: number; y: number }[];
  edges: { from: string; to: string; path: string }[];
  selectedTasks: readonly TrackerTask[]; width: number; height: number;
  tooLarge: boolean; invalid: boolean; hiddenParents: number;
};

/** Containment only: never reads dependencyTaskIds or server readiness edges. */
export function buildHierarchyGraph(tasks: readonly TrackerTask[], selectedId: string | null, focus = true, search = ""): HierarchyLayout {
  const byId = new Map(tasks.map((task) => [task.taskId, task]));
  const children = new Map<string, string[]>();
  for (const task of tasks) if (task.parentTaskId) children.set(task.parentTaskId, [...(children.get(task.parentTaskId) ?? []), task.taskId]);
  let scope = new Set(byId.keys());
  if (focus && selectedId && byId.has(selectedId)) {
    scope = new Set<string>();
    const pending = [selectedId];
    while (pending.length) {
      const current = pending.pop()!;
      if (scope.has(current)) continue;
      scope.add(current); pending.push(...(children.get(current) ?? []));
    }
    let current: string | null = byId.get(selectedId)?.parentTaskId ?? null;
    while (current && byId.has(current) && !scope.has(current)) { scope.add(current); current = byId.get(current)?.parentTaskId ?? null; }
  }
  const query = search.trim().toLowerCase();
  if (query) {
    const matches = new Set(tasks.filter((task) => scope.has(task.taskId) && [task.title, task.taskId].some((text) => text.toLowerCase().includes(query))).map((task) => task.taskId));
    for (const id of [...matches]) {
      let parent = byId.get(id)?.parentTaskId;
      while (parent && scope.has(parent) && !matches.has(parent)) { matches.add(parent); parent = byId.get(parent)?.parentTaskId; }
    }
    scope = matches;
  }
  const selected = tasks.filter((task) => scope.has(task.taskId)).slice().sort((a, b) => a.taskId.localeCompare(b.taskId));
  const hiddenParents = selected.filter((task) => task.parentTaskId && !scope.has(task.parentTaskId)).length;
  const base: HierarchyLayout = { nodes: [], edges: [], selectedTasks: selected, width: NODE_WIDTH + 48, height: NODE_HEIGHT + 48,
    tooLarge: selected.length > MAX_GRAPH_TASKS, invalid: hierarchyRows(selected).invalid, hiddenParents };
  if (base.tooLarge || base.invalid || !selected.length) return base;
  const root = "project-root"; // Presentation only; never passed to canonical task actions.
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: 32, ranksep: 62, marginx: 24, marginy: 24 });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setNode(root, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const task of selected) graph.setNode(task.taskId, { width: NODE_WIDTH, height: NODE_HEIGHT });
  const edges: { from: string; to: string }[] = [];
  for (const task of selected) {
    const parent = task.parentTaskId ?? root;
    // A filtered-out parent is a boundary, never an invented project-root edge.
    if (parent === root || scope.has(parent)) { graph.setEdge(parent, task.taskId); edges.push({ from: parent, to: task.taskId }); }
  }
  dagre.layout(graph);
  return { ...base, width: graph.graph().width ?? base.width, height: graph.graph().height ?? base.height,
    nodes: [{ id: root, task: null }, ...selected.map((task) => ({ id: task.taskId, task }))].map((node) => {
      const position = graph.node(node.id); return { ...node, x: position.x - NODE_WIDTH / 2, y: position.y - NODE_HEIGHT / 2 };
    }).sort((a, b) => a.y - b.y || a.x - b.x),
    edges: edges.map((edge) => ({ ...edge, path: (graph.edge(edge.from, edge.to).points as { x: number; y: number }[]).map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ") })) };
}
