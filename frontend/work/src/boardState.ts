import dagre from "@dagrejs/dagre";
import type { RoleOption, TrackerSnapshot } from "./contracts.js";
import type { BlueprintApplication } from "./libraryTypes.js";
import type { TrackerViewState } from "./trackerState.js";

/**
 * The project board (ADR 0020): standing roles are nodes, agent links are
 * edges, departments are frames. Everything here is a projection of server
 * facts plus the operator's own arrangement; nothing in this module advances a
 * canonical state, and a missing arrangement only costs an auto-layout.
 */

export const ROLE_NODE_WIDTH = 264;
export const ROLE_NODE_HEIGHT = 140;
export const FRAME_PADDING = 32;
export const FRAME_HEADER = 44;
const AGENT_ID = /^agent\.[0-7][0-9A-HJKMNP-TV-Z]{25}$/;
const FRAME_ID = /^frame\.[a-f0-9]{8,32}$/;
const COORDINATE_LIMIT = 1_000_000;

export class BoardError extends Error {}

export type BoardLink = Readonly<{ id: string; from: string; to: string; allowedAction: string | null; state: string | null }>;
export type BoardGraph = Readonly<{ roles: readonly string[]; links: readonly BoardLink[]; reportsTo: readonly Readonly<{ from: string; to: string }>[] }>;
export type BoardPosition = Readonly<{ x: number; y: number }>;
export type BoardFrame = Readonly<{ id: string; title: string; x: number; y: number; width: number; height: number; members: readonly string[]; blueprintId: string | null }>;
export type BoardLayout = Readonly<{ revision: number; positions: Readonly<Record<string, BoardPosition>>; frames: readonly BoardFrame[] }>;
export type RoleActivity = "running" | "queued" | "attention" | "idle";
export type BoardRoleNode = Readonly<{
  id: string; name: string; specialization: string | null; profileId: string; model: string | null; contextMode: "fresh" | "accumulated";
  activity: RoleActivity; runPhase: string | null; openTaskCount: number; taskCount: number; awaitsHuman: boolean;
  position: BoardPosition; frameId: string | null; placed: boolean;
}>;
export type BoardModel = Readonly<{ nodes: readonly BoardRoleNode[]; frames: readonly BoardFrame[]; links: readonly BoardLink[]; reportsTo: readonly Readonly<{ from: string; to: string }>[] }>;

export const EMPTY_LAYOUT: BoardLayout = Object.freeze({ revision: 0, positions: Object.freeze({}), frames: Object.freeze([]) });

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new BoardError("expected an object");
  return value as Record<string, unknown>;
}
function integer(value: unknown, limit: number, minimum = -limit): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > limit) throw new BoardError("expected a bounded integer");
  return value;
}
function optionalText(value: unknown): string | null { return typeof value === "string" && value.length <= 200 ? value : null; }

/** Only the role structure of the workspace graph: standing roles and the links between them. */
export function parseBoardGraph(value: unknown): BoardGraph {
  const root = object(value);
  if (!Array.isArray(root.nodes) || !Array.isArray(root.edges)) throw new BoardError("graph is malformed");
  const roles: string[] = [];
  const linkNodes = new Map<string, { allowedAction: string | null; state: string | null; from: string | null; to: string | null }>();
  for (const raw of root.nodes) {
    const node = object(raw);
    const id = typeof node.id === "string" ? node.id : "";
    const attrs = typeof node.attrs === "object" && node.attrs !== null ? node.attrs as Record<string, unknown> : {};
    if (node.kind === "agent" && AGENT_ID.test(id) && attrs.template !== true && node.state !== "retired") roles.push(id);
    if (node.kind === "agent_link" && typeof id === "string") {
      linkNodes.set(id, { allowedAction: optionalText(attrs.allowed_action), state: optionalText(node.state), from: null, to: null });
    }
  }
  const reportsTo: { from: string; to: string }[] = [];
  for (const raw of root.edges) {
    const edge = object(raw);
    const from = typeof edge.from === "string" ? edge.from : "", to = typeof edge.to === "string" ? edge.to : "";
    if (edge.kind === "reports_to" && AGENT_ID.test(from) && AGENT_ID.test(to)) reportsTo.push({ from, to });
    const link = linkNodes.get(from);
    if (link && AGENT_ID.test(to)) {
      if (edge.kind === "link_from") link.from = to;
      if (edge.kind === "link_to") link.to = to;
    }
  }
  const known = new Set(roles);
  const links: BoardLink[] = [];
  for (const [id, link] of linkNodes) {
    if (link.from && link.to && known.has(link.from) && known.has(link.to) && link.state !== "closed") {
      links.push({ id, from: link.from, to: link.to, allowedAction: link.allowedAction, state: link.state });
    }
  }
  return { roles, links, reportsTo: reportsTo.filter((edge) => known.has(edge.from) && known.has(edge.to)) };
}

