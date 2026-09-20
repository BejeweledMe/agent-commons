import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-task-inspector-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  resolve(root, "src/api.ts"), resolve(root, "src/trackerState.ts"),
  resolve(root, "src/taskPresentation.ts"), resolve(root, "src/components/TaskInspector.tsx")
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const { WorkApi, ApiProblem, parseTaskCreateResult, parseTaskDetail, parseContextSourceCatalog, parseCatalog } = await import(pathToFileURL(resolve(compiled, "api.js")).href);
const { stateLabel, filterTrackerTasks, TASK_FILTERS, taskObservationCurrent, trackerCapacityOnlyGap } = await import(pathToFileURL(resolve(compiled, "taskPresentation.js")).href);
const { loadTaskDetailState } = await import(pathToFileURL(resolve(compiled, "trackerState.js")).href);
const { TaskInspectorContent, updateDetailPresentation } = await import(pathToFileURL(resolve(compiled, "components/TaskInspector.js")).href);
const { cardOwnsControl, decisionControl } = await import(pathToFileURL(resolve(compiled, "components/DecisionCard.js")).href);
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const taskId = `task.${"0".repeat(26)}`;
const otherId = `task.${"1".repeat(26)}`;
const revision = `evt.${"0".repeat(26)}`;
const nextRevision = `evt.${"1".repeat(26)}`;
const artifactId = `artifact.${"0".repeat(26)}`;
const createResult = () => ({ event_type: "task.created", entity_ref: { kind: "task", id: taskId }, event_id: revision, revision, created: true, index: { private: "must not be exposed" } });
const detailPayload = () => ({ schema: "agent_commons.ui.entity.v1", kind: "task", id: taskId, record: {
  id: taskId, state: "ready", revision, effective_revision: nextRevision,
  title: "A readable task", description: "A concrete goal", acceptance_criteria: ["An observable criterion"],
  artifact_bindings: [{ ref: { kind: "artifact", id: artifactId }, revision }],
  actor: { private: "must not be exposed" }, arbitrary_content: "must not be exposed"
} });
const task = (overrides = {}) => ({ taskId, title: "A readable task", taskState: "ready", readiness: "ready", dependencyTaskIds: [], blockingDependencyIds: [], ownerSessionId: null, roleName: null, provider: null, profileId: null, phase: null, awaitsHuman: false, nextAction: "start_ready_work", freshness: "fresh", evidenceState: "complete", gaps: [], ...overrides });
const reply = (payload, status = 200) => ({ ok: status === 200, status, json: async () => payload });

test("creation resolves only the exact canonical task identity and revision", () => {
  assert.deepEqual(parseTaskCreateResult(createResult()), { taskId, revision });
  for (const mutation of [
    (value) => { value.entity_ref.kind = "agent"; },
    (value) => { value.entity_ref.id = otherId + "?secret"; },
    (value) => { value.event_type = "task.revised"; },
    (value) => { value.revision = nextRevision; },
    (value) => { delete value.event_id; }
  ]) {
    const value = createResult(); mutation(value);
    assert.throws(() => parseTaskCreateResult(value), ApiProblem);
  }
});

test("malformed success after task commit remains retryable with the same frozen request", async () => {
  const api = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const bodies = [];
  globalThis.fetch = async (_url, init) => { bodies.push(init.body); return reply(bodies.length === 1 ? { created: true } : createResult()); };
  const input = { title: "Task", description: "Goal", criteria: ["Criterion"], dependencyIds: [] };
  await assert.rejects(() => api.createTask(input, new AbortController().signal, "same-key"), ApiProblem);
  const result = await api.createTask(input, new AbortController().signal, "same-key");
  assert.deepEqual(result, { taskId, revision });
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
});

test("task detail is an owned allowlist with effective revision and exact evidence references", () => {
  const input = detailPayload();
  const result = parseTaskDetail(input, taskId);
  assert.deepEqual(Object.keys(result).sort(), ["acceptanceCriteria", "description", "evidenceRefs", "findings", "revision", "state", "summary", "taskId", "title", "truncated"].sort());
  assert.equal(result.revision, nextRevision);
  assert.equal(result.summary, null);
  assert.deepEqual(result.evidenceRefs, [{ kind: "artifact", id: artifactId, revision }]);
  input.record.acceptance_criteria[0] = "changed";
  assert.equal(result.acceptanceCriteria[0], "An observable criterion");
  assert.equal(JSON.stringify(result).includes("must not be exposed"), false);
});

test("task details reject wrong identities, malformed values and oversized provider-shaped input", () => {
  for (const mutation of [
    (value) => { value.id = otherId; },
    (value) => { value.record.id = otherId; },
    (value) => { value.record.effective_revision = "untrusted-version"; },
    (value) => { value.record.acceptance_criteria = [7]; },
    (value) => { value.record.description = "я".repeat(2049); },
    (value) => { value.record.state = "succeeded"; },
    (value) => { value.record.acceptance_criteria = Array(33).fill("Criterion"); },
    (value) => { value.record.acceptance_criteria = Array(32).fill("x".repeat(4096)); },
    (value) => { value.record.artifact_bindings[0].revision = "unbound"; }
  ]) {
    const value = detailPayload(); mutation(value);
    assert.throws(() => parseTaskDetail(value, taskId), ApiProblem);
  }
});

