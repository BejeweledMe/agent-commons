import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-editor-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--resolveJsonModule", "--allowSyntheticDefaultImports", "--outDir", compiled, resolve(root, "src/components/AgentSettings.tsx"), resolve(root, "src/agentSettings.ts"), resolve(root, "src/dialogStack.ts"), resolve(root, "src/agentBoardState.ts")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (name) => import(pathToFileURL(resolve(compiled, name + ".js")).href);
const { AgentEditorSession, agentBudgetIssue, parseAgentDetails, agentMethodFork, agentChanges, agentDraft, agentTabDirty } = await load("agentSettings");
const { ApiProblem, WorkApi } = await load("api");
const stack = await load("dialogStack");
const { AgentBoardSessions } = await load("agentBoardState");
const id = (kind, n = 1) => `${kind}.${String(n).padStart(26, "0")}`;
const agent = id("agent");
const ref = { kind: "role", source: "builtin", id: "qa-reviewer", version: "a".repeat(64) };
const skill = { ...ref, kind: "skill", id: "qa-testing" };
const content = { name: "QA", description: "Full guidance", group: "quality", system_prompt: "Retain this exact guidance", entry_skill: skill, core_skills: [skill], conditional_skills: [{ ref: skill, when: "When required" }] };
const detail = (extra = {}) => ({ schema: "agent_commons.agent-details.v1", id: agent, revision: id("evt"), name: "QA", profile_id: "codex-independent-reviewer", model: null, state: "active", context_mode: "fresh", lifetime: { kind: "persistent" }, grants: { create_roles: "deny", retire_roles: "deny", open_links: "ask" }, effective_grants: { create_roles: "deny", retire_roles: "deny", open_links: "ask" }, skills: [], tool_allowlist: [], turnover_budget: null, supervisor_agent_id: null, created_by_agent_id: null, origin: "human", approval: "human", specialization_ref: null, specialization_state: "none", specialization: null, profile_tools: ["commons_read_skill"], profile_skills: [], live_delegations: [], retirement_blockers: [], links: [], organization_children: [], ...extra });
const receipt = { event_id: id("evt", 2), revision: id("evt", 2), entity_ref: { kind: "agent", id: agent } };
const editorApi = (requestData) => ({ requestData, writeAgentSettings: (path, body, signal) => requestData(path, { method: "POST", body, signal }) });
const operation = (extra = {}) => ({ path: `/agents/${agent}/reconfigure`, body: { expected_revision: id("evt"), changes: { name: "Changed" }, reason: "Operator edit" }, key: "original-key", confirmed: false, ...extra });

test("full editor DTO preserves arrays beyond generic limit and rejects partial/oversize buffers", () => {
  const data = detail({ profile_tools: Array.from({ length: 60 }, (_, i) => `tool-${i}`), name: "Long legitimate name" });
  assert.equal(parseAgentDetails(data, agent).profile_tools.length, 60);
  for (const changed of [{ skills: ["[truncated]"] }, { schema: "generic" }, { grants: { ...data.grants, create_roles: "maybe" } }, { profile_tools: "partial" }, { name: "x".repeat(512 * 1024) }]) assert.throws(() => parseAgentDetails({ ...data, ...changed }, agent));
  assert.throws(() => parseAgentDetails(data, id("agent", 2)));
});

test("modern edit forks private exact version, preserves unrelated content and reviewer suffix, then versions its own role", () => {
  const saved = agentMethodFork(agent, ref, content, "abcdefgh-1234");
  assert.match(saved.id, /^agent-[a-z0-9-]+-reviewer$/); assert.ok(saved.id.length <= 64);
  assert.equal(saved.expected_version, null); assert.deepEqual(saved.fork_ref, ref); assert.deepEqual(saved.content, content);
  const own = { ...ref, source: "custom", id: saved.id, version: "b".repeat(64) };
  const next = agentMethodFork(agent, own, content, "next");
  assert.equal(next.id, own.id); assert.equal(next.expected_version, own.version); assert.equal(next.fork_ref, undefined);
  const draft = agentDraft(parseAgentDetails(detail(), agent)); draft.name = "New"; draft.supervisor_agent_id = id("agent", 3);
  assert.deepEqual(agentChanges(detail(), draft), { name: "New", supervisor_agent_id: id("agent", 3) });
});

