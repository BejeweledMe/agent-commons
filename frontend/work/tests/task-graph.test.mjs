import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-task-graph-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--resolveJsonModule", "--allowSyntheticDefaultImports", "--outDir", compiled,
  ...["taskGraph.ts", "mapViewport.ts", "components/MapCanvas.tsx", "components/TaskHierarchy.tsx", "taskGraphApi.ts", "taskEditorState.ts", "taskPresentation.ts", "trackerState.ts", "components/TaskGraph.tsx", "components/TaskEditor.tsx", "components/TaskViews.tsx"].map((file) => resolve(root, "src", file))
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (file) => import(pathToFileURL(resolve(compiled, file + ".js")).href);
const { buildTaskGraph, graphTaskState, wouldCreateTaskCycle, GRAPH_ICONS, MAX_GRAPH_TASKS, NODE_HEIGHT, NODE_WIDTH, selectGraphTasks, focusedTaskIds } = await load("taskGraph");
const { TaskGraphApi, parseTaskEditDetail } = await load("taskGraphApi");
const { taskEditorDraft, observeTaskEditorDraft, editorInput } = await load("taskEditorState");
const { bucketTasksForNow, filterTrackerTasks, NOW_COLUMNS } = await load("taskPresentation");
const { trackerStreamSucceeded } = await load("trackerState");
const { TaskGraph } = await load("components/TaskGraph");
const { MapViewportStore, contentSignature, fitViewport, panViewport, pointerAnchor, viewBoxAttribute, viewportKey, viewportZoom, zoomViewport, MAX_MAP_ZOOM, MIN_MAP_ZOOM } = await load("mapViewport");
const { TaskEditor } = await load("components/TaskEditor");
const { TaskViews } = await load("components/TaskViews");
const { parseTrackerSnapshot } = await load("api");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const taskId = `task.${"0".repeat(26)}`;
const otherId = `task.${"1".repeat(26)}`;
const thirdId = `task.${"2".repeat(26)}`;
const revision = `evt.${"0".repeat(26)}`;
const newer = `evt.${"1".repeat(26)}`;
const task = (id = taskId, dependencies = [], overrides = {}) => ({ taskId: id, title: `Task ${id}`, taskState: "ready", readiness: "ready", dependencyTaskIds: dependencies, blockingDependencyIds: [], ownerSessionId: null, roleName: null, provider: null, profileId: null, phase: null, awaitsHuman: false, nextAction: "start_ready_work", freshness: "fresh", evidenceState: "complete", gaps: [], ...overrides });
const edge = (from, to, prerequisiteMissing = false) => ({ prerequisiteTaskId: from, dependentTaskId: to, prerequisiteMissing });
const detail = () => ({ schema: "agent-commons.ui.task-edit.v1", task_id: taskId, revision, title: "Original task", description: "Complete original description", acceptance_criteria: ["Criterion"], dependencies: [], state: "ready", editable: true, cancellable: true, refusal_code: null });
const success = (action = "revised") => ({ schema: "agent-commons.ui.task-edit-result.v1", task_id: taskId, revision: newer, action });
const signal = () => new AbortController().signal;
const snapshotOf = (tasks, overrides = {}) => ({
  schema: "agent-commons.tracker.v1", sequence: 1, sourceRevision: `sha256:${"0".repeat(64)}`, truncated: false, state: "ready",
  tasks, edges: [], runs: [], attention: [],
  capacity: { state: "available", active: 0, limit: 4, queued: 0, queueCapacity: 8 },
  freshness: { generatedAt: "2026-09-18T00:00:00Z", sourceUpdatedAt: "2026-09-18T00:00:00Z", state: "fresh", resumeGap: false },
  focusTaskIds: [], criticalPathTaskIds: [], criticalPathBasis: "dependency_depth_only", criticalPathPredictive: false, gaps: [],
  ...overrides
});
const GROUPS = [...NOW_COLUMNS, "settled"];
const groupOf = (snapshot, id, agentId = null) => {
  const buckets = bucketTasksForNow(snapshot, agentId);
  return GROUPS.find((group) => buckets[group].some((item) => item.taskId === id)) ?? null;
};
const viewProps = (view, snapshot, overrides = {}) => ({
  view, onViewChange() {}, snapshot, visibleTasks: snapshot.tasks, buckets: bucketTasksForNow(snapshot, null),
  selectedTaskId: null, locale: "en", text: (key) => messages.en[key], onSelectTask() {}, onTaskKeyDown() {},
  registerTaskButton() {}, onClearFilters() {}, ...overrides
});
const occurrences = (html, pattern) => (html.match(pattern) ?? []).length;
const MAP = /class="task-graph[ "]/g;

test("real dependency edges lay out top to bottom, remain deterministic, and focus connected tasks", () => {
  const tasks = [task(), task(otherId, [taskId]), task(thirdId)];
  const edges = [edge(taskId, otherId)];
  const layout = buildTaskGraph(tasks, edges, otherId);
  assert.deepEqual(layout.nodes.map((node) => node.task.taskId), [taskId, otherId]);
  assert.equal(layout.focused, true);
  assert.equal(layout.edges.length, 1);
  assert.ok(layout.nodes[0].y + NODE_HEIGHT < layout.nodes[1].y);
  assert.deepEqual(buildTaskGraph([...tasks].reverse(), edges, otherId), layout);
  assert.equal(buildTaskGraph(tasks, edges, otherId, false).nodes.length, 3);
});

