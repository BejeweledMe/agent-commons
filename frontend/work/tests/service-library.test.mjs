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
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-service-library-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  resolve(root, "src/api.ts"), resolve(root, "src/libraryApi.ts"), resolve(root, "src/libraryEditorState.ts"), resolve(root, "src/rolePresetState.ts"), resolve(root, "src/components/SpecializationPicker.tsx"), resolve(root, "src/components/ServiceLibraryEditor.tsx"), resolve(root, "src/components/ServiceLibrarySection.tsx"), resolve(root, "src/components/LibrarySection.tsx"), resolve(root, "src/components/WorkBlueprintsSection.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path)).href);
const { WorkApi, ApiProblem, parseCatalog } = await load("api.js");
const { LibraryApi, parseServiceLibrary, parseLibraryDetail, parseWorkBlueprints, parseBlueprintApplication, blueprintBriefIsValid } = await load("libraryApi.js");
const { newLibraryDraft, editLibraryDraft, libraryDraftErrors, librarySaveInput, freezeLibrarySave, selectBlueprintProfile } = await load("libraryEditorState.js");
const { chooseRoleProvider, chooseRoleProfile } = await load("rolePresetState.js");
const { libraryRefKey } = await load("libraryTypes.js");
const { SpecializationPicker } = await load("components/SpecializationPicker.js");
const { ServiceLibraryEditor } = await load("components/ServiceLibraryEditor.js");
const { ServiceLibrarySection } = await load("components/ServiceLibrarySection.js");
const { LibrarySection } = await load("components/LibrarySection.js");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const ref = (kind, id, version = "a", source = "builtin") => ({ kind, id, source, version: version.repeat(64) });
const primary = ref("skill", "web-frontend-engineering");
const companion = ref("skill", "qa-testing");
const roleRef = ref("role", "frontend-engineer");
const skill = { ref: primary, name: "Frontend", description: "Build browser interfaces" };
const role = { ref: roleRef, name: "Frontend engineer", description: "Deliver a working browser path", group: "application", entry_skill: primary, core_skills: [primary], conditional_skills: [{ ref: companion, when: "Independent QA question" }] };
const catalogWire = () => ({ schema: "agent_commons.ui.library.v1", editing_enabled: true, roles: [role], skills: [skill, { ref: companion, name: "QA", description: "Checks" }] });
const catalog = () => parseServiceLibrary(catalogWire());
const detail = () => ({ ref: roleRef, content: { name: role.name, description: role.description, group: role.group, system_prompt: "Read the task and use the appropriate method.", entry_skill: primary, core_skills: [primary], conditional_skills: role.conditional_skills }, files: [] });
const entity = (kind, n = "1") => `${kind}.${"0".repeat(25)}${n}`;
const response = (payload, status = 200) => ({ ok: status === 200, status, json: async () => payload });
const signal = () => new AbortController().signal;

test("library metadata is closed and exact refs reject changed or malformed identities", () => {
  const input = catalogWire(); input.roles[0] = { ...role, system_prompt: "PRIVATE", tools: ["admin"] };
  const parsed = parseServiceLibrary(input);
  assert.equal(JSON.stringify(parsed).includes("PRIVATE"), false);
  assert.equal(JSON.stringify(parsed).includes("admin"), false);
  assert.deepEqual(parsed.roles[0].entry_skill, primary);
  assert.throws(() => parseServiceLibrary({ ...catalogWire(), skills: [{ ...skill, ref: { ...primary, version: "latest" } }] }), ApiProblem);
  assert.throws(() => parseLibraryDetail(detail(), { ...roleRef, version: "b".repeat(64) }), ApiProblem);
  assert.throws(() => parseLibraryDetail({ ...detail(), files: [{ path: "../secret", sha256: "a".repeat(64), size: 1 }] }), ApiProblem);
});

test("library capacity includes all service skills plus the maximum custom entries", () => {
  const skills = Array.from({ length: 557 }, (_, index) => ({ ...skill, ref: ref("skill", `method-${index}`, "a", index < 45 ? "builtin" : "custom") }));
  assert.equal(parseServiceLibrary({ ...catalogWire(), skills }).skills.length, 557);
  assert.throws(() => parseServiceLibrary({ ...catalogWire(), skills: Array.from({ length: 601 }, (_, index) => ({ ...skill, ref: ref("skill", `method-${index}`) })) }), ApiProblem);
});

test("builtin edits fork and custom updates retain exact referenced versions across catalog refresh", () => {
  const draft = editLibraryDraft(parseLibraryDetail(detail()));
  assert.equal(draft.id, "frontend-engineer-custom");
  assert.equal(draft.expectedVersion, null);
  assert.deepEqual(draft.forkRef, roleRef);
  const newer = parseServiceLibrary({ ...catalogWire(), skills: [{ ...skill, ref: { ...primary, version: "b".repeat(64) } }] });
  assert.deepEqual(libraryDraftErrors(draft, newer), []);
  const input = librarySaveInput(draft, newer);
  assert.deepEqual(input.content.entry_skill, primary);
  assert.deepEqual(input.content.conditional_skills[0].ref, companion);
  const custom = parseLibraryDetail({ ...detail(), ref: { ...roleRef, source: "custom" } });
  assert.equal(editLibraryDraft(custom).expectedVersion, roleRef.version);
  const fresh = newLibraryDraft("role");
  Object.assign(fresh, { id: "new-role", name: "New", instruction: "Instruction", entrySkill: libraryRefKey(primary) });
  assert.ok(libraryDraftErrors(fresh, newer).includes("entrySkill"));
});

test("skill fork preserves the resource origin and uncertain save retries exactly the frozen operation", async () => {
  const source = parseLibraryDetail({ ref: primary, content: { name: "Frontend", description: "Browser", instruction: "Original" }, files: [{ path: "references/state.md", sha256: "b".repeat(64), size: 20 }] });
  const draft = editLibraryDraft(source); draft.instruction = "My edited instruction";
  const input = librarySaveInput(draft, catalog());
  const operation = freezeLibrarySave(input, "same-save-key");
  draft.instruction = "Later unsent change";
  input.content.instruction = "Mutated caller input";
  const calls = []; let first = true;
  const client = new LibraryApi({ async requestData(path, options) { calls.push({ path, body: JSON.stringify(options.body) }); if (first) { first = false; throw new TypeError("lost response"); } return { ref: ref("skill", operation.input.id, "c", "custom"), content: operation.input.content }; } });
  await assert.rejects(() => client.save(operation.input, operation.key, signal()), TypeError);
  const saved = await client.save(operation.input, operation.key, signal());
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(JSON.parse(calls[0].body).content.instruction, "My edited instruction");
  assert.deepEqual(JSON.parse(calls[0].body).fork_ref, primary);
  assert.equal(saved.ref.source, "custom");
});

test("same specialization hires two exact instances with separately selected provider and model", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const bodies = [];
  globalThis.fetch = async (_url, init) => { const body = JSON.parse(init.body); bodies.push(body); return response({ entity_ref: { kind: "agent", id: entity("agent", String(bodies.length)) } }); };
  const base = { name: "Frontend A", profileId: "codex-builder", rationale: "Implement the UI", contextMode: "fresh", specializationRef: roleRef, modelMode: "explicit", model: "chosen-model" };
  const first = await client.createRole(base, signal(), "first");
  const second = await client.createRole({ ...base, name: "Frontend B", profileId: "claude-independent-reviewer", model: "other-model" }, signal(), "second");
  assert.notEqual(first.agentId, second.agentId);
  assert.deepEqual(bodies[0].specialization_ref, roleRef);
  assert.deepEqual(bodies[1].specialization_ref, roleRef);
  assert.equal(bodies[0].model, "chosen-model");
  assert.equal(bodies[1].profile_id, "claude-independent-reviewer");
  assert.equal(bodies.some((body) => "grants" in body || "skills" in body || "system_prompt" in body), false);
  await client.createRole({ ...base, modelMode: "profile" }, signal(), "third");
  assert.equal("model" in bodies[2], false);
  await assert.rejects(() => client.createRole({ ...base, fromPresetId: entity("agent") }, signal()), ApiProblem);
});

