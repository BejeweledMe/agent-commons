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
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-work-library-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  resolve(root, "src/contextSourcePicker.ts"), resolve(root, "src/contextPackFormState.ts"),
  resolve(root, "src/starterPackPresentation.ts"), resolve(root, "src/blueprintApplyOutcome.ts"),
  resolve(root, "src/components/LibrarySection.tsx")
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path)).href);
const { exactSourceKey, sourceSelectionState, sourceChoices, chooseExactSource, updateSourceDisplayMemory } = await load("contextSourcePicker.js");
const { formFromDraft, draftFromForm, parseAdvancedDraft, parseExactReference, snapshotSaveOperation } = await load("contextPackFormState.js");
const { starterPackPresentation } = await load("starterPackPresentation.js");
const { ContextSourcePicker } = await load("components/ContextSourcePicker.js");
const { StarterPackCard } = await load("components/StarterPacksSection.js");
const { LibrarySection } = await load("components/LibrarySection.js");
const { ContextPackNewButton } = await load("components/ContextPacksSection.js");
const { ContextPackRetryIdentity } = await load("contextPackEditorState.js");
const { blueprintApplyOutcome, blueprintPlanIntent, blueprintStartIntent } = await load("blueprintApplyOutcome.js");
const { BlueprintApplyOutcomePanel } = await load("components/WorkBlueprintsSection.js");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const id = (kind, tail = "1") => `${kind}.${"0".repeat(25)}${tail}`;
const ref = (kind, tail = "1", revision = "1") => ({ kind, id: id(kind, tail), revision: id("evt", revision) });
const kinds = ["artifact", "finding", "task", "thread", "verification", "decision"];
const catalog = (sources = kinds.map((kind) => ({ ref: ref(kind), label: `Source ${kind}`, freshness: "current" })), truncated = false) => ({
  schema: "agent-commons.ui.context-sources.v1", state: sources.length ? "ready" : "empty", sources, truncated
});
const draft = () => ({
  summary: "Context with\nmultiple lines",
  facts: [{ statement: "A statement :: with a delimiter\nand another line", sourceRefs: [ref("task"), ref("artifact")] }],
  decisionRefs: [ref("decision")], openQuestions: ["What remains?\nExplain."]
});

for (const kind of kinds) {
  test(`exact ${kind} selection never upgrades its revision on catalog refresh`, () => {
    const old = ref(kind);
    const key = exactSourceKey(old);
    const current = catalog([{ ref: old, label: "Readable title", freshness: "current" }]);
    assert.equal(sourceSelectionState(key, current), "current");
    assert.deepEqual(chooseExactSource(key, current, kind === "decision"), old);
    const newer = catalog([{ ref: ref(kind, "1", "2"), label: "Updated", freshness: "current" }]);
    assert.equal(sourceSelectionState(key, newer), "stale");
    assert.equal(chooseExactSource(key, newer, kind === "decision"), null);
    assert.equal(sourceSelectionState(key, catalog([])), "unavailable");
    assert.equal(sourceSelectionState(key, catalog([], true)), "unconfirmed");
    assert.equal(sourceSelectionState(key, null), "unconfirmed");
  });
}

test("fact and decision catalogs stay separate and bounded local search does not fabricate candidates", () => {
  assert.deepEqual(sourceChoices(catalog(), false, "").map((source) => source.ref.kind), kinds.slice(0, -1));
  assert.deepEqual(sourceChoices(catalog(), true, "").map((source) => source.ref.kind), ["decision"]);
  assert.equal(sourceChoices(catalog(), false, "FINDING").length, 1);
  assert.equal(sourceChoices(catalog(), false, "missing").length, 0);
  assert.equal(sourceChoices(null, false, "").length, 0);
  assert.equal(chooseExactSource(exactSourceKey(ref("decision")), catalog(), false), null);
  assert.equal(chooseExactSource(exactSourceKey(ref("task")), catalog(), true), null);
  assert.throws(() => parseExactReference(exactSourceKey(ref("decision")), false));
  assert.throws(() => parseExactReference(exactSourceKey(ref("task")), true));
});

