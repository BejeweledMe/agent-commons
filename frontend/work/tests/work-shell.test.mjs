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
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/appRouteState.ts"), resolve(root, "src/setupReadiness.ts"), resolve(root, "src/launchIntentState.ts"), resolve(root, "src/api.ts"), resolve(root, "src/components/TaskComposer.tsx"), resolve(root, "src/components/ProjectSidebar.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const { parseWorkRoute, workRouteHref, sanitizedWorkLocation, isEditingTarget } = await import(pathToFileURL(resolve(compiled, "appRouteState.js")));
const { railSetupLabelKey, railSetupState } = await import(pathToFileURL(resolve(compiled, "setupReadiness.js")));
const { TaskComposer } = await import(pathToFileURL(resolve(compiled, "components/TaskComposer.js")));
const { ProjectSidebar } = await import(pathToFileURL(resolve(compiled, "components/ProjectSidebar.js")));
const { ProjectArchiveDialog } = await import(pathToFileURL(resolve(compiled, "components/ProjectArchiveDialog.js")));
const { ProjectArchiveConfirmation } = await import(pathToFileURL(resolve(compiled, "projectArchive.js")));
const { ApiProblem, WorkApi } = await import(pathToFileURL(resolve(compiled, "api.js")));
const { DEFAULT_RUN_LIMIT_MINUTES, MAX_RUN_LIMIT_MINUTES, MIN_RUN_LIMIT_MINUTES, runLimitSeconds } = await import(pathToFileURL(resolve(compiled, "launchIntentState.js")));
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

