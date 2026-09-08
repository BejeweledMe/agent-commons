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
const compiled = mkdtempSync(resolve(tmpdir(), "commons-work-shell-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/appRouteState.ts"), resolve(root, "src/components/TaskComposer.tsx"), resolve(root, "src/components/ProjectSidebar.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const { parseWorkRoute, workRouteHref, sanitizedWorkLocation, isEditingTarget } = await import(pathToFileURL(resolve(compiled, "appRouteState.js")));
const { TaskComposer } = await import(pathToFileURL(resolve(compiled, "components/TaskComposer.js")));
const { ProjectSidebar } = await import(pathToFileURL(resolve(compiled, "components/ProjectSidebar.js")));
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const styles = readFileSync(resolve(root, "src/styles.css"), "utf8");
const mainSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");
const id = `task.${"0".repeat(26)}`;

test("route roundtrip preserves task, canonical filter, Library tab and composer across refresh/back", () => {
  for (const view of ["work", "team", "library", "settings"]) {
    const state = { projectId: null, view, taskId: id, filter: "accepted", libraryTab: "context", composer: true };
    const href = workRouteHref(state);
    assert.deepEqual(parseWorkRoute(new URL(href, "http://localhost").search), state);
  }
  assert.equal(parseWorkRoute(`?task=${id}`).taskId, id, "route parsing must not guess whether a selected task still exists");
  for (const libraryTab of ["roles", "skills", "blueprints", "context"]) {
    const state = { projectId: null, view: "library", taskId: id, filter: "all", libraryTab, composer: false };
    assert.deepEqual(parseWorkRoute(new URL(workRouteHref(state), "http://localhost").search), state);
  }
  assert.equal(parseWorkRoute("?view=library&tab=templates").libraryTab, "blueprints", "legacy template links still open project templates");
});
test("auth URL scrub preserves only approved navigation and never search/draft or exchange material", () => {
  const href = sanitizedWorkLocation("/work", `?view=library&tab=design&task=${id}&filter=review&q=private+search&token=secret&description=draft&c=exchange`);
  assert.equal(href, `/work?task=${id}&filter=review`);
  assert.deepEqual(parseWorkRoute("?view=admin&task=../../other&filter=finished&tab=secret&new=run"), { projectId: null, view: "work", taskId: null, filter: "all", libraryTab: "roles", composer: false });
});
test("shortcuts ignore editable controls", () => {
  class Element { constructor(editable) { this.editable = editable; } closest() { return this.editable ? this : null; } }
  globalThis.Element = Element;
  assert.equal(isEditingTarget(new Element(true)), true);
  assert.equal(isEditingTarget(new Element(false)), false);
  assert.equal(isEditingTarget(null), false);
});
for (const locale of ["en", "ru"]) {
  test(`${locale}: composer keeps supplied draft, labelled criteria and task-only submit when providers absent`, () => {
    const draft = { title: "My task", description: "Outcome", criteria: "Visible result", dependencyIds: [id] };
    const markup = renderToStaticMarkup(TaskComposer({ draft, onChange() {}, errors: new Set(), busy: false, writesEnabled: true,
      tasks: [{ id, title: "Prerequisite", state: "accepted" }], onSubmit() {}, onClose() {}, onDependency() {}, text: (key) => messages[locale][key] }));
    assert.match(markup, /value="My task"/);
    assert.match(markup, /Outcome/);
    assert.match(markup, /Visible result/);
    assert.match(markup, /checked=""/);
    assert.match(markup, /for="task-criteria"/);
    assert.doesNotMatch(markup, /disabled=""/);
    assert.equal(markup.includes(messages[locale].start_run), false);
    assert.deepEqual(draft.dependencyIds, [id]);
  });
}

test("project shell keeps one compact chooser and keyboard-accessible actions on every row", () => {
  const projects = { readOnly: false, revision: "evt.one", defaultProjectId: "one", projects: [
    { id: "one", revision: "evt.one", name: "Alpha", state: "ready", available: true, archived: false },
    { id: "two", revision: "evt.two", name: "Beta", state: "ready", available: true, archived: false }
  ] };
  const markup = renderToStaticMarkup(createElement(ProjectSidebar, { currentProjectId: "one", legacy: false, locale: "en", projects,
    text: (key) => messages.en[key], onInspect: async () => {}, onCreate: async () => {}, onSelect() {}, onUpdate: async () => {} }));
  assert.equal((markup.match(/type="search"/g) ?? []).length, 1);
  assert.equal((markup.match(/<details class="project-actions">/g) ?? []).length, 2);
  assert.match(markup, /Project actions: Alpha/);
  assert.match(markup, /Project actions: Beta/);
  assert.doesNotMatch(markup, /Open project Gallery/);
  assert.match(styles, /\.sr-only\s*\{[^}]*position:\s*absolute[^}]*clip:/s);
  assert.doesNotMatch(mainSource, /openProjectGallery|text\("project_label"\)/);
});

test("project shell copy names Agents clearly and keeps Context guidance plain in both languages", () => {
  assert.equal(messages.en.shell_nav_team, "Agents");
  assert.equal(messages.ru.shell_nav_team, "Агенты");
  assert.doesNotMatch(messages.en.context_packs_intro, /revision|canonical|bounded/i);
  assert.doesNotMatch(messages.ru.context_packs_intro, /ревизи|канонич|огранич/i);
});
