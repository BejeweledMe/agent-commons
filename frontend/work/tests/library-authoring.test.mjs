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
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-library-authoring-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/libraryApi.ts"), resolve(root, "src/libraryAuthoringState.ts"), resolve(root, "src/appRouteState.ts"), resolve(root, "src/components/SkillGroups.tsx"), resolve(root, "src/components/BlueprintAuthoring.tsx"), resolve(root, "src/components/BlueprintDefinitionEditor.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path)).href);
const { ApiProblem } = await load("api.js");
const { LibraryApi, parseServiceLibrary, parseSkillOrganization, parseWorkBlueprints } = await load("libraryApi.js");
const { LibraryMutation, libraryMutation, libraryDraftMemory, groupedSkills, blueprintDraft, blueprintDraftErrors, blueprintSaveInput } = await load("libraryAuthoringState.js");
const { parseWorkRoute, workRouteHref } = await load("appRouteState.js");
const { SkillGroups } = await load("components/SkillGroups.js");
const { BlueprintAuthoring } = await load("components/BlueprintAuthoring.js");
const { BlueprintDefinitionEditor } = await load("components/BlueprintDefinitionEditor.js");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const ref = (kind, id, version = "a") => ({ kind, id, source: "builtin", version: version.repeat(64) });
const roleRef = ref("role", "engineer");
const skill = { ref: ref("skill", "method"), name: "Method", description: "Build interfaces" };
const role = { ref: roleRef, name: "Engineer", description: "Build", group: "application", entry_skill: skill.ref, core_skills: [skill.ref], conditional_skills: [] };
const organization = () => ({ schema: "agent_commons.skill-organization.v1", revision: "a".repeat(64), groups: [{ id: "application", name: { en: "Application", ru: "Приложения" }, source: "builtin" }, { id: "empty", name: { en: "Our group", ru: "Наша группа" }, source: "custom" }], assignments: [{ source: "builtin", id: "method", group_id: "application" }] });
const catalogWire = () => ({ schema: "agent_commons.ui.library.v1", roles: [role], skills: [skill], editing_enabled: true, skill_organization: organization() });
const catalog = () => parseServiceLibrary(catalogWire());
const tx = (value) => ({ en: value, ru: value });
const plan = () => ({ id: "workflow", version: "a".repeat(64), source: "custom", archived: false, name: tx("Workflow"), description: tx("Deliver the project"), slots: [{ id: "builder", name: "Engineer", role_ref: roleRef }], tasks: [{ id: "first", title: tx("First task"), description: tx("Build it"), acceptance_criteria: { en: ["Checked"], ru: ["Проверено"] }, slot_id: "builder", depends_on: [] }, { id: "next", title: tx("Next task"), description: tx("Verify it"), acceptance_criteria: { en: ["Checked"], ru: ["Проверено"] }, slot_id: "builder", depends_on: ["first"] }] });
const noop = () => {};

test("optional organization is backward compatible and rejects dangling or duplicate assignments", () => {
  const old = catalogWire(); delete old.skill_organization;
  assert.equal(parseServiceLibrary(old).skillOrganization, undefined);
  assert.deepEqual(catalog().skills[0].ref, skill.ref);
  const dangling = organization(); dangling.assignments[0].group_id = "missing";
  assert.throws(() => parseSkillOrganization(dangling), ApiProblem);
  const repeated = organization(); repeated.assignments.push(repeated.assignments[0]);
  assert.throws(() => parseSkillOrganization(repeated), ApiProblem);
  const missing = organization(); missing.assignments = [];
  assert.throws(() => parseSkillOrganization(missing, [skill]), ApiProblem);
});

test("grouped search keeps empty groups and matches translated group names", () => {
  assert.equal(groupedSkills([skill], organization(), "", "en").length, 2);
  assert.equal(groupedSkills([skill], organization(), "application", "en")[0].skills[0].ref, skill.ref);
  assert.equal(groupedSkills([skill], organization(), "наша", "ru")[0].skills.length, 0);
  assert.equal(groupedSkills([skill], organization(), "no-match", "en").length, 0);
});