test("constructing/rendering agent map sessions performs no eager details or library reads", async () => {
  let reads = 0; const api = editorApi(async () => { reads++; return detail(); });
  const sessions = Array.from({ length: 100 }, () => new AgentEditorSession(api, agent));
  assert.equal(reads, 0); await Promise.all([sessions[0].load(), sessions[0].load()]); assert.equal(reads, 1);
  const board = readFileSync(resolve(root, "src/components/ProjectBoard.tsx"), "utf8");
  assert.doesNotMatch(board, /readAgentDetails|\/work\/agents|\.load\(/);
  assert.match(board, /configure = useCallback/);
  assert.match(board, /active \|\| visited \? <ReactFlow/);
  assert.match(board, /panOnScroll zoomOnScroll=\{false\} zoomOnPinch/);
  const outputs = readFileSync(resolve(root, "src/components/OutputsPanel.tsx"), "utf8");
  assert.match(outputs, /if \(!owner \|\| !visible \|\| compact\) return/);
});

test("library confirmation survives a lost canonical response; retry keeps exact body, version and independent keys", async () => {
  const posts = []; let libraryWrites = 0, lost = true;
  const editor = new AgentEditorSession(editorApi(async (path, options) => {
    if (options.method !== "POST") return detail();
    posts.push(structuredClone(options.body)); if (lost) { lost = false; throw new TypeError("network lost"); } return receipt;
  }), agent);
  editor.library.save = async (input, key) => { libraryWrites++; assert.equal(key, "library-key"); return { ref: { ...ref, source: "custom", id: input.id, version: "b".repeat(64) }, content, files: [] }; };
  await editor.load();
  await editor.start(operation({ key: "bind-key", body: { expected_revision: id("evt"), reason: "Edit methods" }, library: { key: "library-key", input: agentMethodFork(agent, ref, content, "abcdefgh") } }));
  assert.equal(editor.state, "uncertain"); assert.equal(editor.locked, true); assert.ok(editor.operation.library.savedRef);
  editor.update({ name: "Cannot replace uncertain input" }); assert.equal(editor.draft.name, "QA");
  await editor.retry(); assert.equal(libraryWrites, 1); assert.deepEqual(posts[0], posts[1]); assert.equal(posts[0].idempotency_key, "bind-key"); assert.equal(posts[0].changes.specialization_ref.version, "b".repeat(64));
  assert.equal(editor.state, "saved");
});

test("confirmed mutation plus failed refresh only retries GET, and definite revision conflict preserves draft", async () => {
  let posts = 0, unavailable = false;
  const editor = new AgentEditorSession(editorApi(async (_path, options) => { if (options.method === "POST") { posts++; unavailable = true; return receipt; } if (unavailable) throw new TypeError("offline"); return detail(); }), agent);
  await editor.load(); editor.update({ name: "Changed" }); await editor.start(operation()); assert.equal(editor.state, "saved_refresh_failed");
  unavailable = false; await editor.retry(); assert.equal(posts, 1); assert.equal(editor.state, "saved");
  const conflict = new AgentEditorSession(editorApi(async (_path, options) => { if (options.method === "POST") throw new ApiProblem(409, { code: "revision_conflict", message: "", safeNextActions: [] }); return detail(); }), agent);
  await conflict.load(); conflict.update({ name: "Keep me" }); await conflict.start(operation()); assert.equal(conflict.state, "failed"); assert.equal(conflict.draft.name, "Keep me");
  conflict.editAfterRefusal(); assert.equal(conflict.operation, null); assert.equal(conflict.draft.name, "Keep me");
});

test("reloading a conflict preserves only edited fields while accepting other current server settings", async () => {
  let current = detail(); const editor = new AgentEditorSession(editorApi(async () => current), agent);
  await editor.load(); editor.update({ name: "My draft" }); current = detail({ revision: id("evt", 9), supervisor_agent_id: id("agent", 4) }); await editor.load(true);
  assert.equal(editor.draft.name, "My draft"); assert.equal(editor.draft.supervisor_agent_id, id("agent", 4)); assert.equal(editor.detail.revision, id("evt", 9));
});

test("nested dialog ownership leaves outer modal registered; detail surfaces never take a map column", () => {
  stack.resetDialogStack(); const outer = stack.pushDialog(), inner = stack.pushDialog();
  assert.equal(stack.isInnermostDialog(outer.id), false); assert.equal(stack.isInnermostDialog(inner.id), true); inner.release(); inner.release(); assert.equal(stack.dialogDepth(), 1); assert.equal(stack.isInnermostDialog(outer.id), true); outer.release(); assert.equal(stack.anyDialogOpen(), false);
  const modal = readFileSync(resolve(root, "src/components/Modal.tsx"), "utf8"), shell = readFileSync(resolve(root, "src/main.tsx"), "utf8"), tracker = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  assert.match(modal, /showModal\(\)/); assert.match(modal, /preventScroll: true/); assert.match(modal, /isInnermostDialog/);
  assert.match(shell, /anyDialogOpen\(\)/); assert.doesNotMatch(shell, /className="work-panel-strip"/); assert.doesNotMatch(shell, /route\.view === "board" && workspaceInitialized \? <ProjectBoard/);
  assert.match(tracker, /<Modal title=/); assert.doesNotMatch(tracker, /<aside className="work-panel/);
});


test("real WorkApi uses scoped narrow agent mutations while generic data transport stays closed", async () => {
  const calls = [], previous = globalThis.fetch;
  const project = `project.${"a".repeat(32)}`, base = `/api/${"b".repeat(32)}`;
  const api = new WorkApi(project, base), signal = new AbortController().signal;
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify(init.method === "GET" ? detail() : receipt), { headers: { "Content-Type": "application/json" } }); };
  try {
    const editor = new AgentEditorSession(api, agent); await editor.load(); await editor.start(operation());
    assert.equal(editor.state, "saved"); assert.equal(calls.filter((c) => c.init.method === "POST").length, 1);
    assert.equal(calls[1].url, `${base}/projects/${project}/agents/${agent}/reconfigure`);
    assert.equal(JSON.parse(calls[1].init.body).expected_revision, id("evt")); assert.equal(calls[1].init.credentials, "same-origin");
    await api.writeAgentSettings("/agent-links", { from_agent_id: agent, to_agent_id: id("agent", 2), allowed_action: "handoff_work", idempotency_key: "link" }, signal);
    await api.writeAgentSettings(`/agent-links/${id("agent_link")}/close`, { expected_revision: id("evt"), idempotency_key: "close" }, signal);
    const before = calls.length;
    for (const path of ["/agents/agent.invalid/reconfigure", `/agents/${agent}/retire`, "/tasks", "/agent-links/../close"]) await assert.rejects(api.writeAgentSettings(path, {}, signal));
    await assert.rejects(api.requestData(`/agents/${agent}/reconfigure`, { method: "POST", body: {}, signal }));
    assert.equal(calls.length, before);
  } finally { globalThis.fetch = previous; }
});