test("the rail states setup in words and only duplicates it with a non-acceptance colour", () => {
  assert.deepEqual(
    ["setup_not_a_repository", "setup_uninitialized", "setup_unconfigured", "setup_configured", "setup_unavailable", ""].map(railSetupState),
    ["not_repository", "needs_setup", "needs_setup", "configured", "unknown", "unknown"]
  );
  const labels = ["setup_not_a_repository", "setup_uninitialized", "setup_unconfigured", "setup_configured", "setup_unavailable"].map(railSetupLabelKey);
  assert.deepEqual(labels, ["setup_not_repository", "setup_uninitialized", "setup_unconfigured", "setup_configured", "not_configured"]);
  for (const locale of ["en", "ru"]) {
    const readings = labels.map((key) => messages[locale][key]);
    assert.equal(readings.filter(Boolean).length, labels.length, "every rail state needs copy");
    assert.equal(new Set(readings).size, labels.length, "the text alone must separate the states");
  }
  assert.match(mainSource, /<span className="status-dot" aria-hidden="true" data-state=\{railSetupState\(data\.setup\.state\)\} \/>\{text\(railSetupLabelKey\(data\.setup\.state\)\)\}/);
  for (const state of ["configured", "needs_setup", "not_repository", "unknown"]) {
    assert.match(styles, new RegExp(`\\.status-dot\\[data-state="${state}"\\] \\{ background: #[0-9a-f]{6}; \\}`));
  }
  // Green stays reserved for acceptance, so the configured dot is blue.
  assert.match(styles, /\.status-dot\[data-state="configured"\] \{ background: #7fa8ff; \}/);
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: archiving asks first, naming the project, its folder and what stays untouched`, () => {
    const confirmation = new ProjectArchiveConfirmation();
    confirmation.ask({ projectId: "one", revision: "sha256:one", name: "Alpha", path: "/Users/owner/Projects/alpha" });
    const markup = renderToStaticMarkup(createElement(ProjectArchiveDialog, { confirmation, disabled: false,
      text: (key) => messages[locale][key], onArchive: async () => true, onClose() {} }));
    assert.match(markup, /<dialog[^>]*aria-labelledby="/);
    assert.ok(markup.includes(messages[locale].project_archive_title));
    assert.ok(markup.includes("Alpha"));
    assert.ok(markup.includes("/Users/owner/Projects/alpha"));
    assert.ok(markup.includes(messages[locale].project_archive_explains));
    assert.ok(markup.includes(messages[locale].project_archive_cancel));
    assert.ok(markup.includes(messages[locale].project_archive_confirm));
    assert.equal(markup.includes(messages[locale].project_archive_path_unknown), false);

    const unopened = new ProjectArchiveConfirmation();
    const closedMarkup = renderToStaticMarkup(createElement(ProjectArchiveDialog, { confirmation: unopened, disabled: false,
      text: (key) => messages[locale][key], onArchive: async () => true, onClose() {} }));
    assert.ok(closedMarkup.includes(messages[locale].project_archive_path_unknown), "an unknown folder is stated, never invented");
  });
}

test("Prepare run offers a bounded minutes control that decides the request in state, not in HTML", () => {
  assert.deepEqual([MIN_RUN_LIMIT_MINUTES, DEFAULT_RUN_LIMIT_MINUTES, MAX_RUN_LIMIT_MINUTES], [1, 10, 60]);
  // A new draft opens at the default; the control is labelled and typed.
  assert.match(mainSource, /limitMinutes: String\(DEFAULT_RUN_LIMIT_MINUTES\)/);
  assert.match(mainSource, /<label htmlFor="run-limit">\{text\("run_limit_field"\)\}<\/label>/);
  assert.match(mainSource, /id="run-limit"/);
  assert.match(mainSource, /type="number"/);
  assert.match(mainSource, /max=\{MAX_RUN_LIMIT_MINUTES\}/);
  assert.match(mainSource, /min=\{MIN_RUN_LIMIT_MINUTES\}/);
  assert.match(mainSource, /step=\{1\}/);
  assert.match(mainSource, /aria-describedby=\{\["run-limit-help"/);
  // Submission and the request itself both re-derive the value from the draft.
  assert.match(mainSource, /runLimitSeconds\(run\.limitMinutes\) === null \? \["run-limit"\] : \[\]/);
  assert.match(mainSource, /const wallTimeSeconds = runLimitSeconds\(run\.limitMinutes\);/);
  assert.match(mainSource, /if \(wallTimeSeconds === null\) \{/);
  // The width is a class, and nothing about the limit is styled inline.
  assert.match(styles, /\.launch-limit-minutes \{ max-width: \d+px; \}/);
  assert.doesNotMatch(mainSource, /style=\{\{[^}]*\}\}/);
});

test("an unusable minutes value is refused locally and a refused one is refused by the server, in its words", async () => {
  for (const [typed, seconds] of [["1", 60], ["10", 600], ["60", 3600], ["", null], ["0", null], ["61", null], ["10.5", null], ["-1", null]]) {
    assert.equal(runLimitSeconds(typed), seconds, typed);
  }
  const client = Object.assign(new WorkApi(), { apiBase: "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" });
  const bodies = [];
  const reason = "wall_time_seconds must be an integer between 60 and 3600";
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: false, status: 422, json: async () => ({ error: { code: "invalid_wall_time_seconds", message: reason, safe_next_actions: [] } }) };
  };
  const refused = await client.startRun({ agentId: `agent.${"0".repeat(26)}`, taskId: id, contextPackId: null, contextPackRevision: null,
    designPackageId: null, designPackageRevision: null, wallTimeSeconds: 3600 }, "refused-limit", new AbortController().signal)
    .then(() => null, (error) => error);
  assert.ok(refused instanceof ApiProblem);
  assert.equal(refused.status, 422);
  assert.equal(refused.apiError.code, "invalid_wall_time_seconds");
  assert.equal(refused.apiError.message, reason);
  assert.deepEqual(bodies, [{ agent_id: `agent.${"0".repeat(26)}`, task_id: id, wall_time_seconds: 3600, idempotency_key: "refused-limit" }]);
  // The refusal is shown beside the control, keyed to the exact refused value,
  // and the draft that produced it is never rewritten.
  // The refusal is recognised by its typed code only; message text never drives the UI
  // (tests/ui/test_work_app_contract.py), and the explanation shown is the localized bound.
  assert.match(mainSource, /return apiError\.code === RUN_LIMIT_REFUSAL_CODE;/);
  assert.doesNotMatch(mainSource, /apiError\.message\.startsWith/);
  assert.match(mainSource, /setRunLimitRefusal\(\{ taskId: intent\.draft\.taskId, limitMinutes: intent\.draft\.limitMinutes \}\)/);
  assert.match(mainSource, /id="run-limit-refusal" role="alert">\{text\("run_limit_server_refusal"\)\} \{text\("form_error_run_limit"\)\}/);
  assert.doesNotMatch(mainSource, /isRunLimitRefusal\(problem\)[\s\S]{0,240}setRun\(/);
  for (const locale of ["en", "ru"]) {
    for (const key of ["run_limit_field", "run_limit_help", "form_error_run_limit", "run_limit_server_refusal"]) {
      assert.equal(typeof messages[locale][key], "string", `${locale}.${key}`);
      assert.notEqual(messages[locale][key], "");
    }
  }
});

test("project shell copy names Agents clearly and keeps Context guidance plain in both languages", () => {
  assert.equal(messages.en.shell_nav_team, "Agents");
  assert.equal(messages.ru.shell_nav_team, "Агенты");
  assert.doesNotMatch(messages.en.context_packs_intro, /revision|canonical|bounded/i);
  assert.doesNotMatch(messages.ru.context_packs_intro, /ревизи|канонич|огранич/i);
});
