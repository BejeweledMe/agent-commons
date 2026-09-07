import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
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
const { stateLabel, filterTrackerTasks, TASK_FILTERS, taskObservationCurrent } = await import(pathToFileURL(resolve(compiled, "taskPresentation.js")).href);
const { loadTaskDetailState } = await import(pathToFileURL(resolve(compiled, "trackerState.js")).href);
const { TaskInspectorContent, updateDetailPresentation } = await import(pathToFileURL(resolve(compiled, "components/TaskInspector.js")).href);
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
  assert.deepEqual(Object.keys(result).sort(), ["acceptanceCriteria", "description", "evidenceRefs", "revision", "state", "summary", "taskId", "title", "truncated"].sort());
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
    assert.ok(html.indexOf("A concrete goal") < html.indexOf(text("inspector_technical")));
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
    assert.ok(html.indexOf(text("inspector_readiness_unknown")) < html.indexOf(text("inspector_technical")));
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
    for (const label of ["Resolve source mismatch", "resolve_dependencies", text("inspector_actions_stale"), text("evidence_complete"), text("inspector_evidence_help"), text("inspector_close")]) assert.ok(visible.includes(label), label);
    assert.ok(visible.indexOf("Resolve source mismatch") < visible.indexOf("resolve_dependencies"));
    assert.equal(visible.includes(text("inspector_no_summary")), false);
    assert.equal(visible.includes(text("inspector_no_runs")), false);
    assert.equal(visible.includes("A concrete goal"), false);
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