test("Team provider and profile switches retain the named specialization but never send a prior explicit model", async () => {
  const original = { name: "Frontend A", provider: "codex", profileId: "codex-builder", model: "codex-only-model", modelMode: "explicit", specializationRef: roleRef, specializationName: role.name, rationale: "Build the requested screen", contextMode: "fresh", fromPresetId: "" };
  const provider = chooseRoleProvider(original, "claude");
  assert.deepEqual({ provider: provider.provider, profileId: provider.profileId, model: provider.model, modelMode: provider.modelMode }, { provider: "claude", profileId: "", model: "", modelMode: "profile" });
  const chosen = chooseRoleProfile(provider, "claude-builder");
  const profile = chooseRoleProfile({ ...chosen, modelMode: "explicit", model: "builder-only-model" }, "claude-independent-reviewer");
  assert.equal(profile.provider, "claude"); assert.equal(profile.profileId, "claude-independent-reviewer");
  assert.equal(profile.model, ""); assert.equal(profile.modelMode, "profile");
  for (const current of [provider, profile]) {
    for (const key of ["name", "specializationName", "rationale", "contextMode"]) assert.equal(current[key], original[key]);
    assert.deepEqual(current.specializationRef, roleRef);
  }
  assert.equal(original.model, "codex-only-model", "the previous frozen request remains unchanged");
  assert.equal(chooseRoleProfile({ ...profile, model: "explicit", modelMode: "explicit" }, "").modelMode, "profile");
  const client = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const bodies = [];
  globalThis.fetch = async (_url, init) => { bodies.push(JSON.parse(init.body)); return response({ entity_ref: { kind: "agent", id: entity("agent") } }); };
  await client.createRole(chosen, signal(), "provider-switch");
  await client.createRole(profile, signal(), "profile-switch");
  assert.ok(bodies.every((body) => !("model" in body)));
  assert.deepEqual(bodies.map((body) => body.profile_id), ["claude-builder", "claude-independent-reviewer"]);
});