test("metadata-only blueprints expose counts without leaking content or enabling definition copy", () => {
  const metadata = { ...plan(), slot_count: 2, task_count: 3, content_available: false, description: tx("WITHHELD"), slots: [{ secret: "WITHHELD" }], tasks: [{ secret: "WITHHELD" }] };
  const parsed = parseWorkBlueprints({ schema: "agent_commons.library-blueprints.v1", blueprints: [metadata] })[0];
  assert.equal(parsed.contentAvailable, false);
  assert.equal(parsed.taskCount, 3);
  assert.equal(JSON.stringify(parsed).includes("WITHHELD"), false);
  assert.throws(() => blueprintDraft(parsed));
  const html = renderToStaticMarkup(createElement(BlueprintAuthoring, { api: {}, catalog: catalog(), selected: parsed, plans: [parsed], locale: "en", text: (key) => messages.en[key], editable: true, applying: false, projectId: "restricted", onRefresh: noop }));
  assert.ok(!html.includes(messages.en.library_blueprint_copy));
  assert.ok(!html.includes(messages.en.library_edit_custom));
});

test("blueprint catalog supports all builtins and 128 custom definitions", () => {
  const values = Array.from({ length: 133 }, (_, index) => ({ ...plan(), id: `workflow-${index}` }));
  assert.equal(parseWorkBlueprints({ schema: "agent_commons.library-blueprints.v1", blueprints: values }).length, 133);
  assert.throws(() => parseWorkBlueprints({ schema: "agent_commons.library-blueprints.v1", blueprints: [...values, { ...plan(), id: "extra" }] }), ApiProblem);
});

test("draft validation checks role pins, criteria, missing dependencies and cycles", () => {
  const draft = blueprintDraft(plan());
  assert.deepEqual(blueprintDraftErrors(draft, catalog()), []);
  const updatedCatalog = catalog(); updatedCatalog.roles = [{ ...role, ref: ref("role", "engineer", "b") }];
  assert.deepEqual(blueprintDraftErrors(draft, updatedCatalog), []);
  assert.deepEqual(blueprintSaveInput(draft).definition.slots[0].role_ref, roleRef);
  const missingRole = structuredClone(draft); missingRole.slots[0].roleRef = ref("role", "absent");
  assert.ok(blueprintDraftErrors(missingRole, catalog()).includes("roles"));
  const cycle = structuredClone(draft); cycle.tasks[0].depends_on = ["next"];
  assert.ok(blueprintDraftErrors(cycle, catalog()).includes("cycle"));
  const missing = structuredClone(draft); missing.tasks[0].depends_on = ["missing"];
  assert.ok(blueprintDraftErrors(missing, catalog()).includes("dependencies"));
  const criteria = structuredClone(draft); criteria.tasks[0].acceptance_criteria.ru = [];
  assert.ok(blueprintDraftErrors(criteria, catalog()).includes("text"));
  const copy = blueprintDraft({ ...plan(), source: "builtin" });
  assert.equal(copy.expectedVersion, null);
  assert.equal(copy.forkRef.source, "builtin");
  assert.equal(copy.id, "workflow-copy");
});

test("lost mutation response locks a frozen request until exact retry succeeds", async () => {
  const mutation = new LibraryMutation(), calls = [];
  let first = true;
  const api = { async saveSkillGroup(input, key) { calls.push({ body: JSON.stringify(input), key }); if (first) { first = false; throw new TypeError("lost response"); } return organization(); } };
  const input = { kind: "group", input: { id: "new", name: tx("Original"), expected_revision: "a".repeat(64) } };
  await mutation.start(input, api, "same-key");
  assert.equal(mutation.snapshot().status, "uncertain");
  input.input.name.en = "Changed caller";
  assert.ok(Object.isFrozen(mutation.snapshot().operation.change.input.name));
  mutation.reset(); assert.equal(mutation.snapshot().status, "uncertain");
  await mutation.start(input, api, "other-key"); assert.equal(calls.length, 1);
  await mutation.retry();
  assert.deepEqual(calls[0], calls[1]); assert.equal(mutation.snapshot().status, "saved");
  await mutation.retry(); assert.equal(calls.length, 2);
});