test("selected display metadata survives newer, missing and truncated catalogs without upgrading the draft", () => {
  const original = draft();
  const body = JSON.stringify(original);
  const key = exactSourceKey(original.facts[0].sourceRefs[0]);
  const current = catalog([ref("task"), ref("artifact")].map((source) => ({ ref: source, label: "Shared title", freshness: "current" })));
  const confirmed = updateSourceDisplayMemory(null, "source", key, false, current);
  assert.equal(confirmed.label, "Shared title");
  const newer = catalog([{ ref: ref("task", "1", "2"), label: "New revision title", freshness: "current" }]);
  for (const [incoming, state] of [[newer, "stale"], [catalog([]), "unavailable"], [catalog([], true), "unconfirmed"], [null, "unconfirmed"]]) {
    const display = updateSourceDisplayMemory(confirmed, "source", key, false, incoming);
    assert.equal(display, confirmed);
    assert.equal(display.key, key);
    assert.equal(sourceSelectionState(key, incoming), state);
    assert.equal(chooseExactSource(key, incoming, false), null);
    assert.equal(JSON.stringify(draftFromForm(formFromDraft(original))), body);
  }
  const renamed = catalog([{ ref: ref("task"), label: "Confirmed corrected title", freshness: "current" }]);
  const corrected = updateSourceDisplayMemory(confirmed, "source", key, false, renamed);
  assert.equal(corrected.label, "Confirmed corrected title");
  assert.equal(updateSourceDisplayMemory(corrected, "source", key, false, newer), corrected);
});

test("display memory clears on selection, picker scope and kind changes and never borrows a newer title", () => {
  const key = exactSourceKey(ref("task"));
  const confirmed = updateSourceDisplayMemory(null, "source", key, false, catalog());
  const unknownKey = exactSourceKey(ref("task", "2"));
  for (const [pickerId, selectedKey, decisionOnly] of [["source", unknownKey, false], ["source", "", false], ["other-row", key, false], ["source", key, true]]) {
    const cleared = updateSourceDisplayMemory(confirmed, pickerId, selectedKey, decisionOnly, null);
    assert.equal(cleared.label, null);
    assert.equal(updateSourceDisplayMemory(cleared, "source", key, false, null).label, null);
  }
  const nextKey = exactSourceKey(ref("task", "1", "2"));
  const newer = catalog([{ ref: ref("task", "1", "2"), label: "Same source, newer version", freshness: "current" }]);
  assert.equal(updateSourceDisplayMemory(null, "source", key, false, newer).label, null);
  const changed = updateSourceDisplayMemory(confirmed, "source", nextKey, false, newer);
  assert.equal(changed.key, nextKey);
  assert.equal(changed.label, "Same source, newer version");
  assert.equal(updateSourceDisplayMemory(changed, "source", key, false, newer).label, null);
});

test("guided and advanced draft roundtrip preserves multiline text and exact source versions", () => {
  const original = draft();
  const form = formFromDraft(original);
  assert.deepEqual(draftFromForm(form), original);
  const advanced = JSON.stringify(draftFromForm(form, false), null, 2);
  assert.deepEqual(parseAdvancedDraft(advanced), original);
  assert.deepEqual(draftFromForm(formFromDraft(parseAdvancedDraft(advanced, false))), original);
  assert.throws(() => parseAdvancedDraft('{"summary":'));
  assert.throws(() => parseAdvancedDraft(JSON.stringify({ ...original, providerOutput: "unknown field" })));
  const wrongKind = structuredClone(original);
  wrongKind.facts[0].sourceRefs[0].kind = "decision";
  assert.throws(() => parseAdvancedDraft(JSON.stringify(wrongKind)));
});

test("both editors preserve existing semantic bounds and incomplete decisions cannot disappear on save", () => {
  const oversized = draft();
  oversized.summary = "x".repeat(4097);
  assert.throws(() => draftFromForm(formFromDraft(oversized)), /context_packs_validation_summary_limit/);
  assert.throws(() => parseAdvancedDraft(JSON.stringify(oversized)), /context_packs_validation_summary_limit/);
  const noSource = formFromDraft(draft());
  noSource.facts[0].sources = [""];
  assert.throws(() => draftFromForm(noSource));
  const noDecision = formFromDraft(draft());
  noDecision.decisions[0].value = "";
  assert.throws(() => draftFromForm(noDecision));
});