test("server-shortened text and arrays are explicitly partial, without fake criteria or evidence", () => {
  const value = detailPayload();
  value.record.description = "A goal …[truncated]";
  value.record.acceptance_criteria = ["Kept criterion", "[truncated: 10 items omitted]"];
  value.record.artifact_bindings = ["[truncated: 10 items omitted]"];
  const result = parseTaskDetail(value, taskId);
  assert.equal(result.truncated, true);
  assert.deepEqual(result.acceptanceCriteria, ["Kept criterion"]);
  assert.deepEqual(result.evidenceRefs, []);
});

const sourceCatalog = () => ({ schema: "agent-commons.ui.context-sources.v1", state: "ready", sources: [
  { ref: { kind: "task", id: taskId, revision }, label: "Task title", freshness: "current" }
], truncated: false });
test("context source discovery accepts all six exact kinds, with closed printable bounded metadata", () => {
  const input = sourceCatalog();
  input.sources = ["artifact", "finding", "task", "thread", "verification", "decision"].map((kind) => ({ ref: { kind, id: `${kind}.${"0".repeat(26)}`, revision }, label: "A label", freshness: "current" }));
  const result = parseContextSourceCatalog(input);
  assert.equal(result.sources.length, 6);
  input.sources[0].ref.revision = nextRevision;
  assert.equal(result.sources[0].ref.revision, revision);
  for (const mutation of [
    (value) => { value.private_metadata = "hidden"; },
    (value) => { value.sources[0].content = "hidden"; },
    (value) => { value.sources[0].ref.extra = "hidden"; },
    (value) => { value.sources[0].label = "я".repeat(129); },
    (value) => { value.sources[0].label = "unsafe\u0000label"; },
    (value) => { value.sources[0].label = "unsafe\u202elabel"; },
    (value) => { value.sources[0].label = "unsafe\u2028label"; },
    (value) => { value.sources[0].freshness = "stale"; },
    (value) => { value.sources[0].ref.kind = "agent"; },
    (value) => { value.sources[0].ref.revision = "latest"; },
    (value) => { value.sources = Array(257).fill(value.sources[0]); },
    (value) => { value.sources.push(value.sources[0]); },
    (value) => { value.state = "empty"; }
  ]) {
    const value = sourceCatalog(); mutation(value);
    assert.throws(() => parseContextSourceCatalog(value), ApiProblem);
  }
  assert.deepEqual(parseContextSourceCatalog({ ...sourceCatalog(), state: "empty", sources: [] }).sources, []);
});

test("source and task adapters use only the existing authenticated read routes", async () => {
  const api = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const urls = [];
  globalThis.fetch = async (url, init) => { urls.push(url); assert.equal(init.method, "GET"); assert.equal(init.credentials, "same-origin"); return reply(url.endsWith("/context-sources") ? sourceCatalog() : detailPayload()); };
  await api.loadContextSources(new AbortController().signal);
  await api.loadTaskDetail(taskId, new AbortController().signal);
  assert.deepEqual(urls, [`${api.apiBase}/work/context-sources`, `${api.apiBase}/entities/task/${taskId}`]);
  await assert.rejects(() => api.loadTaskDetail("task.bad?secret", new AbortController().signal), ApiProblem);
  assert.equal(urls.length, 2);
});

test("late or aborted detail requests cannot overwrite a new task selection", async () => {
  let resolveFirst;
  const states = [];
  const first = new AbortController();
  const second = new AbortController();
  const detail = parseTaskDetail(detailPayload(), taskId);
  const loading = loadTaskDetailState({ loadTaskDetail: () => new Promise((resolve) => { resolveFirst = resolve; }) }, taskId, first.signal, (value) => states.push(value));
  first.abort();
  await loadTaskDetailState({ loadTaskDetail: async () => ({ ...detail, taskId: otherId }) }, otherId, second.signal, (value) => states.push(value));
  resolveFirst(detail);
  await loading;
  assert.deepEqual(states.map((value) => [value.kind, value.taskId]), [["loading", taskId], ["loading", otherId], ["ready", otherId]]);
  const wrong = [];
  await loadTaskDetailState({ loadTaskDetail: async () => detail }, otherId, second.signal, (value) => wrong.push(value));
  assert.equal(wrong.at(-1).kind, "failure");
});

