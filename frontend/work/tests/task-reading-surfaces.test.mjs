import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "commons-task-reading-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  ...["libraryDetailSession.ts", "components/TaskBrief.tsx", "components/TaskComposer.tsx",
    "components/ServiceLibrarySection.tsx", "components/TrackerFreshnessNotice.tsx", "components/TaskInspector.tsx"]
    .map((path) => resolve(root, "src", path))
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path + ".js")).href);
const { LibraryDetailSession } = await load("libraryDetailSession");
const { LibraryDetailContent } = await load("components/ServiceLibrarySection");
const { TaskBrief, briefNeedsExpansion } = await load("components/TaskBrief");
const { TaskComposer } = await load("components/TaskComposer");
const { Modal } = await load("components/Modal");
const { TaskInspectorContent } = await load("components/TaskInspector");
const { TrackerFreshnessNotice } = await load("components/TrackerFreshnessNotice");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const ref = (id) => ({ kind: "role", source: "builtin", id, version: "a".repeat(64) });
const item = (id) => ({ ref: ref(id), name: `Role ${id}`, description: "Catalog description" });
const detail = (id) => ({ ref: ref(id), content: { name: `Role ${id}`, description: "Description", system_prompt: "<script>safe plain text</script>\nNext paragraph", entry_skill: { ...ref("method"), kind: "skill" }, conditional_skills: [], core_skills: [], group: "application" }, files: [] });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const html = (type, props) => renderToStaticMarkup(createElement(type, props));

function initialText(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(initialText).join(" ");
  if (typeof node !== "object") return String(node);
  if (typeof node.type === "function") return initialText(node.type(node.props));
  const children = node.props?.children;
  if (node.type === "details" && !node.props.open) return initialText((Array.isArray(children) ? children : [children]).find((child) => child?.type === "summary"));
  return initialText(children);
}

test("closing the first or last catalog read invalidates late success and failure, without writing", async () => {
  for (const id of ["first", "last"]) for (const outcome of ["success", "failure"]) {
    const waiting = deferred(); let requestSignal;
    const reader = new LibraryDetailSession({ detail: (_ref, signal) => { requestSignal = signal; return waiting.promise; }, readFile: () => assert.fail("no file request") });
    const pending = reader.open(item(id));
    assert.equal(reader.snapshot().status, "loading"); assert.equal(reader.snapshot().item.ref.id, id);
    reader.close(); assert.equal(requestSignal.aborted, true);
    if (outcome === "success") waiting.resolve(detail(id)); else waiting.reject(new Error("late"));
    await pending; assert.equal(reader.snapshot().status, "closed"); assert.equal(reader.snapshot().item, null);
  }
});

test("a new selection owns its read, retry keeps the exact version, and closed file reads stay closed", async () => {
  const old = deferred(), file = deferred(), calls = []; let fail = true;
  const reader = new LibraryDetailSession({ detail: async (ref, signal) => { calls.push({ ref, signal }); if (ref.id === "first") return old.promise; if (fail) throw new Error("offline"); return detail(ref.id); }, readFile: () => file.promise });
  const pending = reader.open(item("first")); await reader.open(item("last"));
  assert.equal(calls[0].signal.aborted, true); assert.equal(reader.snapshot().status, "error");
  fail = false; await reader.retry(); assert.deepEqual(calls[1].ref, calls[2].ref);
  old.resolve(detail("first")); await pending; assert.equal(reader.snapshot().detail.ref.id, "last");
  const reading = reader.readFile({ path: "reference.md", size: 20, sha256: "b".repeat(64) });
  assert.equal(reader.snapshot().file.text, null); reader.close(); file.resolve("late private reference"); await reading;
  assert.equal(reader.snapshot().file, null); assert.equal(reader.snapshot().status, "closed");
});

test("separate catalog file reads cannot replace the later chosen file", async () => {
  const old = deferred(); const reader = new LibraryDetailSession({ detail: async () => detail("first"), readFile: async (_ref, file) => file.path === "first.md" ? old.promise : "Second file" });
  await reader.open(item("first")); const first = reader.readFile({ path: "first.md" }); await reader.readFile({ path: "last.md" });
  old.resolve("First file"); await first; assert.equal(reader.snapshot().file.path, "last.md"); assert.equal(reader.snapshot().file.text, "Second file");
});

test("brief preview counts paragraphs and Unicode without changing the original text", () => {
  assert.equal(briefNeedsExpansion("Short\n\nSecond paragraph"), false);
  assert.equal(briefNeedsExpansion("😀".repeat(490)), false);
  assert.equal(briefNeedsExpansion("😀".repeat(491)), true);
  assert.equal(briefNeedsExpansion(Array(8).fill("line").join("\n")), true);
});

