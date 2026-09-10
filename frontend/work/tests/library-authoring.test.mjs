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
const { LibraryMutation, libraryMutation, libraryDraftMemory, groupedSkills, archivedGroups, moveTargets, groupMemberCount, blueprintDraft, blueprintDraftErrors, blueprintSaveInput, sameText, sameLines, blueprintLegacyMismatch } = await load("libraryAuthoringState.js");
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

test("archived groups arrive as an additive flag: absent means active, non-boolean is refused", () => {
  const parsed = parseSkillOrganization(organization());
  assert.deepEqual(parsed.groups.map((group) => group.archived), [false, false], "a catalog without the field is all active");
  const flagged = organization(); flagged.groups[1] = { ...flagged.groups[1], archived: true };
  assert.equal(parseSkillOrganization(flagged).groups[1].archived, true);
  const invalid = organization(); invalid.groups[1] = { ...invalid.groups[1], archived: "yes" };
  assert.throws(() => parseSkillOrganization(invalid), ApiProblem);
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

// The shared fixture carries distinct EN/RU texts, which the single-entry editor now
// reads as a legacy record; validation of the rest starts from one text per value.
const singleText = (draft) => ({ ...draft, name: sameText(draft.name.en), description: sameText(draft.description.en), tasks: draft.tasks.map((task) => ({ ...task, title: sameText(task.title.en), description: sameText(task.description.en), acceptance_criteria: sameLines(task.acceptance_criteria.en) })) });
test("draft validation checks role pins, criteria, missing dependencies and cycles", () => {
  const draft = singleText(blueprintDraft(plan()));
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
  // One entered value fills both slots, so differing slots are a legacy record, not missing text.
  const criteria = structuredClone(draft); criteria.tasks[0].acceptance_criteria.ru = [];
  assert.ok(blueprintDraftErrors(criteria, catalog()).includes("legacy"));
  const emptyCriteria = structuredClone(draft); emptyCriteria.tasks[0].acceptance_criteria = { en: [], ru: [] };
  assert.ok(blueprintDraftErrors(emptyCriteria, catalog()).includes("text"));
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

// User-authored text is entered once, in whatever language the author writes, and is
// stored into both locale slots without translation. Built-in catalog copy stays bilingual.
const groupProps = (overrides = {}) => ({
  skills: [skill], organization: organization(), search: "", api: {}, locale: "en", editable: true,
  projectId: "authoring", text: (key) => messages.en[key], onOpen: noop, onRefresh: noop, ...overrides
});

test("a custom group is authored in one field and no per-locale name label survives", () => {
  for (const key of ["library_name_en", "library_name_ru", "library_text_language"]) {
    assert.equal(messages.en[key], undefined, key);
    assert.equal(messages.ru[key], undefined, key);
  }
  libraryDraftMemory.set("skill-groups:single", { id: "ours", name: "Наша группа", legacy: null, rename: false });
  const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ projectId: "single" })));
  assert.ok(html.includes(messages.en.library_text_single_help));
  assert.equal(html.split('maxLength="200"').length - 1, 1);
  assert.ok(html.includes('value="Наша группа"'));
  assert.ok(!html.includes(messages.en.library_legacy_conflict));
  assert.deepEqual(sameText("Наша группа"), { en: "Наша группа", ru: "Наша группа" });
  libraryDraftMemory.set("skill-groups:single", null);
});

test("a group whose stored texts differ shows both and blocks the single field until one is chosen", () => {
  libraryDraftMemory.set("skill-groups:legacy", { id: "ours", name: "", legacy: { en: "Our group", ru: "Наша группа" }, rename: true });
  const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ projectId: "legacy" })));
  assert.ok(html.includes(messages.en.library_legacy_conflict));
  assert.match(html, /class="legacy-text-value">Our group</);
  assert.match(html, /class="legacy-text-value">Наша группа</);
  assert.equal(html.split(messages.en.library_legacy_use).length - 1, 2);
  assert.match(html, /<input[^>]*maxLength="200"[^>]*disabled/);
  libraryDraftMemory.set("skill-groups:legacy", null);
});

test("built-in group names stay bilingual while an authored name renders as typed under both locales", () => {
  const authored = organization();
  authored.groups[1] = { id: "empty", name: tx("Наша группа"), source: "custom" };
  for (const locale of ["en", "ru"]) {
    const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ organization: authored, locale, projectId: `display-${locale}`, text: (key) => messages[locale][key] })));
    assert.ok(html.includes("Наша группа"));
    assert.ok(!html.includes(messages[locale].library_legacy_conflict));
  }
  const builtin = renderToStaticMarkup(createElement(SkillGroups, groupProps({ locale: "ru", projectId: "builtin-ru", text: (key) => messages.ru[key] })));
  assert.ok(builtin.includes("Приложения"));
  assert.ok(!builtin.includes("Application"));
});