test("filters preserve canonical distinctions and attention can come from the attention projection", () => {
  const tasks = TASK_FILTERS.filter((value) => !["all", "attention"].includes(value)).map((state, index) => task({ taskId: `task.${index}`, taskState: state, title: `Task ${state}` }));
  const snapshot = { tasks, attention: [{ taskId: tasks[0].taskId }] };
  for (const current of tasks) assert.deepEqual(filterTrackerTasks(snapshot, current.taskState, ""), [current]);
  assert.deepEqual(filterTrackerTasks(snapshot, "attention", ""), [tasks[0]]);
  assert.equal(filterTrackerTasks(snapshot, "all", "ACCEPTED").length, 1);
  assert.equal(filterTrackerTasks(snapshot, "ready", "accepted").length, 0);
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: a new ready task with complete evidence is neither completed, successful nor accepted`, () => {
    const text = (key) => { assert.equal(typeof messages[locale][key], "string", key); return messages[locale][key]; };
    assert.notEqual(stateLabel("evidence", "complete"), stateLabel("task", "completed"));
    assert.notEqual(stateLabel("evidence", "complete"), stateLabel("run", "succeeded"));
    assert.notEqual(stateLabel("evidence", "complete"), stateLabel("task", "accepted"));
    const detail = parseTaskDetail(detailPayload(), taskId);
    const html = renderToStaticMarkup(TaskInspectorContent({ task: task(), tasks: [task()], runs: [], sourceRevision: revision, locale, text, actionsCurrent: true, writesEnabled: true,
      onSelectTask: () => {}, onLaunchTask: () => {}, detailState: { kind: "ready", taskId, detail }, onRefresh: () => {} }));
    assert.ok(html.includes("A concrete goal"));
    assert.ok(html.includes("An observable criterion"));
    assert.ok(html.includes(text("evidence_complete")));
    assert.ok(html.includes(text("inspector_no_runs")));
    assert.equal(html.includes(text("tracker_gloss_complete")), false);
    assert.equal(html.includes(text("tracker_gloss_succeeded")), false);
    // The decision comes first and its technical values sit inside it; the goal
    // and every supporting disclosure follow the card.
    assert.ok(html.indexOf("decision-details") < html.indexOf("A concrete goal"));
    assert.equal(html.includes("must not be exposed"), false);
  });
  test(`${locale}: unknown readiness and missing detail stay explicit before technical diagnostics`, () => {
    const text = (key) => messages[locale][key];
    let launched = false;
    const view = TaskInspectorContent({ task: task({ readiness: "policy_unknown" }), tasks: [], runs: [], sourceRevision: revision, locale, text, actionsCurrent: false, writesEnabled: true,
      onSelectTask: () => {}, onLaunchTask: () => { launched = true; }, detailState: { kind: "failure", taskId }, onRefresh: () => {} });
    const html = renderToStaticMarkup(view);
    assert.ok(html.includes(text("inspector_detail_unavailable")));
    assert.ok(html.includes(text("inspector_readiness_unknown")));
    assert.ok(html.indexOf(text("inspector_readiness_unknown")) < html.indexOf("decision-details"));
    assert.ok(html.includes('disabled=""'));
    assert.equal(launched, false);
  });
}

test("profiles with the same provider have distinct accessible option labels", () => {
  const catalog = parseCatalog({ profiles: ["claude-builder", "claude-independent-reviewer"], profile_info: {
    "claude-builder": { provider: "Claude" }, "claude-independent-reviewer": { provider: "Claude" }
  } });
  assert.notEqual(catalog.profiles[0].label, catalog.profiles[1].label);
  for (const profile of catalog.profiles) assert.ok(profile.label.includes(profile.id));
});

test("auth scrubs exchange material before network while preserving only allowed navigation", async () => {
  const storedWindow = globalThis.window;
  const storedFetch = globalThis.fetch;
  const saved = new Map();
  const locations = [];
  globalThis.window = {
    location: { pathname: "/work", search: `?view=work&task=${taskId}&filter=ready&search=private&secret=private`, hash: "#c=one-use-secret" },
    history: { replaceState: (_state, _title, url) => locations.push(url) },
    sessionStorage: { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: (key) => saved.delete(key) }
  };
  globalThis.fetch = async (url, init) => {
    assert.equal(locations.length, 1, "fragment must be scrubbed before the first request");
    assert.equal(locations[0].includes("private"), false);
    assert.equal(locations[0].includes("one-use-secret"), false);
    assert.equal(locations[0].includes("#"), false);
    assert.ok(locations[0].includes(taskId));
    assert.ok(locations[0].includes("filter=ready"));
    assert.equal(url, "/api/auth/exchange");
    assert.deepEqual(JSON.parse(init.body), { code: "one-use-secret" });
    return reply({ api_base: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  };
  try {
    await new WorkApi().connect(new AbortController().signal);
    assert.equal(JSON.stringify([...saved.values()]).includes("one-use-secret"), false);
  } finally { globalThis.window = storedWindow; globalThis.fetch = storedFetch; }
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: canonical ready task does not claim dependency eligibility and in-progress readiness does not invent a run`, () => {
    const text = (key) => messages[locale][key];
    const render = (current) => renderToStaticMarkup(TaskInspectorContent({ task: current, tasks: [current], runs: [], sourceRevision: revision, locale, text, actionsCurrent: true, writesEnabled: true,
      onSelectTask: () => {}, onLaunchTask: () => {}, detailState: { kind: "failure", taskId }, onRefresh: () => {} }));
    const blocked = render(task({ readiness: "blocked", nextAction: "resolve_dependencies", blockingDependencyIds: [otherId] }));
    assert.ok(blocked.includes(text("task_view_state_ready")));
    assert.equal(blocked.includes(text("tracker_gloss_ready")), false);
    const reviewing = render(task({ taskState: "review", readiness: "in_progress", nextAction: "wait_for_review" }));
    assert.ok(reviewing.includes(text("inspector_readiness_in_progress")));
    assert.ok(reviewing.includes(text("inspector_no_runs")));
    assert.equal(reviewing.includes(text("tracker_gloss_in_progress")), false);
    assert.ok(reviewing.includes('<code>review</code> —'));
  });
}