for (const locale of ["en", "ru"]) {
  const text = (key) => messages[locale][key];
  test(`${locale}: embedded composer delegates heading and close to one medium native modal`, () => {
    const props = { draft: { title: "", description: "", criteria: "", dependencyIds: [] }, errors: new Set(), busy: false, writesEnabled: true, tasks: [], onChange() {}, onSubmit() {}, onClose() {}, onDependency() {}, text };
    const standalone = html(TaskComposer, props); assert.equal((standalone.match(/<h2/g) ?? []).length, 1);
    const modal = html(Modal, { title: text("shell_new_task"), closeLabel: text("shell_close"), size: "medium", onClose() {}, children: createElement(TaskComposer, { ...props, embedded: true }) });
    assert.equal((modal.match(/<h2/g) ?? []).length, 1); assert.equal((modal.match(/commons-dialog-close/g) ?? []).length, 1);
    assert.match(modal, /commons-dialog-medium/); assert.doesNotMatch(modal, /task-composer-title/);
  });
  test(`${locale}: instructions show loading, retryable error, and safely escaped ready prose`, () => {
    let retries = 0; const props = { text, editable: false, onEdit() {}, onRetry: () => retries++, onReadFile() {} };
    assert.match(html(LibraryDetailContent, { ...props, view: { status: "loading", detail: null } }), /role="status"/);
    const error = LibraryDetailContent({ ...props, view: { status: "error", detail: null } });
    error.props.children[1].props.onClick(); assert.equal(retries, 1);
    assert.ok(html(LibraryDetailContent, { ...props, view: { status: "error", detail: null } }).includes(text("library_detail_retry")));
    const ready = html(LibraryDetailContent, { ...props, view: { status: "ready", detail: detail("first"), file: null } });
    assert.match(ready, /&lt;script&gt;safe plain text/); assert.doesNotMatch(ready, /<script>/); assert.match(ready, /<details open="">/);
  });
  test(`${locale}: long task prose is a native disclosure and criteria remain visible outside it`, () => {
    const description = "<img src=x>\n" + "Long description. ".repeat(80);
    const brief = html(TaskBrief, { description, text });
    assert.match(brief, /&lt;img src=x&gt;/); assert.doesNotMatch(brief, /<img/); assert.doesNotMatch(brief, /<details[^>]* open/);
    assert.ok(brief.includes(text("inspector_brief_expand"))); assert.ok(brief.includes(text("inspector_brief_collapse")));
    const task = { taskId: "task.synthetic", title: "Task", taskState: "accepted", readiness: "ready", roleName: null, blockingDependencyIds: [], dependencyTaskIds: [], freshness: "fresh", gaps: [], evidenceState: "complete", nextAction: "none", awaitsHuman: false };
    const record = { taskId: task.taskId, title: "Task", description, acceptanceCriteria: ["Always visible criterion"], summary: null, evidenceRefs: [], findings: [], revision: "evt.synthetic", truncated: false };
    const run = { delegationId: "delegation.old", taskId: task.taskId, phase: "needs_operator", roleName: "Historical worker", wallTimeSeconds: 1200, stopReason: { code: "unknown", reason: "Old stopped run", nextAction: "inspect_canonical_outcome" }, providerDiagnostic: null, finishedAt: null, updatedAt: null, attemptId: null, profileId: null, evidenceState: "complete", freshness: "fresh", startedAt: "2026-09-01T00:00:00Z" };
    const view = createElement(TaskInspectorContent, { task, tasks: [task], runs: [run], locale, text, actionsCurrent: true, writesEnabled: true, onSelectTask() {}, onLaunchTask() {}, detailState: { kind: "ready", taskId: task.taskId, detail: record }, onRefresh() {} });
    const visible = initialText(view); assert.ok(visible.includes("Always visible criterion")); assert.ok(visible.includes(text("decision_task_now"))); assert.ok(visible.includes(text("inspector_last_run"))); assert.ok(visible.includes(text("inspector_runs_context")));
    const markup = renderToStaticMarkup(view); assert.match(markup, /data-accepted="true"/); assert.ok(markup.includes("needs_operator"));
  });
  test(`${locale}: freshness names known source times and provides a read-only refresh action`, () => {
    let reads = 0;
    for (const sourceUpdatedAt of [null, "2026-10-09T10:00:00Z"]) {
      const freshness = { generatedAt: "2026-10-09T10:01:00Z", sourceUpdatedAt, state: "stale", resumeGap: false };
      const view = TrackerFreshnessNotice({ freshness, locale, text, onRefresh: () => reads++ });
      const markup = renderToStaticMarkup(view); assert.ok(markup.includes(text("tracker_freshness_source"))); assert.ok(markup.includes('dateTime="2026-10-09T10:01:00Z"'));
      if (sourceUpdatedAt === null) assert.ok(markup.includes(text("tracker_source_time_unknown"))); else assert.ok(markup.includes(`dateTime="${sourceUpdatedAt}"`));
      view.props.children[1].props.children.at(-1).props.onClick();
    }
    assert.equal(reads, 2);
  });
}