test("cycles, missing prerequisites and large graphs expose a real list instead of an invented DAG", () => {
  assert.equal(buildTaskGraph([task(taskId, [otherId]), task(otherId, [taskId])], [edge(taskId, otherId), edge(otherId, taskId)], taskId).invalid, true);
  assert.equal(buildTaskGraph([task(taskId, [otherId])], [edge(otherId, taskId, true)], taskId).invalid, true);
  const tasks = Array.from({ length: 129 }, (_, index) => task(`task.${index}`));
  assert.equal(buildTaskGraph(tasks, [], null).tooLarge, true);
  const html = renderToStaticMarkup(createElement(TaskGraph, { tasks, edges: [], selectedTaskId: null, onSelectTask() {}, locale: "en" }));
  assert.match(html, /task-graph-fallback/);
  assert.equal((html.match(/aria-pressed="false"/g) ?? []).length, 129);
  assert.equal(wouldCreateTaskCycle([task(), task(otherId, [taskId])], taskId, [otherId]), true);
  assert.equal(wouldCreateTaskCycle([task(), task(otherId, [taskId])], otherId, [taskId]), false);
});

test("accepted, completed and running are separate facts with status text and icons", () => {
  assert.equal(graphTaskState(task(taskId, [], { taskState: "completed", phase: "succeeded" })), "completed");
  assert.equal(graphTaskState(task(taskId, [], { taskState: "accepted" })), "accepted");
  assert.equal(graphTaskState(task(taskId, [], { phase: "running" })), "active");
  assert.equal(graphTaskState(task(taskId, [otherId], { blockingDependencyIds: [otherId] })), "queued");
  assert.notEqual(GRAPH_ICONS.accepted, GRAPH_ICONS.completed);
  const css = readFileSync(resolve(root, "src/taskGraph.css"), "utf8");
  // Only accepted nodes use acceptance tokens; completed work has neutral colours.
  assert.match(css, /\[data-graph-state="accepted"\][^{]*\{ background:var\(--status-accepted-surface\); color:var\(--status-accepted-text\); border-color:var\(--status-accepted\);/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "no raw colour in the graph stylesheet"); assert.match(css, /double/);
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(TaskGraph, { tasks: [task(taskId, [], { title: "<script>unsafe</script>", roleName: "Frontender", provider: "claude", phase: "running" })], edges: [], selectedTaskId: taskId, onSelectTask() {}, locale }));
    assert.match(html, /Frontender · claude/); assert.ok(html.includes(messages[locale].tracker_gloss_running));
    assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /style=|<script>/);
    for (const state of ["accepted", "completed", "active", "ready", "queued", "blocked"]) assert.match(html, new RegExp(`data-graph-state="${state}"`));
    assert.match(html, /aria-pressed="true"/);
  }
});

test("graph planned role hints remain distinct from the actual responsible role", () => {
  const props = { tasks: [task(taskId, [], { suggestedAgentId: `agent.${"0".repeat(26)}`, suggestedRoleName: "Frontend engineer", suggestedProvider: "claude" })], edges: [], selectedTaskId: taskId, onSelectTask() {}, locale: "en" };
  const planned = renderToStaticMarkup(createElement(TaskGraph, props));
  assert.match(planned, /Suggested agent: Frontend engineer · claude/);
  assert.match(planned, /No current run/);
  const active = renderToStaticMarkup(createElement(TaskGraph, { ...props, tasks: [{ ...props.tasks[0], roleName: "Actual builder", provider: "codex", phase: "running" }] }));
  assert.match(active, /Actual builder · codex/);
  assert.doesNotMatch(active, /Suggested agent: Frontend engineer/);
});

test("tracker suggestion parser requires a complete bounded metadata triple", () => {
  const payload = { schema: "agent-commons.tracker.v1", sequence: 1, source_revision: `sha256:${"0".repeat(64)}`, truncated: false, state: "ready", tasks: [{
    task_id: taskId, title: "Task", task_state: "ready", readiness: "ready", dependency_task_ids: [], blocking_dependency_ids: [], owner_session_id: null, role_name: null, provider: null, profile_id: null, phase: null, awaits_human: false, next_action: "start_ready_work", freshness: "fresh", evidence_state: "complete", gaps: [],
  }], edges: [], runs: [], attention: [], capacity: { state: "available", active: 0, limit: 4, queued: 0, queue_capacity: 8 }, freshness: { generated_at: "2026-09-07T00:00:00Z", source_updated_at: "2026-09-07T00:00:00Z", state: "fresh", resume_gap: false }, focus_task_ids: [], critical_path_task_ids: [], critical_path_basis: "dependency_depth_only", critical_path_predictive: false, gaps: [] };
  const absent = parseTrackerSnapshot(payload).tasks[0];
  assert.equal(absent.suggestedAgentId, null); assert.equal(absent.roleName, null);
  const hint = { suggested_agent_id: `agent.${"0".repeat(26)}`, suggested_role_name: "Frontend engineer", suggested_provider: "claude" };
  const withHint = (suggestion) => ({ ...payload, tasks: [{ ...payload.tasks[0], ...suggestion }] });
  const parsed = parseTrackerSnapshot(withHint({ ...hint, system_prompt: "hidden" })).tasks[0];
  assert.equal(parsed.suggestedRoleName, "Frontend engineer"); assert.equal(parsed.suggestedProvider, "claude"); assert.equal(parsed.roleName, null); assert.equal(parsed.ownerSessionId, null);
  assert.equal(JSON.stringify(parsed).includes("hidden"), false);
  for (const invalid of [
    { suggested_agent_id: hint.suggested_agent_id }, { ...hint, suggested_provider: "arbitrary" },
    { ...hint, suggested_agent_id: "agent.bad" }, { ...hint, suggested_role_name: "Я".repeat(81) },
    { ...hint, suggested_role_name: "unsafe\nlabel" }, { suggested_agent_id: null, suggested_role_name: null, suggested_provider: null },
  ]) assert.throws(() => parseTrackerSnapshot(withHint(invalid)));
});

