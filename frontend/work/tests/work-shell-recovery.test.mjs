import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "commons-shell-recovery-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  ...["api.ts", "launchIntentState.ts", "taskDependencyState.ts", "components/LaunchRecoveryPanel.tsx", "components/TaskComposer.tsx"].map((path) => resolve(root, "src", path))], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const moduleAt = (path) => import(pathToFileURL(resolve(compiled, path)));
const { WorkApi } = await moduleAt("api.js");
const { freezeLaunchIntent, launchIntentIsVisible, restoreLaunchDraft, runLimitSeconds } = await moduleAt("launchIntentState.js");
const { taskDependencyCatalog } = await moduleAt("taskDependencyState.js");
const { LaunchRecoveryPanel } = await moduleAt("components/LaunchRecoveryPanel.js");
const { TaskComposer } = await moduleAt("components/TaskComposer.js");
const strings = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const id = (kind, n = 0) => `${kind}.${"0".repeat(25)}${n}`;
const taskA = id("task"), taskB = id("task", 1), roleA = id("agent"), roleB = id("agent", 1);
const api = () => Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
const json = (payload) => ({ ok: true, status: 200, json: async () => payload });
function intentFixture() {
  return freezeLaunchIntent({ key: "original-A-key", taskTitle: "Original task A", roleName: "Original role A", profileId: "claude-builder",
    draft: { taskId: taskA, agentId: roleA, contextPackKey: `${id("context_pack")}@${id("evt")}`, designPackageKey: `${id("design_package")}@${id("evt", 1)}`, limitMinutes: "25" },
    input: { taskId: taskA, agentId: roleA, contextPackId: id("context_pack"), contextPackRevision: id("evt"), designPackageId: id("design_package"), designPackageRevision: id("evt", 1), wallTimeSeconds: 1500 } });
}
for (const locale of ["en", "ru"]) {
  test(`${locale}: unknown A launch survives selection B; recovery names A and exposes restore rather than a misleading retry`, async () => {
    const intent = intentFixture(), client = api(), sent = [];
    globalThis.fetch = async (_url, init) => { sent.push(init.body); if (sent.length === 1) throw new TypeError("Response lost after commit"); return json({ created: false }); };
    await assert.rejects(client.startRun(intent.input, intent.key, new AbortController().signal));
    const draftB = { taskId: taskB, agentId: roleB, contextPackKey: "", designPackageKey: "", limitMinutes: "10" };
    assert.equal(launchIntentIsVisible(intent, taskB, draftB), false);
    const props = { intent, visible: false, busy: false, uncertain: true, failureCode: "request_unavailable", onRestore() {}, onRetry() {}, onEdit() {}, text: (key) => strings[locale][key] };
    const markup = renderToStaticMarkup(createElement(LaunchRecoveryPanel, props));
    assert.ok(markup.includes("Original task A"));
    assert.ok(markup.includes("Original role A"));
    assert.ok(markup.includes("claude-builder"));
    assert.ok(markup.includes(strings[locale].shell_restore_launch));
    assert.equal(markup.includes(strings[locale].shell_retry_original), false);
    assert.equal(markup.includes(strings[locale].shell_edit_launch), false, "unknown launch cannot be discarded");
    assert.equal(sent.length, 1, "selection and recovery display never launch anything");
    const restored = restoreLaunchDraft(intent);
    assert.equal(launchIntentIsVisible(intent, taskA, restored), true);
    assert.equal(launchIntentIsVisible(intent, taskA, { ...restored, agentId: roleB }), false);
    await client.startRun(intent.input, intent.key, new AbortController().signal);
    assert.equal(sent[0], sent[1]);
    assert.equal(JSON.parse(sent[1]).task_id, taskA);
    assert.equal(JSON.parse(sent[1]).agent_id, roleA);
    assert.equal(JSON.parse(sent[1]).idempotency_key, intent.key);
    assert.equal(JSON.parse(sent[1]).context_pack_revision, id("evt"));
    assert.equal(JSON.parse(sent[1]).design_package_revision, id("evt", 1));
    assert.equal(JSON.parse(sent[1]).wall_time_seconds, 1500);
  });

  test(`${locale}: the frozen intent carries the chosen limit through restore and retry, and names it in recovery`, () => {
    const intent = intentFixture();
    // Restore returns the exact minutes the operator typed, not the form default.
    assert.equal(restoreLaunchDraft(intent).limitMinutes, "25");
    assert.equal(runLimitSeconds(restoreLaunchDraft(intent).limitMinutes), intent.input.wallTimeSeconds);
    // A different limit is a different draft: retry would re-send the original.
    assert.equal(launchIntentIsVisible(intent, taskA, restoreLaunchDraft(intent)), true);
    assert.equal(launchIntentIsVisible(intent, taskA, { ...restoreLaunchDraft(intent), limitMinutes: "30" }), false);
    const markup = renderToStaticMarkup(createElement(LaunchRecoveryPanel, { intent, visible: true, busy: false, uncertain: false,
      failureCode: "invalid_wall_time_seconds", onRestore() {}, onRetry() {}, onEdit() {}, text: (key) => strings[locale][key] }));
    assert.ok(markup.includes(strings[locale].run_limit_label));
    assert.ok(markup.includes(`25 ${strings[locale].run_limit_minutes}`));
    assert.equal(markup.includes(strings[locale].run_limit_absent), false);
  });
}