test("public authenticated transport cannot escape the opaque API prefix", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  let calls = 0; globalThis.fetch = async () => { calls++; return response({}); };
  for (const path of ["https://example.com", "//example.com", "/library/../setup", "/library/%2e%2e/setup", "/work/x?secret", "/work/x\\y"]) await assert.rejects(() => client.requestData(path, { signal: signal() }), ApiProblem);
  assert.equal(calls, 0);
  await client.requestData("/library", { signal: signal() }); assert.equal(calls, 1);
});

const bilingual = (value) => ({ en: value, ru: value });
const blueprint = () => ({ id: "web-app", version: "a".repeat(64), name: bilingual("Web app"), description: bilingual("Build an app"), slots: [{ id: "frontend", name: "Frontend", role_ref: roleRef }], tasks: [{ id: "define", title: bilingual("Define"), description: bilingual("Define the user path"), acceptance_criteria: { en: ["Defined"], ru: ["Defined"] }, depends_on: [], slot_id: "frontend" }, { id: "implement", title: bilingual("Implement"), description: bilingual("Implement the path"), acceptance_criteria: { en: ["Checked"], ru: ["Checked"] }, depends_on: ["define"], slot_id: "frontend" }] });
test("blueprint adapters validate the dependency graph and retain exact returned task/role pairs", () => {
  const parse = (item) => parseWorkBlueprints({ schema: "agent_commons.library-blueprints.v1", blueprints: [item] });
  assert.equal(parse(blueprint())[0].tasks.length, 2);
  const cycle = blueprint(); cycle.tasks[0].depends_on = ["implement"]; assert.throws(() => parse(cycle), ApiProblem);
  const absent = blueprint(); absent.tasks[1].depends_on = ["missing"]; assert.throws(() => parse(absent), ApiProblem);
  const application = parseBlueprintApplication({ schema: "agent_commons.blueprint-application.v1", blueprint_id: "web-app", state: "created", roles: [{ slot_id: "frontend", agent_id: entity("agent") }], tasks: [{ node_id: "define", task_id: entity("task"), agent_id: entity("agent") }] }, "web-app");
  assert.equal(application.tasks[0].taskId, entity("task")); assert.equal(application.tasks[0].agentId, entity("agent"));
});