/** The stored arrangement. Absent fields read as "not placed", never as a default place. */
export function parseBoardLayout(value: unknown): BoardLayout {
  const root = object(value);
  if (root.schema !== "agent_commons.board-layout.v1") throw new BoardError("board layout schema mismatch");
  const revision = integer(root.revision, Number.MAX_SAFE_INTEGER, 0);
  const positions: Record<string, BoardPosition> = {};
  for (const [agentId, raw] of Object.entries(object(root.positions ?? {}))) {
    if (!AGENT_ID.test(agentId)) throw new BoardError("position key is not an agent id");
    const point = object(raw);
    positions[agentId] = { x: integer(point.x, COORDINATE_LIMIT), y: integer(point.y, COORDINATE_LIMIT) };
  }
  const frames: BoardFrame[] = [];
  if (!Array.isArray(root.frames ?? [])) throw new BoardError("frames must be a list");
  for (const raw of (root.frames ?? []) as unknown[]) {
    const frame = object(raw);
    if (typeof frame.id !== "string" || !FRAME_ID.test(frame.id) || typeof frame.title !== "string") throw new BoardError("frame is malformed");
    const members = Array.isArray(frame.members) ? frame.members.filter((member): member is string => typeof member === "string" && AGENT_ID.test(member)) : [];
    frames.push({
      id: frame.id, title: frame.title, x: integer(frame.x, COORDINATE_LIMIT), y: integer(frame.y, COORDINATE_LIMIT),
      width: integer(frame.width, 100_000, 1), height: integer(frame.height, 100_000, 1), members: [...new Set(members)],
      blueprintId: optionalText(frame.blueprint_id)
    });
  }
  return { revision, positions, frames };
}

/** Serialize an arrangement for the replace route, carrying the revision it was read at. */
export function layoutBody(layout: BoardLayout): { expected_revision: number; positions: Record<string, { x: number; y: number }>; frames: unknown[] } {
  return {
    expected_revision: layout.revision,
    positions: Object.fromEntries(Object.entries(layout.positions).map(([id, point]) => [id, { x: Math.round(point.x), y: Math.round(point.y) }])),
    frames: layout.frames.map((frame) => ({
      id: frame.id, title: frame.title, x: Math.round(frame.x), y: Math.round(frame.y), width: Math.max(1, Math.round(frame.width)), height: Math.max(1, Math.round(frame.height)),
      members: [...frame.members], blueprint_id: frame.blueprintId
    }))
  };
}