test("completed uses actual neutral colours and readable text; acceptance alone is green", () => {
  // The node reads its colours from the :root tokens; resolve them and measure the pair.
  const rootCss = readFileSync(resolve(root, "src/styles.css"), "utf8");
  const token = (name) => rootCss.match(new RegExp(`--${name}:\\s*#([0-9a-fA-F]{6})`))[1];
  const luminance = (hex) => [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const css = readFileSync(resolve(root, "src/taskGraph.css"), "utf8");
  const completed = css.match(/\.task-graph-node\[data-graph-state="completed"\]\s*\{([^}]+)\}/)[1];
  const accepted = css.match(/\.task-graph-node\[data-graph-state="accepted"\]\s*\{([^}]+)\}/)[1];
  const property = (rule, name) => rule.match(new RegExp(`${name}:\\s*var\\(--([a-z0-9-]+)\\)`))[1];
  assert.equal(property(completed, "background"), "surface-2");
  assert.equal(property(completed, "color"), "text-1");
  assert.equal(property(completed, "border-color"), "border-2");
  assert.doesNotMatch(completed, /status-accepted/);
  assert.notEqual(token(property(completed, "background")), token(property(accepted, "background")));
  assert.notEqual(token(property(completed, "border-color")), token(property(accepted, "border-color")));
  const text = luminance(token(property(completed, "color"))), surface = luminance(token(property(completed, "background")));
  assert.ok((Math.max(text, surface) + 0.05) / (Math.min(text, surface) + 0.05) >= 4.5);
});

test("editor owns complete bounded fields and refuses contradictory permissions", () => {
  const source = { ...detail(), actor: { private: "hidden" }, raw_provider_output: "hidden" };
  const parsed = parseTaskEditDetail(source, taskId);
  source.acceptance_criteria[0] = "Mutated";
  assert.deepEqual(parsed.criteria, ["Criterion"]);
  assert.equal(JSON.stringify(parsed).includes("hidden"), false);
  for (const mutation of [
    (value) => { value.task_id = otherId; }, (value) => { value.revision = "latest"; },
    (value) => { value.dependencies = [otherId, otherId]; }, (value) => { value.state = "succeeded"; },
    (value) => { value.state = "completed"; }, (value) => { value.refusal_code = "task_live_work"; },
    (value) => { value.description = "x".repeat(16001); }, (value) => { value.acceptance_criteria = []; },
  ]) { const value = detail(); mutation(value); assert.throws(() => parseTaskEditDetail(value, taskId)); }
});

test("SSE updates retain task selection and exact draft revision, marking changed content stale", () => {
  const original = parseTaskEditDetail(detail(), taskId);
  const draft = { ...taskEditorDraft(original), title: "Unsaved operator text" };
  assert.equal(observeTaskEditorDraft(draft, original), draft);
  const changed = observeTaskEditorDraft(draft, { ...original, revision: newer, title: "New remote text" });
  assert.equal(changed.title, "Unsaved operator text"); assert.equal(changed.revision, revision); assert.equal(changed.stale, true);
  const selectedTaskId = taskId;
  const snapshot = { sequence: 1, tasks: [task()], sourceRevision: "first", freshness: { state: "fresh", resumeGap: false } };
  const state = trackerStreamSucceeded({ kind: "ready", snapshot, connection: "connected" }, { ...snapshot, sequence: 2, tasks: [task(taskId, [], { phase: "running" })] });
  assert.equal(state.kind, "ready"); assert.equal(state.snapshot.tasks[0].taskId, selectedTaskId);
  assert.equal(buildTaskGraph(snapshot.tasks, [], selectedTaskId).nodes[0].task.taskId, buildTaskGraph(state.snapshot.tasks, [], selectedTaskId).nodes[0].task.taskId);
});

test("editing a title preserves untouched multiline criteria and exact full description", () => {
  const source = parseTaskEditDetail({ ...detail(), description: "  Exact description\n", acceptance_criteria: ["One criterion\nwith two lines", "Another criterion"] }, taskId);
  const input = editorInput({ ...taskEditorDraft(source), title: "Changed title" });
  assert.deepEqual(input.criteria, source.criteria);
  assert.equal(input.description, source.description);
});

test("unknown save outcome retries the frozen body and revision without a refresh or new key", async () => {
  const requests = [];
  const client = new TaskGraphApi({ async requestData(path, options) {
    requests.push({ path, ...options });
    return requests.length === 1 ? { committed: true } : success();
  } });
  const input = { title: "Task", description: "Description", criteria: ["Criterion"], dependencyIds: [otherId] };
  await assert.rejects(() => client.save(taskId, revision, input, "same-key", signal()));
  assert.deepEqual(await client.save(taskId, revision, input, "same-key", signal()), { taskId, revision: newer, action: "revised" });
  assert.equal(requests.length, 2); assert.equal(requests[0].body, requests[1].body);
  assert.equal(requests[1].body.expected_revision, revision); assert.equal(requests[1].method, "POST");
  await assert.rejects(() => client.save(taskId, newer, input, "same-key", signal()));
  assert.equal(requests.length, 2);
});

test("cancellation keeps explicit reason, old revision and same request after an unknown outcome", async () => {
  const bodies = [];
  const client = new TaskGraphApi({ async requestData(path, options) {
    assert.match(path, /\/cancel$/); bodies.push(options.body);
    if (bodies.length === 1) throw new TypeError("connection lost");
    return success("cancelled");
  } });
  await assert.rejects(() => client.cancel(taskId, revision, "No longer needed", "cancel-key", signal()));
  await client.cancel(taskId, revision, "No longer needed", "cancel-key", signal());
  assert.equal(bodies[0], bodies[1]);
  assert.deepEqual(bodies[1], { expected_revision: revision, reason: "No longer needed", idempotency_key: "cancel-key" });
});