test("staff permissions require an explicit finite integer budget, including an intentional zero", () => {
  const draft = agentDraft(detail());
  assert.equal(agentBudgetIssue(draft), null);
  draft.grants.create_roles = "ask";
  assert.equal(agentBudgetIssue(draft), "agent_budget_required");
  for (const value of [0, 1, 1024]) { draft.turnover_budget = value; assert.equal(agentBudgetIssue(draft), null); }
  for (const value of [-1, 1.5, Infinity, NaN, 1025]) { draft.turnover_budget = value; assert.equal(agentBudgetIssue(draft), "agent_budget_invalid"); }
  draft.grants.create_roles = "deny"; draft.grants.retire_roles = "auto"; draft.turnover_budget = null;
  assert.equal(agentBudgetIssue(draft), "agent_budget_required");
});

test("confirmed link operations preserve drafts on other tabs through refresh and later refresh retries", async () => {
  for (const path of ["/agent-links", `/agent-links/${id("agent_link")}/close`]) {
    let failRead = false, posts = 0;
    const editor = new AgentEditorSession(editorApi(async (_path, options) => {
      if (options.method === "POST") { posts++; failRead = true; return { ...receipt, entity_ref: { kind: "agent_link", id: id("agent_link") } }; }
      if (failRead) throw new TypeError("offline after confirmation");
      return detail({ specialization_ref: ref });
    }), agent);
    editor.library.detail = async () => ({ content }); editor.library.load = async () => ({ editingEnabled: true, skills: [] });
    await editor.load(); editor.update({ name: "Private name", grants: { ...editor.draft.grants, open_links: "deny" } });
    editor.methods = { ...editor.methods, core_skills: [] };
    const methods = structuredClone(editor.methods);
    await editor.start(operation({ path, body: { reason: "link only" } }));
    assert.equal(editor.state, "saved_refresh_failed");
    failRead = false; await editor.retry(); await editor.load();
    assert.equal(posts, 1); assert.equal(editor.draft.name, "Private name"); assert.equal(editor.draft.grants.open_links, "deny");
    assert.deepEqual(editor.methods, methods); assert.equal(editor.detail.name, "QA");
    assert.equal(editor.hasDraftChanges, true, "an unrelated saved operation must not claim every field was saved");
  }
});