export function newFrameId(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return `frame.${[...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** A department frame for the roles one blueprint application created, placed below everything already on the board. */
export function frameForApplication(application: BlueprintApplication, title: string, layout: BoardLayout): BoardFrame {
  const members = [...new Set(application.roles.map((role) => role.agentId))];
  const bottom = Math.max(0, ...layout.frames.map((frame) => frame.y + frame.height), ...Object.values(layout.positions).map((point) => point.y + ROLE_NODE_HEIGHT));
  const columns = Math.max(1, Math.min(3, members.length));
  const rows = Math.max(1, Math.ceil(members.length / columns));
  return {
    id: newFrameId(), title, x: 0, y: bottom + (bottom > 0 ? 80 : 0),
    width: FRAME_PADDING * 2 + columns * ROLE_NODE_WIDTH + (columns - 1) * FRAME_PADDING,
    height: FRAME_HEADER + FRAME_PADDING * 2 + rows * ROLE_NODE_HEIGHT + (rows - 1) * FRAME_PADDING,
    members, blueprintId: application.blueprintId
  };
}

/** Positions for members inside a frame in reading order; frame-relative. */
export function positionsInsideFrame(frame: BoardFrame): Record<string, BoardPosition> {
  const columns = Math.max(1, Math.min(3, frame.members.length));
  const positions: Record<string, BoardPosition> = {};
  frame.members.forEach((member, index) => {
    positions[member] = {
      x: frame.x + FRAME_PADDING + (index % columns) * (ROLE_NODE_WIDTH + FRAME_PADDING),
      y: frame.y + FRAME_HEADER + FRAME_PADDING + Math.floor(index / columns) * (ROLE_NODE_HEIGHT + FRAME_PADDING)
    };
  });
  return positions;
}

/** Ranked auto-layout for roles that have no stored place; reporting lines flow downward. */
export function autoLayout(roleIds: readonly string[], edges: readonly Readonly<{ from: string; to: string }>[], origin: BoardPosition): Record<string, BoardPosition> {
  if (roleIds.length === 0) return {};
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: 48, ranksep: 72, marginx: 0, marginy: 0 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const id of roleIds) graph.setNode(id, { width: ROLE_NODE_WIDTH, height: ROLE_NODE_HEIGHT });
  const known = new Set(roleIds);
  for (const edge of edges) if (known.has(edge.from) && known.has(edge.to) && edge.from !== edge.to) graph.setEdge(edge.to, edge.from);
  dagre.layout(graph);
  const positions: Record<string, BoardPosition> = {};
  for (const id of roleIds) {
    const node = graph.node(id);
    positions[id] = { x: Math.round(origin.x + node.x - ROLE_NODE_WIDTH / 2), y: Math.round(origin.y + node.y - ROLE_NODE_HEIGHT / 2) };
  }
  return positions;
}

function activityOf(agentId: string, snapshot: TrackerSnapshot | null): { activity: RoleActivity; runPhase: string | null; awaitsHuman: boolean; taskIds: Set<string> } {
  const taskIds = new Set<string>();
  let runPhase: string | null = null, running = false, queued = false, awaits = false;
  if (snapshot) {
    for (const task of snapshot.tasks) if (task.suggestedAgentId === agentId) taskIds.add(task.taskId);
    for (const run of snapshot.runs) {
      if (run.agentId !== agentId) continue;
      if (run.taskId) taskIds.add(run.taskId);
      if (run.finishedAt === null) {
        runPhase = run.phase;
        if (run.awaitsHuman) awaits = true;
        if (["running", "active", "input_needed"].includes(run.phase)) running = true;
        else if (["queued", "preparing", "requested", "starting"].includes(run.phase)) queued = true;
      }
    }
    for (const task of snapshot.tasks) if (taskIds.has(task.taskId) && task.awaitsHuman) awaits = true;
  }
  return { activity: awaits ? "attention" : running ? "running" : queued ? "queued" : "idle", runPhase, awaitsHuman: awaits, taskIds };
}

const CLOSED_TASK_STATES = new Set(["accepted", "cancelled", "completed"]);

/** Join roles, graph, tracker and arrangement into what the canvas draws. Unplaced roles get an auto-layout beside the placed ones. */
export function buildBoardModel(roles: readonly RoleOption[], graph: BoardGraph | null, tracker: TrackerViewState, layout: BoardLayout): BoardModel {
  const snapshot = tracker.kind === "ready" ? tracker.snapshot : null;
  const closed = new Set(snapshot?.tasks.filter((task) => CLOSED_TASK_STATES.has(task.taskState)).map((task) => task.taskId) ?? []);
  const frameOf = new Map<string, string>();
  for (const frame of layout.frames) for (const member of frame.members) if (!frameOf.has(member)) frameOf.set(member, frame.id);
  const unplaced = roles.filter((role) => !(role.id in layout.positions) && !frameOf.has(role.id)).map((role) => role.id);
  const placedRight = Math.max(0, ...layout.frames.map((frame) => frame.x + frame.width), ...Object.values(layout.positions).map((point) => point.x + ROLE_NODE_WIDTH));
  const generated = autoLayout(unplaced, [...(graph?.reportsTo ?? []), ...(graph?.links ?? [])], { x: placedRight + (placedRight > 0 ? 96 : 0), y: 0 });
  const framePositions: Record<string, BoardPosition> = {};
  for (const frame of layout.frames) Object.assign(framePositions, positionsInsideFrame(frame));
  const nodes = roles.map((role): BoardRoleNode => {
    const state = activityOf(role.id, snapshot);
    const stored = layout.positions[role.id];
    const position = stored ?? framePositions[role.id] ?? generated[role.id] ?? { x: 0, y: 0 };
    return {
      id: role.id, name: role.name, specialization: role.specializationRef?.id ?? null, profileId: role.profileId, model: role.model ?? null, contextMode: role.contextMode,
      activity: state.activity, runPhase: state.runPhase, awaitsHuman: state.awaitsHuman,
      taskCount: state.taskIds.size, openTaskCount: [...state.taskIds].filter((taskId) => !closed.has(taskId)).length,
      position, frameId: frameOf.get(role.id) ?? null, placed: stored !== undefined || role.id in framePositions
    };
  });
  const known = new Set(roles.map((role) => role.id));
  return {
    nodes, frames: layout.frames,
    links: (graph?.links ?? []).filter((link) => known.has(link.from) && known.has(link.to)),
    reportsTo: (graph?.reportsTo ?? []).filter((edge) => known.has(edge.from) && known.has(edge.to))
  };
}

/** The frame whose area contains a role's top-left corner, if any. */
export function frameAt(frames: readonly BoardFrame[], point: BoardPosition): BoardFrame | null {
  return frames.find((frame) => point.x >= frame.x && point.y >= frame.y && point.x <= frame.x + frame.width && point.y <= frame.y + frame.height) ?? null;
}

/** Move a role: update its stored place and its frame membership from where it landed. */
export function moveRole(layout: BoardLayout, agentId: string, point: BoardPosition): BoardLayout {
  const target = frameAt(layout.frames, point);
  const frames = layout.frames.map((frame) => {
    const without = frame.members.filter((member) => member !== agentId);
    if (target && frame.id === target.id) return { ...frame, members: [...without, agentId] };
    return without.length === frame.members.length ? frame : { ...frame, members: without };
  });
  return { ...layout, positions: { ...layout.positions, [agentId]: { x: Math.round(point.x), y: Math.round(point.y) } }, frames };
}

/** Move a frame and everything stored inside it by the same offset. */
export function moveFrame(layout: BoardLayout, frameId: string, point: BoardPosition): BoardLayout {
  const frame = layout.frames.find((item) => item.id === frameId);
  if (!frame) return layout;
  const dx = Math.round(point.x) - frame.x, dy = Math.round(point.y) - frame.y;
  const positions = { ...layout.positions };
  for (const member of frame.members) {
    const current = positions[member];
    if (current) positions[member] = { x: current.x + dx, y: current.y + dy };
  }
  return { ...layout, positions, frames: layout.frames.map((item) => item.id === frameId ? { ...item, x: item.x + dx, y: item.y + dy } : item) };
}

export function addFrame(layout: BoardLayout, frame: BoardFrame): BoardLayout {
  const inside = positionsInsideFrame(frame);
  const others = layout.frames.map((item) => ({ ...item, members: item.members.filter((member) => !frame.members.includes(member)) }));
  return { ...layout, positions: { ...layout.positions, ...inside }, frames: [...others, frame] };
}

export function renameFrame(layout: BoardLayout, frameId: string, title: string): BoardLayout {
  const cleaned = title.trim().slice(0, 120);
  return cleaned ? { ...layout, frames: layout.frames.map((frame) => frame.id === frameId ? { ...frame, title: cleaned } : frame) } : layout;
}

export function removeFrame(layout: BoardLayout, frameId: string): BoardLayout {
  return { ...layout, frames: layout.frames.filter((frame) => frame.id !== frameId) };
}

/** Forget every stored place so the next build lays the roles out again; frames keep their members. */
export function resetPositions(layout: BoardLayout): BoardLayout {
  return { ...layout, positions: {} };
}