test("blueprint brief is required, counts Unicode characters, and survives the exact lost-response retry", async () => {
  for (const brief of [undefined, "", "  ", "😀".repeat(4001)]) assert.equal(blueprintBriefIsValid(brief), false);
  assert.equal(blueprintBriefIsValid("😀".repeat(4000)), true);
  const draft = { expected_version: "a".repeat(64), title: "Community app", brief: "Help local readers exchange books. Audience: neighbours. Constraint: mobile browser.", locale: "en", bindings: [{ slot_id: "frontend", name: "Frontend A", profile_id: "codex-builder", model: null }] };
  const frozen = structuredClone(draft); draft.brief = "Later unsent draft";
  const calls = []; let first = true;
  const client = new LibraryApi({ async requestData(path, options) {
    calls.push({ path, body: JSON.stringify(options.body) });
    if (first) { first = false; throw new TypeError("lost response"); }
    return { schema: "agent_commons.blueprint-application.v1", blueprint_id: "web-app", state: "created", roles: [{ slot_id: "frontend", agent_id: entity("agent") }], tasks: [{ node_id: "define", task_id: entity("task"), agent_id: entity("agent") }] };
  } });
  await assert.rejects(() => client.applyBlueprint("web-app", frozen, "same-blueprint-key", signal()), TypeError);
  await client.applyBlueprint("web-app", frozen, "same-blueprint-key", signal());
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(JSON.parse(calls[1].body).brief, frozen.brief);
  assert.equal(JSON.parse(calls[1].body).idempotency_key, "same-blueprint-key");
  await assert.rejects(() => client.applyBlueprint("web-app", { ...frozen, brief: " " }, "invalid-key", signal()), ApiProblem);
  assert.equal(calls.length, 2);
});