test("an inspector remount can recover a retained unknown-outcome edit with its exact intent", async () => {
  const input = { title: "Unsaved retained title", description: "Retained description", criteria: ["Criterion"], dependencyIds: [] };
  const intent = { kind: "edit", taskId, revision, input, key: "retained-key" };
  const source = parseTaskEditDetail(detail(), taskId);
  const entries = { [taskId]: { mode: "edit", detail: source, draft: { ...taskEditorDraft(source), title: input.title, description: input.description }, dependent: null, reason: "", loading: false, replacing: false, loadFailed: false, action: { kind: "failure", code: "request_unavailable", uncertain: true, intent } } };
  const props = { api: {}, task: task(), tasks: [task()], sourceRevision: "source", locale: "en", writesEnabled: true, actionsCurrent: true, onChanged() {}, onSelectTask() {}, entries, setEntries() {} };
  const firstMount = renderToStaticMarkup(createElement(TaskEditor, props));
  const secondMount = renderToStaticMarkup(createElement(TaskEditor, props));
  assert.equal(firstMount, secondMount);
  assert.match(secondMount, /Unsaved retained title/); assert.match(secondMount, /disabled=""/);
  assert.match(secondMount, /Retry the same action/);
  const bodies = [];
  const transport = { async requestData(_path, options) { bodies.push(JSON.stringify(options.body)); return success(); } };
  await new TaskGraphApi(transport).save(intent.taskId, intent.revision, intent.input, intent.key, signal());
  await new TaskGraphApi(transport).save(intent.taskId, intent.revision, intent.input, intent.key, signal());
  assert.equal(bodies[0], bodies[1]);
  const owner = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  assert.match(owner, /useState<TaskEditorEntries>/);
  assert.match(owner, /entries=\{editorEntries\} setEntries=\{setEditorEntries\}/);
  assert.match(owner, /detailTaskRef\.current === detailTask\.taskId/);
});

const TASK_STATES = ["ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"];
const READINESS = ["ready", "blocked", "terminal_dependency_failure", "policy_unknown", "in_progress", "human_attention", "complete", "cancelled", "unknown"];
const RUN_PHASES = [null, "requested", "reserved", "launching", "running", "cancellation_requested", "input_needed", "succeeded", "failed", "cancelled", "timed_out", "needs_operator", "unknown"];

test("Now buckets partition every task state, readiness and run phase into exactly one group", () => {
  const fixture = [];
  for (const taskState of TASK_STATES) for (const readiness of READINESS) for (const phase of RUN_PHASES) for (const awaitsHuman of [false, true]) {
    fixture.push(task(`task.${fixture.length}`, [], { taskState, readiness, phase, awaitsHuman }));
  }
  const snapshot = snapshotOf(fixture);
  const buckets = bucketTasksForNow(snapshot, null);
  const placed = GROUPS.flatMap((group) => buckets[group].map((item) => item.taskId));
  assert.equal(placed.length, fixture.length, "no task is dropped");
  assert.equal(new Set(placed).size, fixture.length, "and none is counted twice");
  assert.deepEqual(NOW_COLUMNS, ["needs_you", "in_progress", "next"], "the column order is fixed");
  assert.deepEqual(bucketTasksForNow(snapshot, null), buckets, "bucketing is pure and deterministic");
  for (const group of GROUPS) {
    const positions = buckets[group].map((item) => fixture.findIndex((entry) => entry.taskId === item.taskId));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b), `${group} keeps the snapshot order`);
  }
});

test("Now columns answer the owner's three questions and keep finished work out of them", () => {
  const at = (overrides, extra = {}) => groupOf(snapshotOf([task(taskId, [], overrides)], extra), taskId);
  assert.equal(at({ awaitsHuman: true }), "needs_you");
  assert.equal(at({ taskState: "review" }), "needs_you");
  assert.equal(at({ taskState: "completed" }), "needs_you");
  for (const phase of ["input_needed", "needs_operator", "failed", "timed_out"]) assert.equal(at({ phase }), "needs_you", phase);
  assert.equal(at({}, { attention: [{ kind: "review", itemId: "review.1", taskId, reasonCode: "review_requested", nextAction: "accept_task" }] }), "needs_you");
  assert.equal(at({ taskState: "active" }), "in_progress");
  for (const phase of ["requested", "reserved", "launching", "running", "cancellation_requested"]) assert.equal(at({ phase }), "in_progress", phase);
  for (const taskState of ["ready", "assigned"]) assert.equal(at({ taskState }), "next", taskState);
  assert.equal(at({ taskState: "blocked", readiness: "blocked", blockingDependencyIds: [otherId] }), "next");
  // Accepted and cancelled work is finished: no column claims it, so the Now
  // view never shows it and green stays with acceptance in "All tasks" alone.
  for (const taskState of ["accepted", "cancelled"]) {
    assert.equal(at({ taskState }), "settled", taskState);
    assert.equal(at({ taskState, awaitsHuman: true, phase: "failed" }), "settled", `${taskState} stays settled`);
  }
});