test("unrelated partial capacity does not block a fresh task, while uncertain task observations remain gated", () => {
  const selected = task();
  const snapshot = { state: "partial", tasks: [selected], gaps: ["capacity_missing"],
    freshness: { state: "fresh", resumeGap: false }, capacity: { state: "unknown" } };
  assert.equal(taskObservationCurrent(snapshot, selected), true);
  for (const state of ["error", "empty", "loading", "stale"]) {
    assert.equal(taskObservationCurrent({ ...snapshot, state }, selected), false, state);
  }
  assert.equal(taskObservationCurrent({ ...snapshot, freshness: { state: "unknown", resumeGap: false } }, selected), false);
  assert.equal(taskObservationCurrent({ ...snapshot, freshness: { state: "fresh", resumeGap: true } }, selected), false);
  for (const freshness of ["unknown", "stale"]) assert.equal(taskObservationCurrent(snapshot, task({ freshness })), false);
  for (const readiness of ["unknown", "policy_unknown", "terminal_dependency_failure"]) assert.equal(taskObservationCurrent(snapshot, task({ readiness })), false);
  for (const locale of ["en", "ru"]) {
    const text = (key) => messages[locale][key];
    const html = renderToStaticMarkup(TaskInspectorContent({ task: selected, tasks: [selected], runs: [], sourceRevision: revision, locale, text,
      actionsCurrent: taskObservationCurrent(snapshot, selected), writesEnabled: true,
      onSelectTask: () => {}, onLaunchTask: () => {}, detailState: { kind: "failure", taskId }, onRefresh: () => {} }));
    assert.ok(html.includes(text("inspector_prepare_run")));
    assert.equal(html.includes('disabled=""'), false);
    assert.equal(html.includes(text("inspector_actions_stale")), false);
  }
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: readiness owns scheduling labels and freshness alone does not claim unknown readiness`, () => {
    const text = (key) => messages[locale][key];
    assert.notEqual(stateLabel("readiness", "complete"), stateLabel("task", "completed"));
    assert.notEqual(stateLabel("readiness", "blocked"), stateLabel("task", "blocked"));
    assert.equal(stateLabel("readiness", "complete"), "inspector_readiness_complete");
    const current = task({ freshness: "stale" });
    const html = renderToStaticMarkup(TaskInspectorContent({ task: current, tasks: [current], runs: [], sourceRevision: revision, locale, text,
      actionsCurrent: false, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
      detailState: { kind: "failure", taskId }, onRefresh: () => {} }));
    assert.ok(html.includes(text("tracker_task_state_label")));
    assert.ok(html.includes(text("tracker_readiness_label")));
    assert.ok(html.includes(text("inspector_actions_stale")));
    assert.equal(html.includes(text("inspector_readiness_unknown")), false);
    assert.ok(html.includes('disabled=""'));
  });
}

// Model the native disclosure's initial visible subtree. Browser verification covers
// keyboard toggling and retained focus; this check guards what starts hidden.
function initiallyVisible(node) {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(initiallyVisible).join("");
  if (typeof node !== "object") return String(node);
  if (typeof node.type === "function") return initiallyVisible(node.type(node.props));
  const children = node.props?.children;
  if (node.type === "details" && !node.props.open) {
    return initiallyVisible((Array.isArray(children) ? children : [children]).find((child) => child?.type === "summary"));
  }
  return initiallyVisible(children);
}

for (const locale of ["en", "ru"]) {
  test(`${locale}: collapsed supporting sections never conceal blocker, next action, evidence state or stale observation`, () => {
    const text = (key) => messages[locale][key];
    const current = task({ readiness: "blocked", nextAction: "resolve_dependencies", blockingDependencyIds: [otherId], freshness: "stale" });
    const view = TaskInspectorContent({ task: current, tasks: [current, task({ taskId: otherId, title: "Resolve source mismatch" })], runs: [], sourceRevision: revision, locale, text,
      actionsCurrent: false, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
      detailState: { kind: "ready", taskId, detail: parseTaskDetail(detailPayload(), taskId) }, onRefresh: () => {} });
    const visible = initiallyVisible(view);
    for (const label of ["Resolve source mismatch", text("tracker_gloss_resolve_dependencies"), text("inspector_actions_stale"), text("evidence_complete"), text("inspector_evidence_help"), text("inspector_close")]) assert.ok(visible.includes(label), label);
    assert.ok(visible.indexOf("Resolve source mismatch") < visible.indexOf(text("tracker_gloss_resolve_dependencies")));
    assert.equal(visible.includes(text("inspector_no_summary")), false);
    assert.equal(visible.includes(text("inspector_no_runs")), false);
    assert.equal(visible.includes("A concrete goal"), true);
    assert.ok(visible.includes("An observable criterion"));
    assert.equal(renderToStaticMarkup(view).includes(text("inspector_summary")), false);
    assert.ok(renderToStaticMarkup(view).includes("A concrete goal"));
  });
  test(`${locale}: a recorded summary remains initially visible`, () => {
    const text = (key) => messages[locale][key];
    const detail = { ...parseTaskDetail(detailPayload(), taskId), summary: "Ready for a human to review the references" };
    const view = TaskInspectorContent({ task: task(), tasks: [], runs: [], sourceRevision: revision, locale, text,
      actionsCurrent: true, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
      detailState: { kind: "ready", taskId, detail }, onRefresh: () => {} });
    assert.ok(initiallyVisible(view).includes(detail.summary));
  });
}


test("same-task refresh preserves last details through loading/failure and clears them on selection change", () => {
  const detail = parseTaskDetail(detailPayload(), taskId);
  let presentation = updateDetailPresentation({ current: { kind: "loading", taskId }, lastReady: null }, { kind: "ready", taskId, detail });
  for (const kind of ["loading", "failure", "loading"]) {
    presentation = updateDetailPresentation(presentation, { kind, taskId });
    assert.equal(presentation.lastReady, detail);
    assert.equal(presentation.current.kind, kind);
    for (const locale of ["en", "ru"]) {
      const text = (key) => messages[locale][key];
      const html = renderToStaticMarkup(TaskInspectorContent({ task: task(), tasks: [], runs: [], sourceRevision: revision, locale, text,
        actionsCurrent: true, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
        detailState: presentation.current, cachedDetail: presentation.lastReady, onRefresh: () => {} }));
      assert.ok(html.includes("A concrete goal"));
      assert.ok(html.includes(text("inspector_detail_cached")));
      assert.ok(html.includes(text(kind === "failure" ? "inspector_detail_unavailable" : "inspector_detail_loading")));
    }
  }
  presentation = updateDetailPresentation(presentation, { kind: "loading", taskId: otherId });
  assert.equal(presentation.lastReady, null);
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: identity changes never expose cached details from the previous task`, () => {
    const text = (key) => messages[locale][key];
    const html = renderToStaticMarkup(TaskInspectorContent({ task: task({ taskId: otherId }), tasks: [], runs: [], sourceRevision: revision, locale, text,
      actionsCurrent: false, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
      detailState: { kind: "loading", taskId: otherId }, cachedDetail: parseTaskDetail(detailPayload(), taskId), onRefresh: () => {} }));
    assert.equal(html.includes("A concrete goal"), false);
    assert.equal(html.includes(text("inspector_detail_cached")), false);
    assert.ok(html.includes(text("inspector_detail_loading")));
  });
}