test("unmounted project mutation remains recoverable and late success cannot alter another project", async () => {
  const first = libraryMutation("project-A-test"), second = libraryMutation("project-B-test");
  let release;
  const pending = first.start({ kind: "archive", id: "workflow", input: { expected_version: "a".repeat(64), archived: true } }, { archiveBlueprint: () => new Promise((resolve) => { release = resolve; }) }, "archive-key");
  assert.equal(first.snapshot().status, "saving");
  release({ ...plan(), archived: true }); await pending;
  assert.equal(libraryMutation("project-A-test").snapshot().status, "saved");
  assert.equal(second.snapshot().status, "idle");
  try { throw new TypeError("catalog refresh failed"); } catch {}
  assert.equal(first.snapshot().status, "saved");
});

test("API mutations retain separate version and organization revision contracts", async () => {
  const calls = [];
  const api = new LibraryApi({ async requestData(path, options) { calls.push({ path, body: options.body }); return path.includes("organization") ? organization() : plan(); } });
  const signal = new AbortController().signal;
  await api.saveSkillGroup({ id: "empty", name: tx("Group"), expected_revision: "b".repeat(64) }, "group-key", signal);
  await api.moveSkill({ skill: { source: "builtin", id: "method" }, group_id: "empty", expected_revision: "c".repeat(64) }, "move-key", signal);
  await api.saveBlueprint(blueprintSaveInput(blueprintDraft(plan())), "save-key", signal);
  await api.archiveBlueprint("workflow", { expected_version: plan().version, archived: true }, "archive-key", signal);
  assert.deepEqual(calls.map((item) => item.path), ["/library/organization/groups", "/library/organization/move", "/library/blueprints/custom", "/library/blueprints/custom/workflow/archive"]);
  assert.equal(calls[0].body.expected_revision, "b".repeat(64));
  assert.equal(calls[2].body.expected_version, plan().version);
  assert.equal(calls[3].body.idempotency_key, "archive-key");
});

for (const locale of ["en", "ru"]) test(`${locale}: group and blueprint editors use labelled controls and show empty groups`, () => {
  const text = (key) => { assert.equal(typeof messages[locale][key], "string", key); return messages[locale][key]; };
  const props = { skills: [skill], organization: organization(), search: "", api: {}, locale, editable: true, projectId: `render-${locale}`, text, onOpen: noop, onRefresh: noop };
  const html = renderToStaticMarkup(createElement(SkillGroups, props));
  assert.match(html, /<details class="skill-group"/);
  assert.ok(html.includes(messages[locale].library_group_empty));
  assert.match(html, /aria-label="[^"]+Method"/);
  const editor = renderToStaticMarkup(createElement(BlueprintDefinitionEditor, { draft: blueprintDraft(plan()), catalog: catalog(), plans: [plan()], locale, text, locked: false, onChange: noop, onSave: noop, onClose: noop }));
  assert.match(editor, /<legend>/); assert.match(editor, /type="checkbox"/);
  assert.ok(editor.includes(messages[locale].library_task_criteria));
  const readOnly = renderToStaticMarkup(createElement(SkillGroups, { ...props, editable: false }));
  assert.match(readOnly, /<select[^>]*disabled/);
});

test("legacy Design links become Work routes with only valid project and task scope", () => {
  const task = `task.${"0".repeat(26)}`;
  const route = parseWorkRoute(`?view=library&tab=design&task=${task}&project=project.${"a".repeat(32)}`);
  assert.equal(route.view, "work"); assert.equal(route.taskId, task); assert.equal(route.libraryTab, "roles");
  assert.ok(!workRouteHref(route).includes("design"));
  assert.equal(parseWorkRoute("?view=library&tab=design&task=../secret").taskId, null);
});
