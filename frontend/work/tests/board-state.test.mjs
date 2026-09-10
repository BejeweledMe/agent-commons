import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-board-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--outDir", compiled, resolve(root, "src/boardState.ts")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const board = await import(pathToFileURL(resolve(compiled, "boardState.js")).href);

const id = (kind, n) => `${kind}.${"0".repeat(26 - n.length)}${n}`;
const A = id("agent", "A1"), B = id("agent", "B2"), C = id("agent", "C3"), LINK = id("agent_link", "L1");
const role = (agentId, name) => ({ id: agentId, name, profileId: "claude-builder", contextMode: "fresh", model: null, specializationRef: { kind: "role", id: "frontend-engineer", source: "builtin", version: "v" } });
const graph = () => ({
  nodes: [
    { id: A, kind: "agent", state: "active", attrs: {} }, { id: B, kind: "agent", state: "active", attrs: {} },
    { id: C, kind: "agent", state: "active", attrs: { template: true } }, { id: id("agent", "R9"), kind: "agent", state: "retired", attrs: {} },
    { id: LINK, kind: "agent_link", state: "open", attrs: { allowed_action: "ask" } }, { id: id("task", "T1"), kind: "task", state: "ready", attrs: {} }
  ],
  edges: [
    { kind: "reports_to", from: B, to: A }, { kind: "link_from", from: LINK, to: A }, { kind: "link_to", from: LINK, to: B },
    { kind: "depends_on", from: id("task", "T1"), to: id("task", "T2") }
  ]
});
const layoutValue = (extra = {}) => ({ schema: "agent_commons.board-layout.v1", revision: 3, positions: { [A]: { x: 10, y: 20 } }, frames: [], ...extra });
const tracker = (tasks = [], runs = []) => ({ kind: "ready", snapshot: { tasks, runs, attention: [], edges: [] } });
const task = (taskId, agentId, state = "active", awaits = false) => ({ taskId, suggestedAgentId: agentId, taskState: state, awaitsHuman: awaits, title: taskId, roleName: null, suggestedRoleName: "Builder" });
const run = (taskId, agentId, phase, finished = null, awaits = false) => ({ delegationId: id("delegation", taskId.slice(-2)), taskId, agentId, phase, finishedAt: finished, awaitsHuman: awaits, roleName: "Builder" });

test("the board keeps only standing roles and the links between them from the workspace graph", () => {
  const parsed = board.parseBoardGraph(graph());
  assert.deepEqual(parsed.roles, [A, B], "templates and retired roles are not board nodes");
  assert.deepEqual(parsed.links, [{ id: LINK, from: A, to: B, allowedAction: "ask", state: "open" }]);
  assert.deepEqual(parsed.reportsTo, [{ from: B, to: A }]);
  assert.throws(() => board.parseBoardGraph({ nodes: "x", edges: [] }), board.BoardError);
});

test("the arrangement parses strictly and serializes with the revision it was read at", () => {
  const layout = board.parseBoardLayout(layoutValue({ frames: [{ id: "frame.0123abcd", title: "Dept", x: 0, y: 0, width: 400, height: 300, members: [A, A, "task.x"], blueprint_id: "team" }] }));
  assert.equal(layout.revision, 3);
  assert.deepEqual(layout.frames[0].members, [A], "duplicates and non-agent ids are dropped");
  assert.equal(layout.frames[0].blueprintId, "team");
  for (const bad of [{ schema: "other" }, { positions: { "agent.nope": { x: 0, y: 0 } } }, { positions: { [A]: { x: 0.5, y: 0 } } }, { frames: [{ id: "frame.x", title: "t", x: 0, y: 0, width: 1, height: 1 }] }, { revision: -1 }]) {
    assert.throws(() => board.parseBoardLayout(layoutValue(bad)), board.BoardError, JSON.stringify(bad));
  }
  const body = board.layoutBody({ ...layout, positions: { [A]: { x: 1.4, y: 2.6 } } });
  assert.equal(body.expected_revision, 3);
  assert.deepEqual(body.positions[A], { x: 1, y: 3 });
  assert.deepEqual(body.frames[0], { id: "frame.0123abcd", title: "Dept", x: 0, y: 0, width: 400, height: 300, members: [A], blueprint_id: "team" });
});

