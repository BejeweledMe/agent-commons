import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { setImmediate as tick } from "node:timers/promises";
import test from "node:test";

// Run the production submit coordinator, including its deferred React updater.
// A successful request must clear its own draft, never a newer draft/project.
const source = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8");
const coordinator = stripTypeScriptTypes(source.slice(source.indexOf("  function submitTask("), source.indexOf("  function setTaskDependency(")));
const bind = new Function("dependencies", `const { actionErrors, task, setTaskErrors, taskRetryIdentity, navigationRevision, routeRef, apiRef, projectSelectionRef, mutationsRef, mutationSurface, clearActionError, recordInstrumentation, setTask, emptyTask, navigate, setRun, setState, refresh, requestOutcome, recordActionError } = dependencies; ${coordinator}; return submitTask;`);
const emptyTask = { title: "", description: "", criteria: "", dependencyIds: [] };

function fixture(extra = {}) {
  const submitted = { title: "  New work  ", description: "  Outcome  ", criteria: " First \n\n Second ", dependencyIds: ["task.prerequisite"], ...extra };
  let draft = structuredClone(submitted), generation = 0, resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const requests = [], navigation = [], errors = [];
  const routeRef = { current: { projectId: "A" } };
  const submit = bind({ actionErrors: {}, task: submitted, setTaskErrors() {},
    taskRetryIdentity: { current: { forOperation: () => "same-write-key", reset() {} } },
    navigationRevision: { current: 0 }, routeRef,
    apiRef: { current: { createTask: async (input) => { requests.push(input); return pending; }, forgetTaskWrite() {} } },
    projectSelectionRef: { current: { currentGeneration: () => generation, isCurrent: (value) => value === generation } },
    mutationsRef: { current: { begin: () => "write", end() {} } }, mutationSurface: () => "tasks",
    clearActionError() {}, recordInstrumentation() {}, setTask: (update) => { draft = update(draft); }, emptyTask,
    navigate: (value) => navigation.push(value), setRun() {}, setState() {}, refresh() {},
    requestOutcome: () => "failed", recordActionError: (...value) => errors.push(value) });
  return { requests, navigation, errors, submit: () => submit({ preventDefault() {} }),
    get draft() { return draft; }, edit: (change) => { draft = { ...draft, ...change }; },
    switchProject() { generation++; routeRef.current.projectId = "B"; },
    confirm: () => resolve({ taskId: "task.created" }), fail: () => reject(new TypeError("response lost")) };
}

for (const extra of [{}, { taskKind: "component", parentTaskId: null }, { taskKind: "task", parentTaskId: "task.parent" }]) {
  test(`confirmed creation clears the saved ${JSON.stringify(extra)} draft`, async () => {
    const f = fixture(extra); f.submit(); f.confirm(); await tick();
    assert.equal(f.requests.length, 1);
    assert.deepEqual(f.draft, emptyTask);
    assert.deepEqual(f.navigation, [{ view: "work", taskId: "task.created", composer: false }]);
    assert.equal(f.errors.length, 0);
  });
}

for (const change of [{ title: "A newer draft" }, { taskKind: "component" }, { parentTaskId: "task.other-parent" }]) {
  test(`a late confirmation preserves the newer ${JSON.stringify(change)} draft`, async () => {
    const f = fixture({ taskKind: "task", parentTaskId: "task.parent" });
    f.submit(); f.edit(change); const edited = structuredClone(f.draft); f.confirm(); await tick();
    assert.deepEqual(f.draft, edited);
  });
}

test("an uncertain creation preserves the draft and does not navigate", async () => {
  const f = fixture({ taskKind: "component", parentTaskId: "task.parent" });
  const before = structuredClone(f.draft); f.submit(); f.fail(); await tick();
  assert.deepEqual(f.draft, before); assert.deepEqual(f.navigation, []); assert.equal(f.errors.length, 1);
});

test("confirmation from the previous project cannot clear the current project's draft", async () => {
  const f = fixture(); f.submit(); f.switchProject(); f.edit({ title: "Project B draft" });
  const before = structuredClone(f.draft); f.confirm(); await tick();
  assert.deepEqual(f.draft, before); assert.deepEqual(f.navigation, []);
});