test("uncertain save snapshot retains original body, expected revision and idempotency key", () => {
  const original = { draft: draft(), contextPackId: id("context_pack"), expectedRevision: id("evt"), idempotencyKey: "first-write" };
  const retry = snapshotSaveOperation(original);
  const encoded = JSON.stringify(retry);
  original.draft.facts[0].sourceRefs[0].revision = id("evt", "2");
  original.draft.summary = "Later draft";
  original.expectedRevision = id("evt", "3");
  original.idempotencyKey = "later-write";
  assert.equal(JSON.stringify(retry), encoded);
  assert.notEqual(retry.draft.summary, original.draft.summary);
});

for (const locale of ["en", "ru"]) {
  const text = (key) => messages[locale][key];
  test(`${locale} source picker renders readable exact versions and keeps a stale selection invalid`, () => {
    const key = exactSourceKey(ref("task"));
    const html = renderToStaticMarkup(createElement(ContextSourcePicker, {
      id: "fact-source", label: "Source", value: key, catalog: catalog([{ ref: ref("task", "1", "2"), label: "Updated title", freshness: "current" }]), text, onChange: () => {}
    }));
    assert.match(html, /aria-invalid="true"/);
    assert.match(html, /disabled="" value="task\./);
    assert.ok(html.includes(key));
    assert.ok(html.includes(text("context_source_stale")));
    assert.ok(html.includes("Updated title"));
    assert.match(html, /<details class="context-source-reference"><summary>/);
    assert.ok(html.includes(`<code>${key}</code></details>`));
    assert.ok(!html.includes(`>${key} — ${key}</option>`));
  });
  test(`${locale} source choices lead with readable titles and disambiguate duplicate titles`, () => {
    const key = exactSourceKey(ref("task"));
    const props = { id: "source", label: "Source", value: key, text, onChange: () => {} };
    const html = renderToStaticMarkup(createElement(ContextSourcePicker, {
      ...props, catalog: catalog([{ ref: ref("task"), label: "Readable title", freshness: "current" }])
    }));
    assert.ok(html.includes(`value="${key}" selected="">Readable title</option>`));
    assert.ok(html.includes(`<code>${key}</code></details>`));
    const duplicate = renderToStaticMarkup(createElement(ContextSourcePicker, {
      ...props, catalog: catalog([ref("task"), ref("artifact")].map((source) => ({ ref: source, label: "Shared title", freshness: "current" })))
    }));
    assert.ok(duplicate.includes(`Shared title — ${key}</option>`));
    assert.ok(duplicate.includes(`Shared title — ${exactSourceKey(ref("artifact"))}</option>`));
  });
  test(`${locale} Library keeps every editor mounted and identifies its selected URL-owned tab`, () => {
    for (const tab of ["roles", "skills", "blueprints", "context"]) {
      const html = renderToStaticMarkup(createElement(LibrarySection, {
        tab, onTabChange: () => {}, api: {}, text, writesEnabled: false, onApplied: async () => {}, onChooseTeam: () => {}
      }));
      assert.doesNotMatch(html, /<h1|aria-labelledby="library-title"/);
      assert.equal((html.match(/role="tabpanel"/g) ?? []).length, 4);
      assert.equal((html.match(/aria-selected="true"/g) ?? []).length, 1);
      assert.ok(html.includes('id="context-pack-summary"'));
      assert.ok(!html.includes('href="/gallery"'));
      assert.ok(html.includes(`id="library-panel-${tab}" role="tabpanel"`));
      assert.match(html, /<fieldset disabled="">/);
    }
  });
}

function packFixture() {
  const manifest = JSON.parse(readFileSync(resolve(root, "../../src/agent_commons/resources/starter_packs/feature-delivery.mock.json"), "utf8"));
  return { id: manifest.id, version: manifest.version, title: manifest.title, summary: manifest.summary, sourceKind: "bundled", example: true,
    blueprints: manifest.blueprints.map((blueprint) => ({ ...blueprint, roles: blueprint.roles.map((role) => ({
      id: role.id, name: role.name, purpose: role.purpose, profileId: role.profile_id, contextMode: "fresh", skills: role.skill_refs
    })) })) };
}

test("localized recipe presentation preserves canonical identities, configuration and unknown metadata", () => {
  const original = packFixture();
  const before = JSON.stringify(original);
  const translated = starterPackPresentation(original, (key) => messages.ru[key]);
  assert.notEqual(translated.title, original.title);
  assert.equal(translated.id, original.id);
  assert.equal(translated.blueprints[0].id, original.blueprints[0].id);
  assert.deepEqual(translated.blueprints[0].roles.map(({ id, profileId, contextMode, skills }) => ({ id, profileId, contextMode, skills })), original.blueprints[0].roles.map(({ id, profileId, contextMode, skills }) => ({ id, profileId, contextMode, skills })));
  assert.equal(JSON.stringify(original), before);
  for (const patch of [{ id: "custom.recipe" }, { version: "0.2.0" }]) {
    const unknown = { ...original, ...patch };
    assert.equal(starterPackPresentation(unknown, (key) => messages.ru[key]), unknown);
  }
});

test("recipe viewing renders deny permissions and localized roles without invoking application", () => {
  let writes = 0;
  const pack = packFixture();
  const html = renderToStaticMarkup(createElement(StarterPackCard, {
    pack, text: (key) => messages.ru[key], applyState: { kind: "idle" }, confirmed: {},
    onApply: () => { writes += 1; }, onConfirm: () => {}, onChooseTeam: () => {}, writesEnabled: true
  }));
  assert.equal(writes, 0);
  assert.ok(html.includes(messages.ru.recipe_implementer_name));
  assert.ok(html.includes(messages.ru.recipe_independent_reviewer_name));
  assert.ok(html.includes(messages.ru.recipe_permissions));
  assert.ok(html.includes("claude-independent-reviewer"));
  assert.match(html, /<button class="button button-primary" disabled=""/);
});

function trackerTask(tail, overrides = {}) {
  return {
    taskId: id("task", tail), title: `Created task ${tail}`, taskState: "ready", readiness: "ready",
    dependencyTaskIds: [], blockingDependencyIds: [], ownerSessionId: null, roleName: null,
    suggestedAgentId: null, suggestedRoleName: null, suggestedProvider: null, provider: null,
    profileId: null, phase: null, awaitsHuman: false, nextAction: "start_ready_work",
    freshness: "fresh", evidenceState: "complete", gaps: [], ...overrides
  };
}

function trackerReady(tasks) {
  return { kind: "ready", connection: "connected", snapshot: {
    schema: "agent-commons.tracker.v1", sequence: 4, sourceRevision: null, truncated: false,
    state: "ready", tasks, edges: [], runs: [], attention: [],
    capacity: { state: "available", active: 0, limit: null, queued: 0, queueCapacity: null },
    freshness: { generatedAt: "2026-09-09T09:00:00Z", sourceUpdatedAt: null, state: "fresh", resumeGap: false },
    gaps: []
  } };
}

// The server returns the mapping in blueprint order, not in execution order:
// the leading node here depends on the trailing one.
const blockedFirst = { blueprintId: "web-app",
  roles: [{ slotId: "lead", agentId: id("agent", "A") }, { slotId: "design", agentId: id("agent", "B") }],
  tasks: [{ nodeId: "integration", taskId: id("task", "1"), agentId: id("agent", "A") },
    { nodeId: "experience", taskId: id("task", "2"), agentId: id("agent", "B") }] };
const waitingTask = trackerTask("1", { taskState: "blocked", readiness: "blocked", dependencyTaskIds: [id("task", "2")], blockingDependencyIds: [id("task", "2")], nextAction: "resolve_dependencies" });

function collectButtons(node, found = []) {
  if (node === null || node === undefined || typeof node !== "object") return found;
  if (Array.isArray(node)) { for (const child of node) collectButtons(child, found); return found; }
  if (node.type === "button") found.push(node);
  collectButtons(node.props?.children, found);
  return found;
}

test("apply outcome starts the confirmed ready task, never the first element of the returned mapping", () => {
  const outcome = blueprintApplyOutcome(blockedFirst, trackerReady([waitingTask, trackerTask("2")]));
  assert.equal(outcome.nextAction, "start_ready_task");
  assert.notEqual(outcome.start.taskId, blockedFirst.tasks[0].taskId);
  assert.deepEqual(outcome.start, { taskId: id("task", "2"), agentId: id("agent", "B") });
  assert.deepEqual(outcome.createdTaskIds, [id("task", "1"), id("task", "2")]);
  assert.equal(outcome.roleCount, 2);
  assert.equal(outcome.taskCount, 2);
  // Readiness comes from the refreshed snapshot alone: the same mapping read
  // against a snapshot where only the leading task is ready selects that one.
  const reversed = blueprintApplyOutcome(blockedFirst, trackerReady([trackerTask("1"),
    trackerTask("2", { taskState: "blocked", readiness: "blocked", blockingDependencyIds: [id("task", "1")] })]));
  assert.deepEqual(reversed.start, { taskId: id("task", "1"), agentId: id("agent", "A") });
});

test("apply outcome reports blocked, unobserved and empty mappings without inventing a startable task", () => {
  const allBlocked = trackerReady([waitingTask, trackerTask("2", { taskState: "blocked", readiness: "blocked", blockingDependencyIds: [id("task", "1")] })]);
  assert.deepEqual(blueprintApplyOutcome(blockedFirst, allBlocked), {
    roleCount: 2, taskCount: 2, createdTaskIds: [id("task", "1"), id("task", "2")], start: null, nextAction: "no_ready_task"
  });
  const empty = blueprintApplyOutcome({ blueprintId: "web-app", roles: [], tasks: [] }, allBlocked);
  assert.equal(empty.nextAction, "no_ready_task");
  assert.equal(empty.start, null);
  // Unknown stays unknown: a loading or failed tracker, a snapshot that has not
  // observed every created task, and an unconfirmed readiness are not "blocked".
  for (const tracker of [{ kind: "loading" }, { kind: "failure" },
    trackerReady([trackerTask("2")]),
    trackerReady([waitingTask, trackerTask("2", { readiness: "unknown" })]),
    trackerReady([waitingTask, trackerTask("2", { freshness: "stale" })])]) {
    const outcome = blueprintApplyOutcome(blockedFirst, tracker);
    assert.equal(outcome.nextAction, "unconfirmed");
    assert.equal(outcome.start, null);
    assert.equal(blueprintStartIntent(outcome), null);
  }
});

test("a finished apply can only navigate: its surface holds no client and offers no launch", () => {
  const outcome = blueprintApplyOutcome(blockedFirst, trackerReady([waitingTask, trackerTask("2")]));
  const recorded = [];
  const props = { outcome, text: (key) => messages.en[key], onIntent: (intent) => recorded.push(intent) };
  assert.deepEqual(Object.keys(props).sort(), ["onIntent", "outcome", "text"]);
  const rendered = collectButtons(BlueprintApplyOutcomePanel(props));
  assert.equal(rendered.length, 2);
  for (const button of rendered) button.props.onClick();
  assert.deepEqual(recorded, [
    { kind: "prepare_run", taskId: id("task", "2"), agentId: id("agent", "B") },
    { kind: "view_plan", taskId: id("task", "1") }
  ]);
  assert.deepEqual([...new Set(recorded.map((intent) => intent.kind))].sort(), ["prepare_run", "view_plan"]);
  assert.deepEqual(blueprintPlanIntent(outcome), { kind: "view_plan", taskId: id("task", "1") });
  const blocked = blueprintApplyOutcome(blockedFirst, trackerReady([waitingTask,
    trackerTask("2", { taskState: "blocked", readiness: "blocked", blockingDependencyIds: [id("task", "1")] })]));
  const fallback = [];
  const only = collectButtons(BlueprintApplyOutcomePanel({ ...props, outcome: blocked, onIntent: (intent) => fallback.push(intent) }));
  assert.equal(only.length, 1);
  only[0].props.onClick();
  assert.deepEqual(fallback, [{ kind: "view_plan", taskId: id("task", "1") }]);
});

for (const locale of ["en", "ru"]) {
  test(`${locale} apply result separates created roles and tasks from a started run`, () => {
    const text = (key) => messages[locale][key];
    const ready = blueprintApplyOutcome(blockedFirst, trackerReady([waitingTask, trackerTask("2")]));
    const render = (outcome) => renderToStaticMarkup(createElement(BlueprintApplyOutcomePanel, { outcome, text, onIntent: () => {} })).replaceAll("<!-- -->", "");
    const html = render(ready);
    assert.ok(html.includes(text("blueprints_applied_title")));
    assert.ok(html.includes(text("blueprints_applied_not_started")));
    assert.ok(html.includes(`${text("blueprints_created")}: 2 ${text("blueprints_roles_count")}`));
    assert.ok(html.includes(`2 ${text("blueprints_tasks_count")}`));
    assert.ok(html.includes(text("blueprints_applied_start")));
    assert.ok(html.includes(text("blueprints_applied_start_help")));
    assert.ok(html.includes(text("blueprints_applied_view_plan")));
    assert.doesNotMatch(html, / style="/);
    assert.match(html, /role="status"/);
    for (const key of ["blueprints_applied_title", "blueprints_applied_not_started", "blueprints_applied_start",
      "blueprints_applied_start_help", "blueprints_applied_view_plan", "blueprints_applied_no_ready",
      "blueprints_applied_unconfirmed"]) {
      assert.equal(typeof messages.en[key], "string");
      assert.equal(typeof messages.ru[key], "string");
      assert.notEqual(messages.en[key], messages.ru[key]);
    }
    for (const [outcome, key] of [
      [blueprintApplyOutcome(blockedFirst, trackerReady([waitingTask, trackerTask("2", { taskState: "blocked", readiness: "blocked", blockingDependencyIds: [id("task", "1")] })])), "blueprints_applied_no_ready"],
      [blueprintApplyOutcome(blockedFirst, { kind: "loading" }), "blueprints_applied_unconfirmed"]
    ]) {
      const fallback = render(outcome);
      assert.ok(fallback.includes(text(key)));
      assert.ok(fallback.includes(text("blueprints_applied_not_started")));
      assert.ok(!fallback.includes(text("blueprints_applied_start")));
      assert.ok(fallback.includes(text("blueprints_applied_view_plan")));
    }
  });
}

test("apply copy names creation and the absent run in both locales", () => {
  assert.match(messages.en.blueprints_applied_not_started, /No run has started/);
  assert.match(messages.en.blueprints_applied_not_started, /creates roles and tasks only/);
  assert.match(messages.ru.blueprints_applied_not_started, /Ни один прогон не запущен/);
  assert.match(messages.ru.blueprints_applied_not_started, /только роли и задачи/);
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
});

for (const kind of ["publish", "revise"]) {
  test(`${kind}: New cannot discard an uncertain save; a confirmed refusal permits a fresh editor`, () => {
    const generated = ["original-key", "new-key"];
    const retryIdentity = new ContextPackRetryIdentity(() => generated.shift());
    const operation = snapshotSaveOperation({ draft: draft(),
      contextPackId: kind === "revise" ? id("context_pack") : null,
      expectedRevision: kind === "revise" ? id("evt") : null,
      idempotencyKey: retryIdentity.forOperation("original-draft") });
    let pending = operation;
    let editor = formFromDraft(operation.draft);
    let resets = 0;
    const original = JSON.stringify(operation);
    const onNew = () => { resets += 1; pending = null; editor = null; retryIdentity.reset(); };
    const button = ContextPackNewButton({ locked: true, text: (key) => messages.en[key], onNew });
    assert.equal(button.props.disabled, true);
    button.props.onClick();
    assert.equal(resets, 0);
    assert.equal(JSON.stringify(pending), original);
    assert.deepEqual(draftFromForm(editor), operation.draft);
    assert.equal(retryIdentity.forOperation("original-draft"), operation.idempotencyKey);
    assert.equal(pending.contextPackId, operation.contextPackId);
    assert.equal(pending.expectedRevision, operation.expectedRevision);
    // Once a definite refusal clears pending, New may rotate the editor identity.
    pending = null;
    const settled = ContextPackNewButton({ locked: false, text: (key) => messages.en[key], onNew });
    assert.equal(settled.props.disabled, false);
    settled.props.onClick();
    assert.equal(resets, 1);
    assert.equal(editor, null);
    assert.equal(retryIdentity.forOperation("original-draft"), "new-key");
  });
}