test("only absent capacity telemetry avoids the global task-evidence warning", () => {
  const snapshot = {state:"partial",truncated:false,gaps:["capacity_missing"],capacity:{state:"unknown"},tasks:[task()],runs:[]};
  assert.equal(trackerCapacityOnlyGap(snapshot),true);
  for(const patch of [
    {truncated:true}, {gaps:[]}, {gaps:["capacity_malformed"]}, {gaps:["capacity_missing","projection_missing"]},
    {tasks:[task({gaps:["dependency_missing"]})]}, {runs:[{evidenceState:"missing"}]}, {capacity:{state:"available"}}
  ]) assert.equal(trackerCapacityOnlyGap({...snapshot,...patch}),false);
  const source=readFileSync(resolve(root,"src/components/TrackerSection.tsx"),"utf8");
  assert.match(source,/!trackerCapacityOnlyGap\(snapshot\)/);
  assert.match(source,/TaskState domain="capacity" value=\{snapshot.capacity.state\}/);
  assert.match(source,/snapshot.gaps.map/);
});


for (const locale of ["en", "ru"]) test(`${locale}: primary task status uses prose and keeps exact states in diagnostics`, () => {
  const text = (key) => messages[locale][key];
  const render = (current) => TaskInspectorContent({task:current,tasks:[current],runs:[],sourceRevision:revision,locale,text,actionsCurrent:true,writesEnabled:true,onSelectTask:()=>{},onLaunchTask:()=>{},detailState:{kind:"ready",taskId,detail:parseTaskDetail(detailPayload(),taskId)},onRefresh:()=>{}});
  const view=render(task()), visible=initiallyVisible(view), markup=renderToStaticMarkup(view);
  assert.ok(visible.includes(text("task_view_state_ready")));
  assert.ok(visible.includes(text("tracker_gloss_start_ready_work")));
  assert.equal(visible.includes("start_ready_work"),false);
  assert.equal(visible.includes(text("tracker_readiness_label")),false);
  assert.ok(markup.includes('<code>start_ready_work</code>'));
  assert.ok(markup.includes('data-value="ready"'));
  const blocked=initiallyVisible(render(task({readiness:"blocked",nextAction:"resolve_dependencies"})));
  assert.ok(blocked.includes(text("task_view_state_ready")));
  // Readiness joins the state in one sentence; only its raw label and value
  // stay behind the card's details.
  assert.equal(blocked.includes(text("tracker_readiness_label")),false);
  assert.ok(blocked.includes(text("inspector_readiness_blocked")));
  assert.equal(blocked.includes("resolve_dependencies"),false);
  const unknown=initiallyVisible(render(task({readiness:"policy_unknown",nextAction:"future_action"})));
  assert.ok(unknown.includes(text("tracker_gloss_policy_unknown")));
  assert.ok(unknown.includes(text("tracker_gloss_unknown")));
});