test("the role filter narrows the Now columns exactly as it narrows the full list", () => {
  const agentId = `agent.${"0".repeat(26)}`;
  const snapshot = snapshotOf([task(taskId, [], { suggestedAgentId: agentId }), task(otherId, [], { taskState: "active" }), task(thirdId)], {
    runs: [{ delegationId: "delegation.1", taskId: otherId, agentId, roleName: "Builder", provider: "claude", profileId: "claude-builder",
      phase: "running", attemptId: null, attemptNumber: 1, startedAt: null, updatedAt: null, finishedAt: null, durationSeconds: null,
      wallTimeSeconds: null, awaitsHuman: false, nextAction: "wait_for_run", freshness: "fresh", evidenceState: "complete" }]
  });
  const buckets = bucketTasksForNow(snapshot, agentId);
  assert.deepEqual(GROUPS.flatMap((group) => buckets[group].map((item) => item.taskId)).sort(), [taskId, otherId]);
  assert.deepEqual(filterTrackerTasks(snapshot, "all", "", agentId).map((item) => item.taskId).sort(), [taskId, otherId]);
  assert.equal(groupOf(snapshot, thirdId, agentId), null, "another role's task is outside every column");
});

test("each Tasks view has one render path and Map scopes capacity after mounting", () => {
  // Every column is populated, so an empty column can never hide a missing list.
  // Ids must be ULID-shaped: the task list renders an OutputsButton per row and its scope parser rejects anything else.
  const many = Array.from({ length: 300 }, (_, index) => task(`task.${String(index).padStart(26, "0")}`, [], { taskState: ["ready", "active", "review"][index % 3] }));
  assert.ok(many.length > MAX_GRAPH_TASKS);
  const large = snapshotOf(many);
  const now = renderToStaticMarkup(createElement(TaskViews, viewProps("now", large)));
  assert.equal(occurrences(now, /class="task-list"/g), 0);
  assert.equal(occurrences(now, MAP), 0);
  assert.equal(occurrences(now, /class="now-column-list"/g), 3, "three columns, one list each");
  const map = renderToStaticMarkup(createElement(TaskViews, viewProps("map", large)));
  assert.equal(occurrences(map, MAP), 1, "Map owns selection and capacity");
  assert.equal(occurrences(map, /class="task-graph-fallback"/g), 1, "one fallback represents the visible selection");
  assert.equal(occurrences(map, /class="task-list"/g), 0, "the outer list does not duplicate Map");
  assert.doesNotMatch(map, /<svg/, "the oversized full selection stays a list");
  assert.ok(map.includes(messages.en["task_graph.graphLimit"]));
  const graph = renderToStaticMarkup(createElement(TaskViews, viewProps("map", snapshotOf(many.slice(0, 4)))));
  assert.equal(occurrences(graph, MAP), 1);
  assert.equal(occurrences(graph, /class="task-list"/g), 0, "under capacity the graph is the only render path");
  const all = renderToStaticMarkup(createElement(TaskViews, viewProps("all", large)));
  assert.equal(occurrences(all, /class="task-list"/g), 1);
  assert.equal(occurrences(all, MAP), 0);
  assert.equal(occurrences(all, /class="now-column-list"/g), 0);
  const filtered = renderToStaticMarkup(createElement(TaskViews, { ...viewProps("all", large), visibleTasks: [] }));
  assert.equal(occurrences(filtered, /class="task-list"/g), 0);
  assert.ok(filtered.includes(messages.en.task_view_clear_filter));
});

test("the three views are one accessible tab strip with EN and RU copy for every column", () => {
  for (const locale of ["en", "ru"]) {
    const text = (key) => messages[locale][key];
    const html = renderToStaticMarkup(createElement(TaskViews, { ...viewProps("now", snapshotOf([task()])), locale, text }));
    assert.match(html, /role="tablist"/);
    assert.equal(occurrences(html, /role="tab"/g), 3);
    assert.equal(occurrences(html, /aria-selected="true"/g), 1);
    assert.equal(occurrences(html, /tabindex="-1"/g), 2, "one tab stop for the whole strip");
    assert.match(html, /role="tabpanel"/);
    assert.match(html, /aria-labelledby="task-view-tab-now"/);
    assert.match(html, /aria-controls="task-view-panel"/);
    for (const key of ["task_view_tabs_label", "task_view_tab_now", "task_view_tab_map", "task_view_tab_all",
      "task_view_now_needs_you", "task_view_now_in_progress", "task_view_now_next", "tracker_keyboard_help"]) {
      assert.ok(html.includes(messages[locale][key]), `${locale}.${key}`);
    }
    const empty = renderToStaticMarkup(createElement(TaskViews, { ...viewProps("now", snapshotOf([])), locale, text }));
    for (const key of ["task_view_now_needs_you_empty", "task_view_now_in_progress_empty", "task_view_now_next_empty"]) {
      assert.ok(empty.includes(messages[locale][key]), `${locale}.${key}`);
    }
    const settled = renderToStaticMarkup(createElement(TaskViews, { ...viewProps("now", snapshotOf([task(taskId, [], { taskState: "accepted" })])), locale, text }));
    assert.ok(settled.includes(messages[locale].task_view_now_settled), `${locale} names where finished work stays`);
    assert.doesNotMatch(settled, /class="now-column-list"/, "an accepted task is in no column");
  }
});


test("explicit branch selection can bound a 304-task graph, while selection alone never narrows the map", () => {
  const many = Array.from({ length: 304 }, (_, index) => task(`task.${String(index).padStart(26, "0")}`));
  many[1] = { ...many[1], dependencyTaskIds: [many[0].taskId] };
  const edges = [edge(many[0].taskId, many[1].taskId)];
  const focused = buildTaskGraph(many, edges, many[1].taskId);
  assert.equal(focused.tooLarge, false);
  assert.equal(focused.total, 304);
  assert.deepEqual(focused.nodes.map((node) => node.task.taskId), [many[0].taskId, many[1].taskId]);
  const snapshot = snapshotOf(many, { edges });
  const html = renderToStaticMarkup(createElement(TaskViews, { ...viewProps("map", snapshot), selectedTaskId: many[1].taskId }));
  assert.doesNotMatch(html, /<svg/);
  assert.ok(html.includes(messages.en["task_graph.graphLimit"]));
  assert.equal(occurrences(html.slice(html.indexOf('class="task-graph-fallback"')), /<li>/g), 304, "the full untruncated list remains available until explicit focus/search");
  const chain = many.slice(0, 129).map((item, index, all) => ({ ...item, dependencyTaskIds: index ? [all[index - 1].taskId] : [] }));
  const chainEdges = chain.slice(1).map((item, index) => edge(chain[index].taskId, item.taskId));
  const oversized = buildTaskGraph(chain, chainEdges, chain[0].taskId);
  assert.equal(oversized.tooLarge, true);
  assert.equal(oversized.selectedTasks.length, 129);
  assert.equal(buildTaskGraph(chain, chainEdges, chain[0].taskId, "upstream").nodes.length, 1);
});

