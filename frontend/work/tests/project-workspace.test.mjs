import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "commons-project-workspace-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--outDir", compiled, resolve(root, "src/api.ts"), resolve(root, "src/appRouteState.ts"), resolve(root, "src/projectWorkspace.ts")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const workspace = await import(pathToFileURL(resolve(compiled, "projectWorkspace.js")));
const apiModule = await import(pathToFileURL(resolve(compiled, "api.js")));
const routes = await import(pathToFileURL(resolve(compiled, "appRouteState.js")));
const apiSource = readFileSync(resolve(root, "src/api.ts"), "utf8");
const shellSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");
const contextSource = readFileSync(resolve(root, "src/components/ContextPacksSection.tsx"), "utf8");
const blueprintSource = readFileSync(resolve(root, "src/components/WorkBlueprintsSection.tsx"), "utf8");

const projectA = `project.${"a".repeat(32)}`;
const projectB = `project.${"b".repeat(32)}`;
// This is the registry revision emitted by ProjectRegistry, not an event id.
const registryRevision = `sha256:${"c".repeat(64)}`;
const workspaceId = "workspace.01ARZ3NDEKTSV4RRFFQ69G5FAV";

function project(id, name) {
  return { id, workspace_id: workspaceId, name, revision: registryRevision, archived: false, available: true, state: "ready" };
}

test("project registry parser owns the frozen list wire contract", () => {
  const parsed = workspace.parseProjectList({ schema: "agent_commons.project_list.v1", revision: registryRevision, default_project_id: projectA, read_only: false, projects: [project(projectA, "A"), project(projectB, "B")] });
  assert.equal(parsed.defaultProjectId, projectA);
  assert.equal(parsed.projects[1].name, "B");
  assert.throws(() => workspace.parseProjectList({ schema: "agent_commons.project_list.v1", revision: registryRevision, default_project_id: projectA, read_only: false, projects: [project(projectA, "A")], path: "/private" }), /ApiProblem/);
});

test("project names follow the host's Unicode character bound, not an accidental UTF-8 byte bound", () => {
  const cyrillic = "Ж".repeat(160);
  const astral = "😀".repeat(160);
  for (const name of [cyrillic, astral]) {
    const parsed = workspace.parseProjectList({ schema: "agent_commons.project_list.v1", revision: registryRevision, default_project_id: projectA, read_only: false, projects: [project(projectA, name)] });
    assert.equal(parsed.projects[0].name, name);
  }
  assert.throws(() => workspace.parseProjectList({ schema: "agent_commons.project_list.v1", revision: registryRevision, default_project_id: projectA, read_only: false, projects: [project(projectA, "Ж".repeat(161))] }), /ApiProblem/);
});

test("A-to-B selection aborts delayed A work and per-project drafts never cross", () => {
  const selection = new workspace.ProjectSelection();
  const a = selection.select();
  const drafts = new workspace.ProjectDraftStore();
  drafts.set(projectA, { task: "A draft" });
  const b = selection.select();
  assert.equal(a.signal.aborted, true);
  assert.equal(selection.isCurrent(a.generation), false);
  assert.equal(selection.isCurrent(b.generation), true);
  assert.deepEqual(drafts.get(projectB, { task: "empty" }), { task: "empty" });
  assert.deepEqual(drafts.get(projectA, { task: "empty" }), { task: "A draft" });
});

test("a delayed A mutation success, error, and finally cannot commit after B becomes current", async () => {
  const selection = new workspace.ProjectSelection();
  const a = selection.select();
  let resolveA;
  const delayedA = new Promise((resolve) => { resolveA = resolve; });
  const committed = [];
  const settle = delayedA.then(() => {
    if (selection.isCurrent(a.generation)) committed.push("A-success");
  }).catch(() => {
    if (selection.isCurrent(a.generation)) committed.push("A-error");
  }).finally(() => {
    if (selection.isCurrent(a.generation)) committed.push("A-finally");
  });
  const b = selection.select();
  resolveA();
  await settle;
  assert.equal(b.signal.aborted, false);
  assert.deepEqual(committed, [], "A callbacks must be ignored while B is selected");
});

test("a same-project refresh replaces only its read request and leaves the mutation epoch current", () => {
  const selection = new workspace.ProjectSelection();
  const project = selection.select();
  const reads = new workspace.ProjectReadRequest();
  const first = reads.begin();
  const refreshed = reads.begin();
  assert.equal(first.signal.aborted, true, "a refresh supersedes the old read");
  assert.equal(reads.isCurrent(refreshed.generation), true);
  assert.equal(selection.isCurrent(project.generation), true, "a refresh must not invalidate a same-project mutation");
});