test("a launch request always states the limit it was given and refuses an unusable one before the network", async () => {
  const bodies = [];
  globalThis.fetch = async (_url, init) => { bodies.push(JSON.parse(init.body)); return json({ launched: true }); };
  const client = api();
  await client.startRun({ ...intentFixture().input, wallTimeSeconds: 60 }, "at-the-lower-bound", new AbortController().signal);
  await client.startRun({ ...intentFixture().input, wallTimeSeconds: 3600 }, "at-the-upper-bound", new AbortController().signal);
  assert.deepEqual(bodies.map((body) => body.wall_time_seconds), [60, 3600]);

  assert.deepEqual([1, 10, 60].map((minutes) => runLimitSeconds(String(minutes))), [60, 600, 3600]);
  for (const typed of ["", " ", "0", "61", "600", "10.5", "-5", "+10", "1e1", "ten", "1,5"]) {
    assert.equal(runLimitSeconds(typed), null, typed);
  }
  assert.equal(runLimitSeconds(" 10 "), 600, "surrounding whitespace is not a different number");
  assert.equal(bodies.length, 2, "no unusable limit reached the network");
});
function trackerWire() {
  return { schema: "agent-commons.tracker.v1", sequence: 1, source_revision: `sha256:${"0".repeat(64)}`, truncated: false, state: "ready",
    tasks: ["accepted", "completed"].map((task_state, n) => ({ task_id: id("task", n), title: `${task_state} prerequisite`, task_state, readiness: "complete", dependency_task_ids: [], blocking_dependency_ids: [], owner_session_id: null, role_name: null, provider: null, profile_id: null, phase: null, awaits_human: false, next_action: "none", freshness: "fresh", evidence_state: "complete", gaps: [] })),
    edges: [], runs: [], attention: [], capacity: { state: "available", active: 0, limit: 1, queued: 0, queue_capacity: 1 },
    freshness: { generated_at: "2026-09-07T00:00:00Z", source_updated_at: "2026-09-07T00:00:00Z", state: "fresh", resume_gap: false },
    focus_task_ids: [], critical_path_task_ids: [], critical_path_basis: "dependency_depth_only", critical_path_predictive: false, gaps: [] };
}
test("actual tracker API supplies accepted/completed prerequisites to composer and creation preserves selected IDs", async () => {
  const client = api(), requests = [];
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return json(url.endsWith("/work/tracker") ? trackerWire() : { event_type: "task.created", entity_ref: { kind: "task", id: id("task", 2) }, event_id: id("evt", 2), revision: id("evt", 2) }); };
  const snapshot = await client.loadTracker(new AbortController().signal);
  const catalog = taskDependencyCatalog({ kind: "ready", snapshot, connection: "connected" });
  assert.deepEqual(catalog.tasks.map((task) => task.state), ["accepted", "completed"]);
  const draft = { title: "Next task", description: "After prerequisites", criteria: "Checked", dependencyIds: [taskA, taskB] };
  const markup = renderToStaticMarkup(createElement(TaskComposer, { draft, onChange() {}, errors: new Set(), busy: false, writesEnabled: true, tasks: catalog.tasks, dependencyStatus: catalog.status, onSubmit() {}, onClose() {}, onDependency() {}, text: (key) => strings.en[key] }));
  assert.match(markup, /accepted prerequisite/); assert.match(markup, /completed prerequisite/);
  assert.equal((markup.match(/checked=""/g) ?? []).length, 2);
  await client.createTask({ title: draft.title, description: draft.description, criteria: [draft.criteria], dependencyIds: draft.dependencyIds }, new AbortController().signal, "after-accepted");
  assert.deepEqual(JSON.parse(requests.at(-1).init.body).dependencies, [taskA, taskB]);
  assert.equal(requests.some((request) => request.url.endsWith("/launch")), false, "dependency catalog does not misuse launch eligibility");
  assert.equal(taskDependencyCatalog({ kind: "ready", snapshot: { ...snapshot, truncated: true }, connection: "connected" }).status, "partial");
  assert.equal(taskDependencyCatalog({ kind: "failure" }).status, "unavailable");
});