const run = (overrides = {}) => ({ delegationId: `delegation.${"0".repeat(26)}`, taskId, agentId: null, roleName: "Builder",
  provider: "claude", profileId: "claude-builder", phase: "succeeded", attemptId: null, attemptNumber: 1,
  startedAt: "2026-09-09T10:00:00Z", updatedAt: "2026-09-09T10:05:00Z", finishedAt: "2026-09-09T10:05:00Z",
  durationSeconds: 300, wallTimeSeconds: null, awaitsHuman: false, nextAction: "none", freshness: "fresh", evidenceState: "complete", ...overrides });

for (const locale of ["en", "ru"]) {
  test(`${locale}: a run row states the recorded attempt limit and says so when the delegation carries none`, () => {
    const text = (key) => messages[locale][key];
    const render = (runs) => renderToStaticMarkup(TaskInspectorContent({ task: task(), tasks: [], runs, sourceRevision: revision, locale, text,
      actionsCurrent: true, writesEnabled: true, onSelectTask: () => {}, onLaunchTask: () => {},
      detailState: { kind: "failure", taskId }, onRefresh: () => {} }));

    const recorded = render([run({ wallTimeSeconds: 900 })]);
    assert.ok(recorded.includes(`${text("run_limit_label")}: 15 ${text("run_limit_minutes")}`));
    assert.equal(recorded.includes(text("run_limit_absent")), false);

    // A legacy or canary run whose DTO carries no limit is never given one.
    for (const missing of [null, undefined, 0, -60, "600", 12.5]) {
      const absent = render([run({ wallTimeSeconds: missing })]);
      assert.ok(absent.includes(`${text("run_limit_label")}: ${text("run_limit_absent")}`), String(missing));
      assert.equal(absent.includes(`10 ${text("run_limit_minutes")}`), false, String(missing));
      assert.equal(absent.includes("600"), false, String(missing));
    }

    // A limit that is not whole minutes is reported exactly, never rounded.
    assert.ok(render([run({ wallTimeSeconds: 90 })]).includes(`${text("run_limit_label")}: 90 ${text("run_limit_seconds")}`));
  });
}

test("the attempt limit reading exists in both locales and never reuses the duration wording", () => {
  for (const key of ["run_limit_label", "run_limit_minutes", "run_limit_seconds", "run_limit_absent"]) {
    assert.equal(typeof messages.en[key], "string", key);
    assert.equal(typeof messages.ru[key], "string", key);
    assert.notEqual(messages.en[key], "");
    assert.notEqual(messages.ru[key], "");
  }
  assert.equal(messages.en.run_limit_label, "Attempt limit");
  assert.equal(messages.ru.run_limit_label, "Лимит попытки");
  assert.equal(messages.en.run_limit_absent, "Limit not provided");
  assert.equal(messages.ru.run_limit_absent, "Лимит не предоставлен");
});

// ---------------------------------------------------------------- DecisionCard
const findingId = `finding.${"0".repeat(26)}`;
const objectiveId = `objective.${"0".repeat(26)}`;
const applicationId = `application.${"0".repeat(26)}`;
const readyDetail = () => parseTaskDetail(detailPayload(), taskId);
/** Stand in for the tracker's canonical action panel: one primary button. */
const actionPanel = (label) => createElement("div", { className: "tracker-actions" },
  createElement("button", { type: "button", className: "button button-primary" }, label));
const answerEntry = createElement("button", { type: "button", className: "button button-primary button-inline conversation-entry" }, "Answer");
function card(current, options = {}) {
  const { locale = "en", runs = [], detail = readyDetail(), children = null, answerControl = answerEntry,
    actionsCurrent = true, writesEnabled = true } = options;
  const text = (key) => { assert.equal(typeof messages[locale][key], "string", key); return messages[locale][key]; };
  return TaskInspectorContent({ task: current, tasks: [current, task({ taskId: otherId, title: "Resolve source mismatch" })],
    runs, sourceRevision: revision, locale, text, actionsCurrent, writesEnabled,
    onSelectTask: () => {}, onLaunchTask: () => {}, detailState: { kind: "ready", taskId, detail },
    children, answerControl, onRefresh: () => {} });
}

/** Every observed next action names exactly one control, and only two are the card's own. */
const NEXT_ACTION_CONTROL = {
  start_ready_work: "prepare_run", retry_new_run: "prepare_run",
  answer_operator_request: "answer_worker", accept_task: "accept",
  revise_work: "return_for_revision", request_review: "request_review",
  wait_for_run: null, wait_for_review: null, resolve_dependencies: null,
  inspect_failure: null, inspect_missing_evidence: null, none: null
};
const SLOT_CONTROLS = ["accept", "return_for_revision", "request_review"];