test("roles without a stored place are laid out automatically beside the placed ones, and activity comes from the tracker", () => {
  const roles = [role(A, "Lead"), role(B, "Builder")];
  const model = board.buildBoardModel(roles, board.parseBoardGraph(graph()), tracker(
    [task("task.1", B), task("task.2", B, "accepted"), task("task.3", A, "ready", true)],
    [run("task.1", B, "running")]
  ), board.parseBoardLayout(layoutValue()));
  const lead = model.nodes.find((node) => node.id === A), builder = model.nodes.find((node) => node.id === B);
  assert.deepEqual(lead.position, { x: 10, y: 20 });
  assert.equal(lead.placed, true);
  assert.equal(builder.placed, false, "an unplaced role is not pretended to be arranged");
  assert.ok(builder.position.x >= 10 + board.ROLE_NODE_WIDTH, "auto-layout lands to the right of the placed roles");
  assert.equal(builder.activity, "running");
  assert.equal(builder.taskCount, 2);
  assert.equal(builder.openTaskCount, 1, "accepted tasks are not open");
  assert.equal(lead.activity, "attention", "a task waiting on the human marks its role");
  assert.deepEqual(model.links.map((link) => link.id), [LINK]);
  const idle = board.buildBoardModel(roles, null, { kind: "loading" }, board.EMPTY_LAYOUT).nodes.find((node) => node.id === A);
  assert.equal(idle.activity, "idle");
  assert.equal(idle.taskCount, 0);
});

test("queued runs read as queued, never as running", () => {
  const model = board.buildBoardModel([role(A, "Lead")], null, tracker([], [run("task.1", A, "queued")]), board.EMPTY_LAYOUT);
  assert.equal(model.nodes[0].activity, "queued");
  const done = board.buildBoardModel([role(A, "Lead")], null, tracker([], [run("task.1", A, "succeeded", "2026-09-09T00:00:00Z")]), board.EMPTY_LAYOUT);
  assert.equal(done.nodes[0].activity, "idle", "a finished run is not activity");
});

test("a blueprint application becomes a department frame below the board with its roles inside", () => {
  const layout = board.addFrame(board.parseBoardLayout(layoutValue({ positions: { [A]: { x: 0, y: 0 } } })),
    board.frameForApplication({ blueprintId: "team-frontend", roles: [{ slotId: "s1", agentId: B }, { slotId: "s2", agentId: C }], tasks: [] }, "Frontend", board.parseBoardLayout(layoutValue({ positions: { [A]: { x: 0, y: 0 } } }))));
  const frame = layout.frames[0];
  assert.match(frame.id, /^frame\.[a-f0-9]{16}$/);
  assert.equal(frame.title, "Frontend");
  assert.deepEqual(frame.members, [B, C]);
  assert.ok(frame.y >= board.ROLE_NODE_HEIGHT, "the frame is placed below existing roles");
  const inside = board.positionsInsideFrame(frame);
  for (const member of frame.members) {
    assert.ok(layout.positions[member].x >= frame.x && layout.positions[member].y >= frame.y + board.FRAME_HEADER);
    assert.deepEqual(layout.positions[member], inside[member]);
  }
  const model = board.buildBoardModel([role(A, "Lead"), role(B, "Builder"), role(C, "Designer")], null, { kind: "loading" }, layout);
  assert.equal(model.nodes.find((node) => node.id === B).frameId, frame.id);
  assert.equal(model.nodes.find((node) => node.id === A).frameId, null);
});

test("moving a role updates its place and its frame membership; moving a frame carries its members; frames can be renamed and removed", () => {
  const frame = { id: "frame.0123abcd0123abcd", title: "Dept", x: 500, y: 500, width: 600, height: 400, members: [B], blueprintId: null };
  let layout = { revision: 1, positions: { [A]: { x: 0, y: 0 }, [B]: { x: 540, y: 560 } }, frames: [frame] };
  layout = board.moveRole(layout, A, { x: 600.4, y: 620.2 });
  assert.deepEqual(layout.positions[A], { x: 600, y: 620 });
  assert.deepEqual(layout.frames[0].members, [B, A], "a role dropped inside a frame joins it");
  layout = board.moveRole(layout, B, { x: 0, y: 0 });
  assert.deepEqual(layout.frames[0].members, [A], "a role dragged out leaves the frame");
  layout = board.moveFrame(layout, frame.id, { x: 700, y: 700 });
  assert.deepEqual(layout.positions[A], { x: 800, y: 820 }, "members move with their frame");
  assert.deepEqual(layout.positions[B], { x: 0, y: 0 });
  assert.equal(board.renameFrame(layout, frame.id, "  Platform  ").frames[0].title, "Platform");
  assert.equal(board.renameFrame(layout, frame.id, "   ").frames[0].title, "Dept", "an empty title is ignored");
  assert.deepEqual(board.removeFrame(layout, frame.id).frames, []);
  assert.deepEqual(board.resetPositions(layout).positions, {});
  assert.equal(board.resetPositions(layout).frames.length, 1);
});

test("auto-layout is deterministic and follows reporting lines downward", () => {
  const first = board.autoLayout([A, B], [{ from: B, to: A }], { x: 0, y: 0 });
  const second = board.autoLayout([A, B], [{ from: B, to: A }], { x: 0, y: 0 });
  assert.deepEqual(first, second);
  assert.ok(first[B].y > first[A].y, "the role that reports to another sits below it");
  assert.deepEqual(board.autoLayout([], [], { x: 0, y: 0 }), {});
});