test("blueprint profile switches clear provider-specific model overrides in defaults and only the chosen slot", () => {
  const defaults = { profile_id: "codex-builder", model: "gpt-model" };
  const switchedDefaults = selectBlueprintProfile(defaults, "claude-builder");
  assert.deepEqual(switchedDefaults, { profile_id: "claude-builder", model: null });
  assert.equal(defaults.model, "gpt-model", "a pending request's prior runtime choice remains immutable");
  const bindings = [
    { slot_id: "frontend", name: "Frontend A", ...defaults },
    { slot_id: "review", name: "Reviewer B", profile_id: "grok-reviewer", model: "grok-model" }
  ];
  const switched = bindings.map((binding) => binding.slot_id === "frontend" ? selectBlueprintProfile(binding, "claude-builder") : binding);
  assert.deepEqual(switched[0], { slot_id: "frontend", name: "Frontend A", profile_id: "claude-builder", model: null });
  assert.deepEqual(switched[1], bindings[1], "changing one slot must preserve another role's explicit model");
  assert.deepEqual(selectBlueprintProfile(switched[0], ""), { slot_id: "frontend", name: "Frontend A", profile_id: "", model: null });
  const appliedDefaults = switched.map((binding) => ({ ...binding, ...switchedDefaults }));
  assert.ok(appliedDefaults.every((binding) => binding.profile_id === "claude-builder" && binding.model === null));
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: library and hire use labelled native controls, exact stale choices and editable private instructions`, () => {
    const text = (key) => messages[locale][key];
    const picker = renderToStaticMarkup(createElement(SpecializationPicker, { library: { kind: "ready", catalog: catalog() }, value: { ...roleRef, version: "b".repeat(64) }, selectedName: "My role", onChange() {}, onRefresh() {}, text }));
    assert.ok(picker.includes('for="role-specialization"'));
    assert.ok(picker.includes(text("library_selection_unavailable")));
    assert.ok(picker.includes(roleRef.id + "/" + "b".repeat(64)));
    const editor = renderToStaticMarkup(createElement(ServiceLibraryEditor, { initialDraft: editLibraryDraft(parseLibraryDetail(detail())), catalog: catalog(), api: {}, onClose() {}, onSaved() {}, text, writesEnabled: true }));
    for (const id of ["library-edit-name", "library-edit-primary", "library-edit-instruction"]) assert.ok(editor.includes(`for="${id}"`));
    assert.ok(editor.includes(text("library_fork_help")));
    const section = renderToStaticMarkup(createElement(ServiceLibrarySection, { api: {}, state: { kind: "ready", catalog: catalog() }, onRefresh() {}, onChooseRole() {}, text, writesEnabled: true }));
    assert.ok(section.includes(text("library_new_skill")) === false);
    assert.ok(section.includes(text("library_new_role")));
    assert.ok(section.includes(text("library_choose_team")));
    assert.equal(section.includes("Read the task and use the appropriate method."), false);
    const library = renderToStaticMarkup(createElement(LibrarySection, { tab: "blueprints", onTabChange() {}, api: {}, libraryApi: {}, libraryState: { kind: "ready", catalog: catalog() }, onRefreshLibrary() {}, onChooseRole() {}, onApplied: async () => {}, onChooseTeam() {}, onBlueprintApplied() {}, text, locale, writesEnabled: true }));
    assert.equal((library.match(/role="tabpanel"/g) ?? []).length, 4);
    assert.ok(library.includes('aria-labelledby="library-tab-blueprints" class="library-panel" id="library-panel-blueprints"'));
    assert.ok(library.includes('aria-labelledby="library-tab-roles" class="library-panel" hidden=""'));
    assert.ok(library.includes('aria-labelledby="library-tab-skills" class="library-panel" hidden=""'));
    assert.ok(library.includes(text("library_new_role")) && library.includes(text("library_new_skill")));
    const skillDraft = editLibraryDraft(parseLibraryDetail({ ref: primary, content: { name: "Frontend", description: "Browser", instruction: "Skill text" }, files: [] }));
    const roleEditor = renderToStaticMarkup(createElement(ServiceLibraryEditor, { idPrefix: "library-role-edit", initialDraft: editLibraryDraft(parseLibraryDetail(detail())), catalog: catalog(), api: {}, onClose() {}, onSaved() {}, text, writesEnabled: true }));
    const skillEditor = renderToStaticMarkup(createElement(ServiceLibraryEditor, { idPrefix: "library-skill-edit", initialDraft: skillDraft, catalog: catalog(), api: {}, onClose() {}, onSaved() {}, text, writesEnabled: true }));
    const ids = [...(roleEditor + skillEditor).matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(ids.length, new Set(ids).size, "simultaneously mounted drafts must retain unique label targets");
  });
}

test("catalog exposes runtime model offers without pinning a specialization to a provider", () => {
  const parsed = parseCatalog({ profiles: ["codex-builder"], profile_info: { "codex-builder": { provider: "codex", model: "configured-model" } }, model_options: { codex: ["configured-model", "other-model"] } });
  assert.equal(parsed.profiles[0].provider, "codex");
  assert.equal(parsed.profiles[0].model, "configured-model");
  assert.deepEqual(parsed.modelOptions.codex, ["configured-model", "other-model"]);
});