test("settings acknowledgement rebases submitted fields once and preserves methods and subsequent edits", async () => {
  let current = detail({ specialization_ref: ref });
  const editor = new AgentEditorSession(editorApi(async (_path, options) => {
    if (options.method === "POST") { current = { ...current, name: "Canonical spelling", revision: id("evt", 3) }; return receipt; }
    return current;
  }), agent);
  editor.library.detail = async () => ({ content }); editor.library.load = async () => ({ editingEnabled: true, skills: [] });
  await editor.load(); editor.update({ name: "Changed", supervisor_agent_id: id("agent", 3) });
  editor.methods = { ...editor.methods, core_skills: [] };
  await editor.start(operation());
  assert.equal(editor.draft.name, "Canonical spelling"); assert.equal(editor.draft.supervisor_agent_id, id("agent", 3));
  assert.deepEqual(editor.methods.core_skills, []);
  editor.update({ name: "Changed" }); await editor.load();
  assert.equal(editor.draft.name, "Changed", "a later same-valued draft is not acknowledged twice");
});

test("method bind preserves general edits and refreshes its own exact confirmed content", async () => {
  let current = detail({ specialization_ref: ref }), currentContent = content;
  const editor = new AgentEditorSession(editorApi(async (_path, options) => {
    if (options.method === "POST") { current = { ...current, specialization_ref: options.body.changes.specialization_ref }; return receipt; }
    return current;
  }), agent);
  editor.library.detail = async () => ({ content: currentContent }); editor.library.load = async () => ({ editingEnabled: true, skills: [] });
  editor.library.save = async (input) => { currentContent = structuredClone(input.content); return { ref: { ...ref, source: "custom", id: input.id, version: "b".repeat(64) }, content: currentContent }; };
  await editor.load(); editor.update({ name: "Unsaved name" }); editor.methods = { ...editor.methods, core_skills: [] };
  await editor.start(operation({ body: { expected_revision: id("evt") }, library: { key: "methods-key", input: agentMethodFork(agent, ref, editor.methods, "methods") } }));
  assert.equal(editor.draft.name, "Unsaved name"); assert.deepEqual(editor.methods, currentContent);
  assert.equal(editor.detail.specialization_ref.version, "b".repeat(64));
});