test("blueprint text is entered once, fills both slots, and a legacy mismatch demands a choice", () => {
  const draft = blueprintDraft(plan());
  draft.name = sameText("Наш процесс");
  draft.description = sameText("Сделать проект");
  draft.tasks = draft.tasks.map((task) => ({ ...task, title: sameText(task.title.en), description: sameText(task.description.en), acceptance_criteria: sameLines(["Проверено"]) }));
  assert.equal(blueprintLegacyMismatch(draft), false);
  assert.deepEqual(blueprintDraftErrors(draft, catalog()), []);
  const saved = blueprintSaveInput(draft).definition;
  assert.equal(saved.name.en, saved.name.ru);
  assert.equal(saved.description.en, saved.description.ru);
  for (const task of saved.tasks) {
    assert.equal(task.title.en, task.title.ru);
    assert.equal(task.description.en, task.description.ru);
    assert.deepEqual(task.acceptance_criteria.en, task.acceptance_criteria.ru);
  }
  const legacy = blueprintDraft({ ...plan(), name: { en: "Workflow", ru: "Процесс" } });
  assert.equal(blueprintLegacyMismatch(legacy), true);
  assert.ok(blueprintDraftErrors(legacy, catalog()).includes("legacy"));
  const editor = renderToStaticMarkup(createElement(BlueprintDefinitionEditor, { draft: legacy, catalog: catalog(), plans: [plan()], text: (key) => messages.en[key], locked: false, onChange: noop, onSave: noop, onClose: noop }));
  assert.ok(editor.includes(messages.en.library_text_single_help));
  assert.ok(editor.includes(messages.en.library_legacy_conflict));
  assert.match(editor, /class="legacy-text-value">Workflow</);
  assert.match(editor, /class="legacy-text-value">Процесс</);
  assert.match(editor, /<input[^>]*maxLength="200"[^>]*disabled/);
});

// Archive and restore of a custom skill group. The server owns the decision; the client
// only chooses the target group and never hides a group before the returned catalog.
const withArchivedGroup = () => {
  const value = organization();
  value.groups[1] = { ...value.groups[1], archived: true };
  return value;
};
const openArchive = (projectId, id, target = "") => libraryDraftMemory.set(`skill-groups-archive:${projectId}`, { id, target });
const controlTag = (markup, label) => {
  const end = markup.indexOf(label);
  return markup.slice(markup.lastIndexOf("<button", end), end);
};

test("an empty group archives with no target and restore sends archived:false", async () => {
  const calls = [];
  const api = { async archiveSkillGroup(id, input, key) { calls.push({ id, input, key }); return withArchivedGroup(); } };
  const mutation = new LibraryMutation();
  await mutation.start({ kind: "group_archive", id: "empty", input: { expected_revision: "a".repeat(64), archived: true } }, api, "archive-empty");
  assert.equal(mutation.snapshot().status, "saved");
  assert.deepEqual(calls[0], { id: "empty", input: { expected_revision: "a".repeat(64), archived: true }, key: "archive-empty" });
  assert.equal("move_to_group_id" in calls[0].input, false, "an empty group asks for no target");
  const restore = new LibraryMutation();
  await restore.start({ kind: "group_archive", id: "empty", input: { expected_revision: "b".repeat(64), archived: false } }, api, "restore-empty");
  assert.equal(restore.snapshot().status, "saved");
  assert.deepEqual(calls[1].input, { expected_revision: "b".repeat(64), archived: false });
});

test("group archive posts the revision-bound body with the chosen target to the group route", async () => {
  const calls = [];
  const api = new LibraryApi({ async requestData(path, options) { calls.push({ path, body: options.body }); return organization(); } });
  const signal = new AbortController().signal;
  await api.archiveSkillGroup("empty", { expected_revision: "c".repeat(64), archived: true, move_to_group_id: "application" }, "move-archive-key", signal);
  assert.equal(calls[0].path, "/library/organization/groups/empty/archive");
  assert.deepEqual(calls[0].body, { expected_revision: "c".repeat(64), archived: true, move_to_group_id: "application", idempotency_key: "move-archive-key" });
  await assert.rejects(() => api.archiveSkillGroup("empty", { expected_revision: "c".repeat(64), archived: true, move_to_group_id: "../secret" }, "bad-key", signal), ApiProblem);
});

test("archiving a non-empty group requires a target and never offers an archived or self group", () => {
  const populated = withArchivedGroup();
  populated.groups.push({ id: "ours", name: tx("Our second group"), source: "custom" });
  populated.assignments = [{ source: "builtin", id: "method", group_id: "ours" }];
  openArchive("nonempty", "ours");
  const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ organization: populated, projectId: "nonempty" })));
  assert.ok(html.includes(messages.en.library_group_archive_members));
  assert.ok(html.includes(messages.en.library_group_archive_move_help));
  assert.match(html, /class="library-group-count">1</, "the sentence carries the real member count");
  const label = messages.en.library_group_archive_target;
  const select = html.slice(html.indexOf(label), html.indexOf("</select>", html.indexOf(label)));
  assert.match(select, /<option value="application">Application<\/option>/);
  assert.ok(!select.includes('value="empty"'), "an archived group is not a target");
  assert.ok(!select.includes('value="ours"'), "the archived group itself is not a target");
  assert.match(controlTag(html, messages.en.library_group_archive_move), /disabled/, "no target chosen yet");
  openArchive("nonempty-chosen", "ours", "application");
  const chosen = renderToStaticMarkup(createElement(SkillGroups, groupProps({ organization: populated, projectId: "nonempty-chosen" })));
  assert.doesNotMatch(controlTag(chosen, messages.en.library_group_archive_move), /disabled/);
  libraryDraftMemory.set("skill-groups-archive:nonempty", null); libraryDraftMemory.set("skill-groups-archive:nonempty-chosen", null);
});