test("focus and search follow server edges, preserve direction and expose hidden prerequisites", () => {
  const fourthId = `task.${"3".repeat(26)}`;
  const tasks = [task(taskId, [], { title: "Foundation" }), task(otherId, [], { title: "API service" }), task(thirdId, [], { title: "Frontend", roleName: "Designer" }), task(fourthId, [], { title: "Release" })];
  const edges = [edge(taskId, otherId), edge(taskId, thirdId), edge(otherId, fourthId), edge(thirdId, fourthId)];
  assert.deepEqual([...focusedTaskIds(tasks, edges, otherId, "upstream")].sort(), [taskId, otherId]);
  assert.deepEqual([...focusedTaskIds(tasks, edges, otherId, "downstream")].sort(), [otherId, fourthId]);
  assert.equal(selectGraphTasks(tasks, edges, otherId, "connected").length, 4);
  assert.deepEqual(selectGraphTasks(tasks, edges, otherId, "connected", "DESIGNER").map((item) => item.taskId), [thirdId]);
  assert.deepEqual(selectGraphTasks(tasks, edges, null, "all", " API ").map((item) => item.taskId), [otherId]);
  assert.equal(selectGraphTasks(tasks, edges, null, "all", "not found").length, 0);
  const filtered = buildTaskGraph(tasks, edges, null, "all", "Release");
  assert.equal(filtered.invalid, false);
  assert.equal(filtered.hiddenPrerequisites, 2);
  assert.equal(filtered.nodes[0].hiddenPrerequisites, 2);
  assert.equal(filtered.edges.length, 0, "hidden prerequisites are counted, not drawn as imaginary nodes");
  const upstream = buildTaskGraph(tasks, edges, otherId, "upstream");
  assert.equal(upstream.hiddenPrerequisites, 0);
  const downstream = buildTaskGraph(tasks, edges, otherId, "downstream");
  assert.equal(downstream.hiddenPrerequisites, 2);
  assert.equal(buildTaskGraph(tasks, [], otherId).nodes.length, 1, "task metadata cannot fabricate server edges");
  const missing = buildTaskGraph([tasks[1]], [edge(taskId, otherId, true)], null);
  assert.equal(missing.invalid, true);
  assert.equal(missing.hiddenPrerequisites, 0, "missing is a fault, not merely filtered");
});

test("diamond and wide layers have no overlap or backwards wrapping and stable routed edges", () => {
  const ids = Array.from({ length: 8 }, (_, index) => `task.${String(index).padStart(26, "0")}`);
  const tasks = ids.map((id) => task(id));
  const edges = [...ids.slice(1, 7).map((id) => edge(ids[0], id)), ...ids.slice(1, 7).map((id) => edge(id, ids[7]))];
  const layout = buildTaskGraph(tasks, edges, null);
  assert.deepEqual(buildTaskGraph([...tasks].reverse(), [...edges].reverse(), null), layout);
  assert.equal(layout.edges.length, edges.length);
  const byId = new Map(layout.nodes.map((node) => [node.task.taskId, node]));
  for (const connection of edges) assert.ok(byId.get(connection.prerequisiteTaskId).y + NODE_HEIGHT < byId.get(connection.dependentTaskId).y);
  assert.equal(new Set(layout.nodes.filter((node) => node.rank === 1).map((node) => node.y)).size, 1, "six peers stay on one dependency layer");
  for (let i = 0; i < layout.nodes.length; i++) for (let j = i + 1; j < layout.nodes.length; j++) {
    const a = layout.nodes[i], b = layout.nodes[j];
    assert.ok(a.x + NODE_WIDTH <= b.x || b.x + NODE_WIDTH <= a.x || a.y + NODE_HEIGHT <= b.y || b.y + NODE_HEIGHT <= a.y, "node rectangles never overlap");
  }
  assert.ok(layout.edges.every((connection) => /^M [0-9.]+ [0-9.]+ L /.test(connection.path)), "use layout routing points");
});

