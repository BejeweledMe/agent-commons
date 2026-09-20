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
  ...["taskGraph.ts", "taskGraphApi.ts", "taskEditorState.ts", "taskPresentation.ts", "trackerState.ts", "components/TaskGraph.tsx", "components/TaskEditor.tsx", "components/TaskViews.tsx"].map((file) => resolve(root, "src", file))
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (file) => import(pathToFileURL(resolve(compiled, file + ".js")).href);
const { buildTaskGraph, graphTaskState, wouldCreateTaskCycle, GRAPH_ICONS, MAX_GRAPH_TASKS, NODE_HEIGHT } = await load("taskGraph");
const { TaskGraphApi, parseTaskEditDetail } = await load("taskGraphApi");
const { taskEditorDraft, observeTaskEditorDraft, editorInput } = await load("taskEditorState");
const { bucketTasksForNow, filterTrackerTasks, NOW_COLUMNS } = await load("taskPresentation");
const { trackerStreamSucceeded } = await load("trackerState");
const { TaskGraph } = await load("components/TaskGraph");
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
  // Accepted and completed nodes wear the status-accepted tokens, never a raw green.
  assert.match(css, /\[data-graph-state="accepted"\][^{]*\{ background:var\(--status-accepted-surface\); color:var\(--status-accepted-text\); border-color:var\(--status-accepted\);/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "no raw colour in the graph stylesheet"); assert.match(css, /double/);
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(TaskGraph, { tasks: [task(taskId, [], { title: "<script>unsafe</script>", roleName: "Frontender", provider: "claude", phase: "running" })], edges: [], selectedTaskId: taskId, onSelectTask() {}, locale }));
    assert.match(html, /Frontender · claude/); assert.match(html, /running/);
    assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /style=|<script>/);
    for (const state of ["accepted", "completed", "active", "ready", "queued", "blocked"]) assert.match(html, new RegExp(`data-graph-state="${state}"`));
    assert.match(html, /aria-pressed="true"/);
  }
});

test("graph planned role hints remain distinct from the actual responsible role", () => {
  const props = { tasks: [task(taskId, [], { suggestedAgentId: `agent.${"0".repeat(26)}`, suggestedRoleName: "Frontend engineer", suggestedProvider: "claude" })], edges: [], selectedTaskId: taskId, onSelectTask() {}, locale: "en" };
  const planned = renderToStaticMarkup(createElement(TaskGraph, props));
  assert.match(planned, /Suggested role: Frontend engineer · claude/);
  assert.match(planned, /No current run/);
  const active = renderToStaticMarkup(createElement(TaskGraph, { ...props, tasks: [{ ...props.tasks[0], roleName: "Actual builder", provider: "codex", phase: "running" }] }));
  assert.match(active, /Actual builder · codex/);
  assert.doesNotMatch(active, /Suggested role: Frontend engineer/);
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

test("completed node text has at least 4.5 to 1 contrast on its status surface", () => {
  // The node reads its colours from the :root tokens; resolve them and measure the pair.
  const rootCss = readFileSync(resolve(root, "src/styles.css"), "utf8");
  const token = (name) => rootCss.match(new RegExp(`--${name}:\\s*#([0-9a-fA-F]{6})`))[1];
  const luminance = (hex) => [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const text = luminance(token("status-accepted-text")), surface = luminance(token("status-accepted-surface"));
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
  assert.match(owner, /selectedTaskRef\.current === selectedTask\.taskId/);
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

test("each Tasks view has one render path and the graph capacity is decided before mount", () => {
  // Every column is populated, so an empty column can never hide a missing list.
  // Ids must be ULID-shaped: the task list renders an OutputsButton per row and its scope parser rejects anything else.
  const many = Array.from({ length: 300 }, (_, index) => task(`task.${String(index).padStart(26, "0")}`, [], { taskState: ["ready", "active", "review"][index % 3] }));
  assert.ok(many.length > MAX_GRAPH_TASKS);
  const large = snapshotOf(many);
  const now = renderToStaticMarkup(createElement(TaskViews, viewProps("now", large)));
  assert.equal(occurrences(now, /class="task-list"/g), 0);
  assert.equal(occurrences(now, /class="task-graph"/g), 0);
  assert.equal(occurrences(now, /class="now-column-list"/g), 3, "three columns, one list each");
  const map = renderToStaticMarkup(createElement(TaskViews, viewProps("map", large)));
  assert.equal(occurrences(map, /class="task-graph"/g), 0, "over capacity the graph is never mounted");
  assert.equal(occurrences(map, /class="task-list"/g), 1, "and the list replaces it instead of joining it");
  assert.ok(map.includes(messages.en["task_graph.graphLimit"]));
  const graph = renderToStaticMarkup(createElement(TaskViews, viewProps("map", snapshotOf(many.slice(0, 4)))));
  assert.equal(occurrences(graph, /class="task-graph"/g), 1);
  assert.equal(occurrences(graph, /class="task-list"/g), 0, "under capacity the graph is the only render path");
  const all = renderToStaticMarkup(createElement(TaskViews, viewProps("all", large)));
  assert.equal(occurrences(all, /class="task-list"/g), 1);
  assert.equal(occurrences(all, /class="task-graph"/g), 0);
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