test("an empty group confirms in one paragraph before it is archived", () => {
  libraryDraftMemory.set("skill-groups-archive:confirm", { id: "empty", target: "" });
  const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ projectId: "confirm" })));
  assert.ok(html.includes(messages.en.library_group_archive_confirm));
  assert.ok(!html.includes(messages.en.library_group_archive_target), "an empty group is never asked for a target");
  const panel = html.slice(html.indexOf('class="skill-group-archive"'));
  assert.doesNotMatch(controlTag(panel, messages.en.library_archive_group), /disabled/);
  libraryDraftMemory.set("skill-groups-archive:confirm", null);
});

test("archived groups leave the active list and the move selects and only offer restore", () => {
  const value = withArchivedGroup();
  assert.deepEqual(groupedSkills([skill], value, "", "en").map((row) => row.group.id), ["application"]);
  assert.deepEqual(archivedGroups(value).map((group) => group.id), ["empty"]);
  assert.deepEqual(moveTargets(value, "application").map((group) => group.id), []);
  assert.equal(groupMemberCount(value, "application"), 1);
  const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ organization: value, projectId: "archived-list" })));
  assert.equal(html.split('<details class="skill-group"').length - 1, 1, "only the active group is listed");
  assert.match(html, /<details class="skill-groups-archived">/, "the archived section starts collapsed");
  assert.ok(html.includes(messages.en.library_archived_groups));
  assert.ok(html.includes(messages.en.library_restore_group));
  assert.ok(html.includes("Our group"), "the archived group is named in its own section");
  assert.ok(!html.includes('<option value="empty"'), "an archived group is never a move target");
  const active = renderToStaticMarkup(createElement(SkillGroups, groupProps({ projectId: "archived-none" })));
  assert.ok(!active.includes(messages.en.library_archived_groups), "the section is hidden when nothing is archived");
  assert.match(active, /<option value="empty">/);
  // Focus after a restore: while a group remains archived the summary is the target; after the
  // last one is restored the summary is gone and the section root is the persistent fallback.
  assert.match(html, /<details class="skill-groups-archived"><summary>/);
  assert.match(active, /^<div class="skill-groups" tabindex="-1">/, "the root stays focusable when the archived section unmounts");
  assert.ok(!active.includes("skill-groups-archived"));
});

test("archive copy is present in both locales and leaves group identifiers untranslated", () => {
  const keys = ["library_archive_group", "library_restore_group", "library_archived_groups", "library_archived_groups_help",
    "library_group_archive_confirm", "library_group_archive_members", "library_group_archive_move_help",
    "library_group_archive_target", "library_group_archive_choose", "library_group_archive_move", "library_group_archive_unavailable"];
  for (const key of keys) {
    for (const locale of ["en", "ru"]) {
      assert.equal(typeof messages[locale][key], "string", `${locale}.${key}`);
      assert.ok(messages[locale][key].trim(), `${locale}.${key}`);
    }
    assert.notEqual(messages.en[key], messages.ru[key], key);
  }
  const populated = withArchivedGroup();
  populated.groups.push({ id: "ours", name: tx("Наша группа"), source: "custom" });
  populated.assignments = [{ source: "builtin", id: "method", group_id: "ours" }];
  for (const locale of ["en", "ru"]) {
    libraryDraftMemory.set(`skill-groups-archive:locale-${locale}`, { id: "ours", target: "" });
    const html = renderToStaticMarkup(createElement(SkillGroups, groupProps({ organization: populated, locale, projectId: `locale-${locale}`, text: (key) => messages[locale][key] })));
    assert.ok(html.includes(messages[locale].library_archive_group));
    assert.ok(html.includes(messages[locale].library_group_archive_move));
    assert.ok(html.includes(messages[locale].library_archived_groups));
    assert.match(html, /<option value="application">/, "the canonical group id is never translated");
    assert.ok(html.includes("Наша группа"), "an authored name renders as typed under both locales");
    libraryDraftMemory.set(`skill-groups-archive:locale-${locale}`, null);
  }
});

test("legacy Design links become Work routes with only valid project and task scope", () => {
  const task = `task.${"0".repeat(26)}`;
  const route = parseWorkRoute(`?view=library&tab=design&task=${task}&project=project.${"a".repeat(32)}`);
  assert.equal(route.view, "work"); assert.equal(route.taskId, task); assert.equal(route.libraryTab, "roles");
  assert.ok(!workRouteHref(route).includes("design"));
  assert.equal(parseWorkRoute("?view=library&tab=design&task=../secret").taskId, null);
});