test("each next action resolves to one named control, and an unknown action offers none", () => {
  // The mapping covers the closed canonical set, so a new action cannot slip
  // through without a decision about its control.
  const canonical = readFileSync(resolve(root, "src/api.ts"), "utf8")
    .split("const TRACKER_NEXT_ACTIONS = new Set([")[1].split("]);")[0]
    .match(/"[a-z_]+"/g).map((value) => value.slice(1, -1));
  assert.deepEqual(canonical.sort(), Object.keys(NEXT_ACTION_CONTROL).sort());
  for (const [action, control] of Object.entries(NEXT_ACTION_CONTROL)) {
    assert.equal(decisionControl(action), control, action);
    assert.equal(cardOwnsControl(action), control !== null && !SLOT_CONTROLS.includes(control), action);
  }
  assert.equal(decisionControl("future_action"), null);
  assert.equal(cardOwnsControl("future_action"), false);
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: the card offers exactly one primary control for every next action`, () => {
    for (const [action, control] of Object.entries(NEXT_ACTION_CONTROL)) {
      const markup = renderToStaticMarkup(card(task({ nextAction: action }), { locale,
        children: SLOT_CONTROLS.includes(control) ? actionPanel(action) : null }));
      assert.equal((markup.match(/class="button button-primary/g) ?? []).length,
        control === null ? 0 : 1, `${action} in ${locale}`);
    }
  });

  test(`${locale}: the card answers what is happening, who is responsible and what happens next`, () => {
    const text = (key) => messages[locale][key];
    const worker = run({ phase: "running", roleName: "Builder" });
    const asked = run({ phase: "input_needed", roleName: "Builder" });
    const cases = [
      { name: "ready", current: task(), runs: [],
        happening: text("task_view_state_ready"), responsible: text("inspector_unassigned"),
        next: text("tracker_gloss_start_ready_work") },
      { name: "active with a run", runs: [worker],
        current: task({ taskState: "active", readiness: "in_progress", phase: "running", nextAction: "wait_for_run", roleName: "Planner" }),
        happening: `${text("tracker_gloss_active")} — ${text("inspector_readiness_in_progress")}`,
        responsible: `Builder — ${text("decision_responsible_running")}`, next: text("tracker_gloss_wait_for_run") },
      { name: "awaiting human", runs: [asked],
        current: task({ taskState: "active", readiness: "human_attention", phase: "input_needed", awaitsHuman: true, nextAction: "answer_operator_request" }),
        happening: `${text("tracker_gloss_active")} — ${text("tracker_gloss_human_attention")}`,
        responsible: `Builder — ${text("decision_responsible_running")}`, next: text("tracker_gloss_answer_operator_request") },
      { name: "review", runs: [],
        current: task({ taskState: "review", readiness: "in_progress", nextAction: "wait_for_review", roleName: "Reviewer" }),
        happening: `${text("tracker_gloss_review")} — ${text("inspector_readiness_in_progress")}`,
        responsible: `Reviewer — ${text("decision_responsible_owner")}`, next: text("tracker_gloss_wait_for_review") },
      { name: "accepted", runs: [],
        current: task({ taskState: "accepted", readiness: "complete", nextAction: "none" }),
        happening: `${text("tracker_gloss_accepted")} — ${text("inspector_readiness_complete")}`,
        responsible: text("inspector_unassigned"), next: text("tracker_gloss_none") },
      { name: "blocked", runs: [],
        current: task({ taskState: "blocked", readiness: "blocked", nextAction: "resolve_dependencies", blockingDependencyIds: [otherId] }),
        happening: `${text("task_view_state_blocked")} — ${text("inspector_readiness_blocked")}`,
        responsible: text("inspector_unassigned"), next: text("tracker_gloss_resolve_dependencies") }
    ];
    for (const item of cases) {
      const visible = initiallyVisible(card(item.current, { locale, runs: item.runs }));
      for (const [label, sentence] of [["decision_happening", item.happening],
        ["decision_responsible", item.responsible], ["decision_next", item.next]]) {
        assert.ok(visible.includes(text(label)), `${item.name}: ${label}`);
        assert.ok(visible.includes(sentence), `${item.name}: ${sentence}`);
      }
      // The three questions keep their order, and no raw canonical value leaks
      // into the answers.
      assert.ok(visible.indexOf(text("decision_happening")) < visible.indexOf(text("decision_responsible")));
      assert.ok(visible.indexOf(text("decision_responsible")) < visible.indexOf(text("decision_next")));
      if (item.current.nextAction.includes("_")) assert.equal(visible.includes(item.current.nextAction), false, item.name);
      assert.equal(visible.includes(item.current.taskId), false, item.name);
    }
  });

  test(`${locale}: technical values stay in a details disclosure that starts closed`, () => {
    const current = task({ objectiveId, applicationId });
    const view = card(current, { locale, runs: [run({ phase: "running" })] });
    const visible = initiallyVisible(view);
    const markup = renderToStaticMarkup(view);
    const text = (key) => messages[locale][key];
    assert.ok(visible.includes(text("decision_details")));
    for (const value of [taskId, nextRevision, objectiveId, applicationId, "start_ready_work", run().delegationId]) {
      assert.ok(markup.includes(value), value);
      assert.equal(visible.includes(value), false, value);
    }
    assert.equal(/<details[^>]*class="decision-details[^>]*open/.test(markup), false);
  });

  test(`${locale}: findings are listed with their count and never hidden`, () => {
    const text = (key) => messages[locale][key];
    const payload = detailPayload();
    payload.record.findings = [{ id: findingId, title: "Evidence gap in the run record" }];
    const detail = parseTaskDetail(payload, taskId);
    assert.deepEqual(detail.findings, [{ id: findingId, title: "Evidence gap in the run record" }]);
    const listed = initiallyVisible(card(task(), { locale, detail }));
    assert.ok(listed.includes(`${text("decision_findings")} (1)`));
    assert.ok(listed.includes("Evidence gap in the run record"));
    assert.ok(listed.includes(findingId));
    // Zero is still stated: an empty count is a fact, not a hidden section.
    const emptyPayload = detailPayload(); emptyPayload.record.findings = [];
    const none = initiallyVisible(card(task(), { locale, detail: parseTaskDetail(emptyPayload, taskId) }));
    assert.ok(none.includes(`${text("decision_findings")} (0)`));
    assert.ok(none.includes(text("decision_no_findings")));
    // A record without the key is not "none": the card says the server has not provided them.
    const unknown = initiallyVisible(card(task(), { locale }));
    assert.ok(unknown.includes(text("decision_findings_unavailable")));
    assert.equal(unknown.includes(`${text("decision_findings")} (0)`), false);
    assert.equal(unknown.includes(text("decision_no_findings")), false);
  });
}

test("attached findings are an owned allowlist and a malformed entry is a protocol fault", () => {
  assert.equal(readyDetail().findings, null, "no findings key means unknown, not an empty list");
  for (const mutation of [
    (value) => { value.record.findings = [findingId]; },
    (value) => { value.record.findings = [{ id: otherId }]; },
    (value) => { value.record.findings = [{ id: findingId, title: 7 }]; },
    (value) => { value.record.findings = [{ id: findingId, secret: "must not be exposed" }, "x"]; },
    (value) => { value.record.findings = "one finding"; }
  ]) {
    const value = detailPayload(); mutation(value);
    assert.throws(() => parseTaskDetail(value, taskId), ApiProblem);
  }
  const value = detailPayload();
  value.record.findings = [{ id: findingId, title: "Kept", extra: "must not be exposed" }];
  assert.equal(JSON.stringify(parseTaskDetail(value, taskId)).includes("must not be exposed"), false);
});

test("green marks accepted work and nothing else", () => {
  for (const state of TASK_FILTERS.filter((value) => !["all", "attention"].includes(value))) {
    const markup = renderToStaticMarkup(card(task({ taskState: state })));
    assert.equal(markup.includes('data-accepted="true"'), state === "accepted", state);
    assert.ok(markup.includes(`data-accepted="${state === "accepted" ? "true" : "false"}"`), state);
  }
  // The acceptance palette is declared once, as the status/accepted tokens, and
  // read by the accepted card alone.
  const css = readFileSync(resolve(root, "src/styles.css"), "utf8");
  assert.match(css, /\.decision-card\[data-accepted="true"\] \{/);
  for (const value of ["#143220", "#72ca8a", "#d9ffe5"]) {
    assert.equal((css.match(new RegExp(value, "g")) ?? []).length, 1, value);
  }
  for (const token of ["--status-accepted-surface", "--status-accepted", "--status-accepted-text"]) {
    const uses = [...css.matchAll(new RegExp(`var\\(${token}\\)`, "g"))];
    assert.equal(uses.length, 1, token);
    assert.ok(uses[0].index > css.indexOf('.decision-card[data-accepted="true"]'), token);
  }
  // Styling stays in classes and data attributes; the card carries no inline style.
  const source = readFileSync(resolve(root, "src/components/DecisionCard.tsx"), "utf8");
  assert.equal(source.includes("style={"), false);
});

test("the card renders only from the snapshot the tracker reloaded, with no optimistic state", () => {
  const tracker = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  // Every canonical action reloads the tracker and re-renders from that answer.
  assert.match(tracker, /await api\.loadTracker\(signal\);\s*\n\s*setState\(\(current\) => trackerLoadSucceeded\(current, snapshot\)\);/);
  const intent = tracker.slice(tracker.indexOf("async function performTaskIntent"), tracker.indexOf("function trackerTaskActions"));
  assert.ok(intent.length > 0);
  // The one re-render an action causes is the server's answer, and the success
  // reading is only recorded after that reload.
  assert.equal((intent.match(/setState\(/g) ?? []).length, 1);
  assert.ok(intent.indexOf("api.loadTracker(signal)") < intent.indexOf('kind: "success"'));
  assert.match(tracker, /actionsCurrent=\{actionsCurrent\}/);
  // A refused or uncertain attempt keeps the existing retry / new-attempt path.
  assert.match(tracker, /tracker_retry_previous_action/);
  assert.match(tracker, /inspector_new_attempt/);
});