test("returning to A retains its exact RAM-only uncertain intent without exposing it to B", () => {
  const drafts = new workspace.ProjectDraftStore();
  const intent = { key: "launch-A-key", input: { taskId: "task-A", agentId: "role-A" }, uncertain: true };
  drafts.set(projectA, intent);
  assert.equal(drafts.get(projectB, null), null);
  assert.deepEqual(drafts.get(projectA, null), intent, "retry must retain its original body and idempotency key");
});

test("Back/Forward-safe route carries only the opaque project identity", () => {
  const state = routes.parseWorkRoute(`?project=${projectA}&view=team&task=task.01ARZ3NDEKTSV4RRFFQ69G5FAV`);
  assert.equal(state.projectId, projectA);
  assert.equal(routes.workRouteHref(state), `/work?project=${projectA}&view=team&task=task.01ARZ3NDEKTSV4RRFFQ69G5FAV`);
  assert.equal(routes.sanitizedWorkLocation("/work", `?project=${projectA}&brief=private&path=%2Fsecret`), `/work?project=${projectA}`);
});

test("host transport and immutable scoped clients prevent missing entities from clearing authentication", () => {
  assert.match(apiSource, /forProject\(projectId: string\): WorkApi/);
  assert.match(apiSource, /return new WorkApi\(projectId, this\.apiBase\)/);
  assert.match(apiSource, /return `\/projects\/\$\{encodeURIComponent\(this\.scopedProjectId\)\}\$\{path\}`/);
  assert.doesNotMatch(apiSource, /response\.status === 401 \|\| response\.status === 404/);
  assert.match(shellSource, /projectSelectionRef\.current\.select\(\)/);
  assert.match(shellSource, /const projectGeneration = projectSelectionRef\.current\.currentGeneration\(\)/);
  assert.match(shellSource, /readRequestRef\.current\.isCurrent\(read\.generation\)/);
});

test("each immutable project client owns a qualified tracker EventSource and its close handle", () => {
  const original = globalThis.EventSource;
  const opened = [];
  class Stream {
    constructor(url, options) { this.url = url; this.options = options; this.closed = false; opened.push(this); }
    addEventListener() {}
    close() { this.closed = true; }
  }
  globalThis.EventSource = Stream;
  try {
    const base = "/api/abcdefghijklmnopqrstuvwxyz123456";
    const alpha = new apiModule.WorkApi(projectA, base);
    const beta = new apiModule.WorkApi(projectB, base);
    const closeAlpha = alpha.openTrackerStream(() => {}, () => {}, () => {});
    const closeBeta = beta.openTrackerStream(() => {}, () => {}, () => {});
    const closeLegacy = new apiModule.WorkApi(null, base).openTrackerStream(() => {}, () => {}, () => {});
    assert.deepEqual(opened.map((stream) => stream.url), [
      `${base}/projects/${projectA}/work/tracker/stream`,
      `${base}/projects/${projectB}/work/tracker/stream`,
      `${base}/work/tracker/stream`
    ]);
    closeAlpha(); assert.equal(opened[0].closed, true); assert.equal(opened[1].closed, false);
    closeBeta(); closeLegacy(); assert.equal(opened[1].closed, true); assert.equal(opened[2].closed, true);
  } finally { globalThis.EventSource = original; }
});

test("the shell clears the old project view before a scoped load and pins async UI commits", () => {
  assert.match(shellSource, /setState\(\{ kind: "checking" \}\);/);
  assert.match(shellSource, /const api = apiRef\.current;[\s\S]*api\.createRole\(roleInput, signal, roleKey\)/);
  assert.match(shellSource, /pendingLaunchRetry: pendingLaunchRetry\.current/);
  assert.match(shellSource, /const attemptGeneration = projectSelectionRef\.current\.currentGeneration\(\)/);
  assert.match(shellSource, /if \(!stillCurrent\(\)\) return;[\s\S]*replaceProviderStatus\(status\)/);
  assert.match(shellSource, /const projectId = routeRef\.current\.projectId;[\s\S]*const submit = async[\s\S]*if \(routeRef\.current\.projectId !== projectId\) return;[\s\S]*const generation = projectSelectionRef\.current\.currentGeneration\(\)/);
  assert.match(shellSource, /navigate\(\{ projectId, taskId: selectedTaskId, composer: false \}\)/);
});

test("Library editors retain RAM-only drafts and exact uncertain retry identities by project", () => {
  assert.match(contextSource, /const sessions = new Map<string, ContextPackSession>\(\)/);
  assert.match(contextSource, /retry: retryIdentity\.current\.snapshot\(\)/);
  assert.match(contextSource, /sessions\.set\(sessionKey, snapshot\.current\)/);
  assert.match(contextSource, /restored\?\.save\.kind === "saving"[\s\S]*uncertain: true/);
  assert.match(blueprintSource, /const sessions = new Map<string, BlueprintSession>\(\)/);
  assert.match(blueprintSource, /if \(!mounted\.current\) return;/);
  assert.match(blueprintSource, /restored\?\.status === "applying"[\s\S]*"uncertain"/);
});