test("project A/B/A preserves distinct viewports, drafts and immutable uncertain editor and canvas operations", async () => {
  const projects = new AgentBoardSessions(), posts = [];
  let lost = true;
  const apiA = editorApi(async (_path, options) => {
    if (options.method !== "POST") return detail();
    posts.push(structuredClone(options.body)); if (lost) { lost = false; throw new TypeError("lost response"); } return receipt;
  });
  const a = projects.forProject("A"), editorA = a.editor(apiA, agent);
  a.setViewport({ x: 219, y: -90, zoom: .63 }); a.visited = true;
  await editorA.load(); editorA.update({ name: "Changed" }); await editorA.start(operation());
  a.setConnection({ source: agent, target: id("agent", 2), kind: "handoff_work", key: "canvas-original", confirmed: false }); a.setLinkState("uncertain");
  const b = projects.forProject("B"); b.setViewport({ x: -45, y: 22, zoom: 1.2 });
  const editorB = b.editor(editorApi(async () => detail({ name: "B" })), agent); await editorB.load();
  const restored = projects.forProject("A"), restoredEditor = restored.editor(apiA, agent);
  assert.equal(restoredEditor, editorA); assert.notEqual(restoredEditor, editorB); assert.equal(restoredEditor.state, "uncertain");
  assert.equal(restoredEditor.draft.name, "Changed"); assert.deepEqual(restored.viewport, { x: 219, y: -90, zoom: .63 });
  assert.deepEqual(b.viewport, { x: -45, y: 22, zoom: 1.2 }); assert.equal(restored.connection.key, "canvas-original"); assert.equal(restored.linkState, "uncertain");
  await restoredEditor.retry(); assert.deepEqual(posts[0], posts[1]); assert.equal(restoredEditor.state, "saved");
  const shell = readFileSync(resolve(root, "src/main.tsx"), "utf8"), board = readFileSync(resolve(root, "src/components/ProjectBoard.tsx"), "utf8");
  assert.match(shell, /agentBoardSessions = useRef\(new AgentBoardSessions\(\)\)/);
  assert.match(shell, /session=\{agentBoardSessions.current.forProject\(renderedProjectId\)\}/);
  assert.match(board, /defaultViewport=\{session.viewport \?\? undefined\} fitView=\{false\}/);
  assert.doesNotMatch(board, /useRef\(new Map<string, AgentEditorSession>/);
});


test("no-op settings and unchanged method content issue neither canonical nor library writes", async () => {
  let posts = 0, versions = 0;
  const editor = new AgentEditorSession(editorApi(async (_path, options) => {
    if (options.method === "POST") { posts++; return receipt; }
    return detail({ specialization_ref: ref });
  }), agent);
  editor.library.detail = async () => ({ content });
  editor.library.load = async () => ({ editingEnabled: true, skills: [] });
  editor.library.save = async () => { versions++; throw new Error("must not write unchanged methods"); };
  await editor.load();
  assert.equal(editor.configDirty, false); assert.equal(editor.methodsDirty, false);
  await editor.start(operation({ body: { expected_revision: id("evt"), changes: {} } }));
  await editor.start(operation({ library: { key: "unchanged", input: agentMethodFork(agent, ref, editor.methods, "no-op") } }));
  assert.equal(posts, 0); assert.equal(versions, 0); assert.equal(editor.operation, null);
  editor.update({ name: "Private name", grants: { ...editor.draft.grants, open_links: "deny" } });
  assert.deepEqual(agentTabDirty(editor.detail, editor.draft, editor.methodsDirty), { general: true, permissions: true, skills: false, connections: false });
  editor.methods = { ...editor.methods, conditional_skills: [{ ref: skill, when: "First line\nSecond line" }] };
  assert.equal(editor.methodsDirty, true);
  assert.equal(agentTabDirty(editor.detail, editor.draft, editor.methodsDirty).skills, true);
});

test("settings show independent save scopes, tab dirtiness and full multiline conditions", async () => {
  const { AgentSettings } = await load("components/AgentSettings");
  const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
  const editor = new AgentEditorSession(editorApi(async () => detail({ specialization_ref: ref })), agent);
  editor.library.detail = async () => ({ content });
  editor.library.load = async () => ({ editingEnabled: true, skills: [{ ref: skill, name: "Quality checks", description: "" }] });
  await editor.load();
  const render = (tab, locale = "en") => renderToStaticMarkup(createElement(AgentSettings, { session: editor, initialTab: tab, text: (key) => messages[locale][key], roles: [], writesEnabled: true, onClose() {}, onChanged() {} }));
  const clean = render("skills");
  assert.match(clean, /disabled=""[^>]*>Save skill methods<\/button>/);
  editor.update({ name: "Private name", context_mode: "accumulated" });
  editor.methods = { ...editor.methods, conditional_skills: [{ ref: skill, when: "First line\nSecond line" }] };
  for (const locale of ["en", "ru"]) {
    const html = render("skills", locale);
    assert.ok(html.includes(messages[locale].agent_methods_scope));
    assert.ok(html.includes(messages[locale].agent_tab_general + " · " + messages[locale].agent_tab_dirty));
    assert.match(html, /<textarea[^>]*rows="3"[^>]*>First line\nSecond line<\/textarea>/);
    const save = html.match(/<button[^>]*>[^<]*<\/button>/g).find((button) => button.includes(messages[locale].agent_save_methods));
    assert.doesNotMatch(save, /disabled/, "an unrelated general draft requiring a reason cannot block saving methods");
  }
  assert.match(render("general"), /disabled=""[^>]*>Save settings<\/button>/, "configuration still requires its own downgrade reason");
});