test("the map is a numeric viewBox the operator pans and zooms, with no inline style", () => {
  const frame = { width: 800, height: 600 };
  const content = { width: 1600, height: 1200 };
  const fitted = fitViewport(content, frame);
  assert.equal(Math.round(viewportZoom(fitted, frame) * 100), 50, "fit shows the whole content");
  assert.deepEqual([Math.round(fitted.width), Math.round(fitted.height)], [1600, 1200]);
  // Zoom is bounded, and a bad number never produces a broken rectangle.
  assert.equal(viewportZoom(zoomViewport(fitted, frame, 99, { x: 0.5, y: 0.5 }, content), frame), MAX_MAP_ZOOM);
  assert.equal(viewportZoom(zoomViewport(fitted, frame, 0, { x: 0.5, y: 0.5 }, content), frame), MIN_MAP_ZOOM);
  assert.equal(viewportZoom(zoomViewport(fitted, frame, NaN, { x: 0.5, y: 0.5 }, content), frame), 1);
  // Pinching around the pointer keeps the point under it in place.
  const anchor = { x: 0.25, y: 0.75 };
  const before = { x: fitted.x + anchor.x * fitted.width, y: fitted.y + anchor.y * fitted.height };
  const zoomed = zoomViewport(fitted, frame, 2, anchor, content);
  assert.equal(Math.round(zoomed.x + anchor.x * zoomed.width), Math.round(before.x));
  assert.equal(Math.round(zoomed.y + anchor.y * zoomed.height), Math.round(before.y));
  assert.equal(pointerAnchor({ clientX: 300, clientY: 450 }, { left: 100, top: 150, width: 800, height: 600 }).x, 0.25);
  // Two-finger panning moves the rectangle, bounded to a half frame of overscroll.
  const panned = panViewport(zoomed, frame, 40, -20, content);
  assert.ok(panned.x > zoomed.x && panned.y < zoomed.y);
  assert.ok(panViewport(zoomed, frame, -100000, 0, content).x >= -zoomed.width * 0.5 - 1);
  assert.ok(panViewport(zoomed, frame, 100000, 0, content).x <= content.width + zoomed.width * 0.5 + 1);
  assert.equal(viewBoxAttribute({ x: 1.006, y: -2, width: 3, height: 4 }), "1.01 -2 3 4");
  // One reading position per project, map kind and drawing; a refresh that
  // changes nothing about the drawing keeps it.
  const store = new MapViewportStore();
  const signature = contentSignature({ count: 3, width: 100.4, height: 50.6, first: taskId });
  assert.equal(signature, contentSignature({ count: 3, width: 100, height: 51, first: taskId }));
  assert.notEqual(signature, contentSignature({ count: 4, width: 100, height: 51, first: taskId }));
  const key = viewportKey({ projectId: "project.one", kind: "dependencies", focus: signature });
  assert.notEqual(key, viewportKey({ projectId: "project.one", kind: "structure", focus: signature }));
  assert.equal(store.get(key), null);
  store.set(key, panned);
  assert.deepEqual(store.get(key), panned);
  store.clear(key);
  assert.equal(store.get(key), null);
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(TaskGraph, { tasks: [task(otherId)], edges: [edge(taskId, otherId)], selectedTaskId: otherId, onSelectTask() {}, locale }));
    for (const key of ["search", "focusLabel", "upstream", "downstream", "zoomIn", "zoomOut", "fit", "reset", "gestures", "edgeMeaning", "hiddenPrerequisites", "list"]) assert.ok(html.includes(messages[locale][`task_graph.${key}`]), `${locale}.${key}`);
    assert.match(html, /type="search"/);
    assert.match(html, /<select/);
    assert.match(html, /<details/);
    assert.match(html, /viewBox="/);
    assert.match(html, /class="map-canvas-frame"/);
    assert.match(html, /role="region"/);
    assert.match(html, /tabindex="0"/);
    assert.doesNotMatch(html, /style=|<script/);
    // The canvas is not a 64vh rectangle with its own scrollbars any more.
    assert.doesNotMatch(html, /task-graph-scroll/);
  }
  const structure = renderToStaticMarkup(createElement(TaskHierarchy, { tasks: [task(taskId, [], { parentTaskId: null })], selectedTaskId: null, onSelectTask() {}, text: (key) => messages.en[key] }));
  assert.match(structure, /class="map-canvas-frame"/, "Structure gets the same gestures as Dependencies");
  assert.doesNotMatch(structure, /style=/);
  const css = readFileSync(resolve(root, "src/taskGraph.css"), "utf8");
  assert.doesNotMatch(css, /max-height:\s*64vh/, "the map takes the height it is given");
  assert.match(css, /\.map-canvas-frame \{[^}]*touch-action:none/);
  assert.match(css, /\.map-canvas-svg \{[^}]*height:100%/);
  // The wheel listener is non-passive and scoped to the canvas element, so
  // preventDefault never reaches the page or the project chat.
  const canvas = readFileSync(resolve(root, "src/components/MapCanvas.tsx"), "utf8");
  assert.match(canvas, /element\.addEventListener\("wheel", onWheel, \{ passive: false \}\)/);
  assert.equal((canvas.match(/preventDefault\(\)/g) ?? []).length > 0, true);
  assert.doesNotMatch(canvas, /window\.addEventListener\("wheel"|document\.addEventListener\("wheel"/);
  assert.match(canvas, /event\.ctrlKey \|\| event\.metaKey/, "a trackpad pinch arrives as a ctrl-wheel");
});

const { hierarchyRows, hierarchyWouldCycle } = await load("taskHierarchy");
const { TaskHierarchy } = await load("components/TaskHierarchy");
test("structure is explicit, dependency-independent, cycle-safe, and survives filtered parents", () => {
  const nodes = [task(taskId, [otherId], { taskKind: "component", parentTaskId: null }), task(otherId, [], { taskKind: "task", parentTaskId: taskId }), task(thirdId, [], { parentTaskId: otherId })];
  assert.deepEqual(hierarchyRows(nodes).rows.map((row) => row.depth), [0, 1, 2]);
  assert.equal(hierarchyWouldCycle(nodes, taskId, thirdId), true);
  assert.equal(hierarchyWouldCycle(nodes, thirdId, taskId), false);
  assert.equal(hierarchyRows(nodes.slice(1)).rows[0].parentOutside, true);
  const cycle = hierarchyRows([nodes[0], { ...nodes[1], parentTaskId: otherId }]);
  assert.equal(cycle.invalid, true);
  assert.equal(cycle.rows.length, 2);
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(TaskHierarchy, { tasks: nodes, selectedTaskId: otherId, onSelectTask() {}, text: (key) => messages[locale][key] }));
    assert.match(html, /<svg/);
    assert.match(html, /task-hierarchy-edges/);
    assert.match(html, /task-hierarchy-root/);
    assert.doesNotMatch(html, /style=/);
    assert.match(html, /aria-pressed="true"/);
    assert.ok(html.includes(messages[locale].hierarchy_component));
    assert.ok(html.includes(messages[locale].hierarchy_help));
  }
});

test("parent DTO validates typed IDs and draft/retry preserves explicit detach", async () => {
  assert.throws(() => parseTaskEditDetail({ ...detail(), parent_task_id: "not-a-task" }, taskId));
  assert.throws(() => parseTaskEditDetail({ ...detail(), task_kind: "directory" }, taskId));
  const draft = taskEditorDraft(parseTaskEditDetail({ ...detail(), parent_task_id: otherId, task_kind: "component" }, taskId));
  assert.equal(editorInput(draft).parentTaskId, otherId);
  const requests = [];
  const api = new TaskGraphApi({ async requestData(path, options) { requests.push(options.body); return success(); } });
  await api.save(taskId, revision, editorInput({ ...draft, parentTaskId: null }), "detach", signal());
  assert.equal(requests[0].changes.parent_task_id, null);
  assert.equal("task_kind" in requests[0].changes, false);
});

const { buildHierarchyGraph } = await load("taskHierarchy");
test("containment diagram uses explicit parents and a presentation-only project root", () => {
  const parent = task(taskId, [thirdId], { parentTaskId: null, taskKind: "component" });
  const child = task(otherId, [thirdId], { parentTaskId: taskId });
  const unrelated = task(thirdId, [], { parentTaskId: null });
  const graph = buildHierarchyGraph([parent, child, unrelated], taskId);
  assert.deepEqual(graph.edges.map(({from, to}) => [from, to]), [["project-root", taskId], [taskId, otherId]]);
  assert.equal(graph.nodes.filter(({task}) => task === null).length, 1);
  assert.equal(graph.nodes.some(({id}) => id === thirdId), false, "dependencies never select containment nodes");
  const p = graph.nodes.find(({id}) => id === taskId), c = graph.nodes.find(({id}) => id === otherId);
  assert.ok(p.y + NODE_HEIGHT < c.y);
  const boundary = buildHierarchyGraph([child], otherId);
  assert.equal(boundary.hiddenParents, 1);
  assert.deepEqual(boundary.edges, [], "hidden parents never turn into project-root edges");
  assert.equal(buildHierarchyGraph([{...parent, parentTaskId: otherId}, child], null).invalid, true);
});

test("containment diagram applies branch/search before cap and remains bounded at 1/20/128/304", () => {
  const nodes = Array.from({length: 304}, (_, index) => task(`task.${String(index).padStart(26, "0")}`, [], { parentTaskId: null }));
  for (const count of [1, 20, 128]) {
    const layout = buildHierarchyGraph(nodes.slice(0, count), null);
    assert.equal(layout.tooLarge, false);
    assert.equal(layout.nodes.length, count + 1);
  }
  assert.equal(buildHierarchyGraph(nodes, null).tooLarge, true);
  assert.equal(buildHierarchyGraph(nodes, nodes[200].taskId).nodes.length, 2);
  const search = buildHierarchyGraph(nodes, null, false, nodes[200].taskId);
  assert.equal(search.nodes.length, 2);
  const largeHtml = renderToStaticMarkup(createElement(TaskHierarchy, { tasks: nodes, selectedTaskId: null, onSelectTask() {}, text: (key) => messages.en[key] }));
  assert.doesNotMatch(largeHtml, /<svg/);
  assert.match(largeHtml, /task-hierarchy-list/);
});


test("structure map and selected strip lead with human task status in both languages", () => {
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(TaskHierarchy, {
      tasks: [task(taskId, [], { title: "Selected task", taskState: "accepted" })],
      selectedTaskId: taskId, onSelectTask() {}, text: (key) => messages[locale][key]
    }));
    const strip = html.match(/<div class="map-selected-strip">([\s\S]*?)<\/div>/)?.[1];
    const svg = html.match(/<svg class="map-canvas-svg"[\s\S]*<\/svg>/)?.[0];
    assert.ok(strip?.includes(messages[locale].tracker_gloss_accepted));
    assert.ok(svg?.includes(messages[locale].tracker_gloss_accepted));
    assert.match(svg, /data-graph-state="accepted"/, "the acceptance colour remains tied to the same task state");
    assert.doesNotMatch(strip, /<code\b/);
    assert.doesNotMatch(svg, /<code\b/);
  }
});

test("both maps keep distinct title-bearing accessible names when far-band CSS hides all titles", () => {
  for (const locale of ["en", "ru"]) {
    const tasks = [task(taskId, [], { title: "Alpha unique", taskState: "ready" }), task(otherId, [], { title: "Beta unique", taskState: "ready" })];
    const common = { tasks, selectedTaskId: null, onSelectTask() {} };
    for (const html of [
      renderToStaticMarkup(createElement(TaskGraph, { ...common, edges: [], locale })),
      renderToStaticMarkup(createElement(TaskHierarchy, { ...common, text: (key) => messages[locale][key] }))
    ]) {
      const buttons = [...html.matchAll(/<button[^>]*class="task-graph-node"[^>]*>/g)].map((match) => match[0]);
      assert.equal(buttons.length, 2);
      const labels = buttons.map((button) => button.match(/aria-label="([^"]+)"/)?.[1]);
      assert.ok(labels.some((label) => label?.includes("Alpha unique")));
      assert.ok(labels.some((label) => label?.includes("Beta unique")));
      assert.equal(new Set(labels).size, 2, "same-state tasks must still be distinguishable without visible title descendants");
    }
  }
});
