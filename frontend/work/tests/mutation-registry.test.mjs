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
const compiled = mkdtempSync(resolve(tmpdir(), "commons-mutation-registry-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled, resolve(root, "src/mutationRegistry.ts"), resolve(root, "src/mutationFocus.ts"), resolve(root, "src/components/MutationFeedback.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const registry = await import(pathToFileURL(resolve(compiled, "mutationRegistry.js")));
const { MutationFeedback } = await import(pathToFileURL(resolve(compiled, "components/MutationFeedback.js")));
const { focusOnWriteStart, focusOnWriteEnd } = await import(pathToFileURL(resolve(compiled, "mutationFocus.js")));
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const styles = readFileSync(resolve(root, "src/styles.css"), "utf8");
const shellSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");
const feedbackSource = readFileSync(resolve(root, "src/components/MutationFeedback.tsx"), "utf8");

const {
  MUTATION_ELAPSED_HINT_MS,
  MUTATION_STILL_SAVING_HINT_MS,
  MutationRegistry,
  mutationElapsedSeconds,
  mutationHint,
  mutationState,
  mutationSurface
} = registry;

const projectA = `project.${"a".repeat(32)}`;
const projectB = `project.${"b".repeat(32)}`;

/** A clock the test moves by hand: no threshold below depends on wall time. */
function clock(start = 1_000_000) {
  let now = start;
  return { now: () => now, advance(ms) { now += ms; return now; } };
}

test("a write is keyed by project, surface and operation, and its elapsed time follows the injected clock", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  const key = { projectId: projectA, surface: "tasks", operation: "create-task" };
  assert.equal(store.elapsedMs(key, time.now()), null, "nothing is in flight before begin");
  const handle = store.begin(key);
  assert.deepEqual({ ...handle }, { ...key, startedAt: 1_000_000, initiator: null }, "no initiator unless the shell passes one");
  assert.equal(store.elapsedMs(key, time.advance(2_500)), 2_500);
  assert.equal(store.elapsedMs({ ...key, operation: "create-role" }, time.now()), null, "another operation is another write");
  assert.equal(store.elapsedMs({ ...key, projectId: projectB }, time.now()), null, "another project is another write");
  store.end(handle);
  assert.equal(store.elapsedMs(key, time.now()), null);
});

test("elapsed thresholds are exact and reported in whole seconds", () => {
  assert.equal(MUTATION_ELAPSED_HINT_MS, 2_000);
  assert.equal(MUTATION_STILL_SAVING_HINT_MS, 8_000);
  assert.equal(mutationHint(null), "none");
  assert.equal(mutationHint(0), "none");
  assert.equal(mutationHint(1_999), "none");
  assert.equal(mutationHint(2_000), "elapsed");
  assert.equal(mutationHint(7_999), "elapsed");
  assert.equal(mutationHint(8_000), "still_saving");
  assert.equal(mutationHint(600_000), "still_saving");
  assert.equal(mutationElapsedSeconds(2_000), 2);
  assert.equal(mutationElapsedSeconds(2_999), 2);
  assert.equal(mutationElapsedSeconds(-5), 0, "a clock that moved backwards never shows a negative wait");
});

test("in-flight writes are reported per project and per surface, never across projects", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  const task = store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" });
  time.advance(10);
  store.begin({ projectId: projectB, surface: "roles", operation: "create-role" });
  assert.deepEqual(store.inFlight(projectA).map((entry) => entry.operation), ["create-task"]);
  assert.deepEqual(store.inFlight(projectB).map((entry) => entry.operation), ["create-role"]);
  assert.deepEqual(store.inFlight(projectA, "roles"), [], "a surface with no write of its own reports none");
  assert.deepEqual(store.inFlight(null), [], "a legacy single-workspace host is its own key");
  store.end(task);
  assert.deepEqual(store.inFlight(projectA), []);
  assert.deepEqual(store.inFlight(projectB).map((entry) => entry.operation), ["create-role"],
    "ending one project's write leaves the other project's write untouched");
});

test("ending is by handle identity and safe to repeat, so a confirmed write may end before its follow-up read", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  const key = { projectId: projectA, surface: "setup", operation: "runtime" };
  const first = store.begin(key);
  const second = store.begin(key);
  assert.equal(store.inFlight(projectA).length, 2, "two writes with the same key are two writes");
  store.end(first);
  store.end(first);
  assert.equal(store.inFlight(projectA).length, 1);
  assert.deepEqual(store.inFlight(projectA)[0], second);
  store.end(second);
  assert.deepEqual(store.inFlight(projectA), []);
});

test("elapsed time of a repeated key follows the oldest write still in flight", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  const key = { projectId: projectA, surface: "launch", operation: "start-run" };
  const first = store.begin(key);
  time.advance(3_000);
  store.begin(key);
  assert.equal(store.elapsedMs(key, time.now()), 3_000);
  store.end(first);
  assert.equal(store.elapsedMs(key, time.now()), 0);
});

test("subscribers see every begin and end and can unsubscribe", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  const seen = [];
  const unsubscribe = store.subscribe(() => seen.push(store.inFlight(projectA).length));
  const handle = store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" });
  store.end(handle);
  store.end(handle);
  unsubscribe();
  store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" });
  assert.deepEqual(seen, [1, 0], "a repeated end publishes nothing and an unsubscribed listener hears nothing");
});

test("every shell write operation is attributed to the surface that owns its form", () => {
  assert.equal(mutationSurface("create-task"), "tasks");
  assert.equal(mutationSurface("create-role"), "roles");
  assert.equal(mutationSurface("start-run"), "launch");
  assert.equal(mutationSurface("initialize"), "setup");
  assert.equal(mutationSurface("runtime"), "setup");
  for (const action of ["login", "cancel", "check"]) {
    assert.equal(mutationSurface(`provider-auth-${action}`), "provider-auth");
  }
  assert.equal(mutationSurface("something-new"), "shell", "an unnamed operation claims no surface it may not own");
});

test("a surface names its own write, and another surface's write as the reason it waits", () => {
  const time = clock();
  const store = new MutationRegistry(time.now);
  assert.deepEqual(mutationState(store.inFlight(projectA), "tasks"), { kind: "idle" });
  const handle = store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" });
  const entries = store.inFlight(projectA);
  assert.deepEqual(mutationState(entries, "tasks"), { kind: "saving", handle });
  for (const surface of ["roles", "launch", "setup", "provider-auth", "shell"]) {
    assert.deepEqual(mutationState(entries, surface), { kind: "blocked", handle },
      "the one server write lock is named, not pretended away");
  }
  store.end(handle);
  assert.deepEqual(mutationState(store.inFlight(projectA), "launch"), { kind: "idle" });
});

function render(entries, surface, locale, now) {
  return renderToStaticMarkup(createElement(MutationFeedback, {
    entries, surface, clock: () => now, text: (key) => messages[locale][key]
  }));
}

for (const locale of ["en", "ru"]) {
  test(`${locale}: the duration hints appear only at 2 s and 8 s, with the agreed copy`, () => {
    const time = clock();
    const store = new MutationRegistry(time.now);
    const handle = store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" });
    const entries = store.inFlight(projectA);
    const large = messages[locale].mutation_large_project;
    const still = messages[locale].mutation_still_saving;

    const short = render(entries, "tasks", locale, handle.startedAt + 1_999);
    assert.equal(short, '<div class="mutation-feedback" tabindex="-1"></div>',
      "a short write says nothing beyond the busy control itself; the container only holds focus dropped by the disabled control");

    const elapsed = render(entries, "tasks", locale, handle.startedAt + 3_000);
    assert.ok(elapsed.includes(`3 ${messages[locale].mutation_elapsed_unit}`), "an elapsed counter in whole seconds");
    // The per-second counter is hidden from assistive technology; only the hint copy,
    // which changes at the 2 s and 8 s transitions, lives in the live region.
    assert.match(elapsed, /<span class="mutation-feedback-elapsed" aria-hidden="true">3 /);
    assert.match(elapsed, new RegExp(`<span class="mutation-feedback-hint" role="status">${large.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</span>`));
    assert.equal(elapsed.split('role="status"').length - 1, 1, "one live region, holding the hint only");
    assert.ok(elapsed.includes(large));
    assert.ok(!elapsed.includes(still));

    const long = render(entries, "tasks", locale, handle.startedAt + 8_000);
    assert.ok(long.includes(`8 ${messages[locale].mutation_elapsed_unit}`));
    assert.ok(long.includes(still), "the same key is still in flight, so clicking again is unnecessary");
    assert.ok(!long.includes(large));

    const blocked = render(entries, "launch", locale, handle.startedAt + 9_000);
    assert.ok(blocked.includes(messages[locale].mutation_blocked_elsewhere));
    assert.ok(!blocked.includes(still), "another surface reports the wait, never this write's own duration");
    assert.equal(render(store.inFlight(projectB), "launch", locale, handle.startedAt + 9_000), "",
      "a write in another project leaves this project's surfaces silent");
  });
}

test("mutation copy exists in both locales and the surface styling is class-based and never green", () => {
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
  for (const key of ["mutation_elapsed_unit", "mutation_large_project", "mutation_still_saving", "mutation_blocked_elsewhere"]) {
    assert.ok(messages.en[key].length > 0 && messages.ru[key].length > 0, `${key} is written in both locales`);
    assert.notEqual(messages.en[key], messages.ru[key]);
  }
  assert.match(styles, /\.mutation-feedback \{/);
  assert.match(styles, /\.mutation-feedback-blocked \{/);
  assert.doesNotMatch(feedbackSource, /style=\{/, "no inline styles");
  const rules = styles.split("\n").filter((line) => line.startsWith(".mutation-feedback")).join("\n");
  assert.ok(rules.length > 0);
  assert.doesNotMatch(rules, /#[0-9a-f]{3,8}|green/i,
    "colour comes from the shared neutral tokens; saving is never dressed as an accepted outcome");
});

test("the shell holds one registry and routes every write through it", () => {
  assert.doesNotMatch(shellSource, /activeAction/, "the global busy flag is gone");
  assert.match(shellSource, /const mutationsRef = useRef\(new MutationRegistry\(\)\)/);
  for (const operation of ["create-task", "start-run"]) {
    // Every write passes the focused control, captured before the write disables it (mutationFocus.ts).
    assert.match(shellSource, new RegExp(`mutationsRef\\.current\\.begin\\(\\{ projectId, surface: mutationSurface\\("${operation}"\\), operation: "${operation}" \\}, typeof document === "undefined" \\? null : document\\.activeElement\\)`));
  }
  assert.match(shellSource, /mutationsRef\.current\.begin\(\{ projectId, surface: mutationSurface\(action\), operation: action \}, typeof document === "undefined" \? null : document\.activeElement\)/,
    "perform() keys its write by the operation it was asked to run");
  assert.match(shellSource, /mutationsRef\.current\.begin\(\{ projectId, surface: mutationSurface\(operation\), operation \}, typeof document === "undefined" \? null : document\.activeElement\)/,
    "the provider authentication path is a write like any other");
  assert.equal(shellSource.match(/mutationsRef\.current\.begin\(/g).length, 4, "four write paths");
  assert.equal(shellSource.match(/mutationsRef\.current\.end\(handle\)/g).length, 5,
    "each of the four ends unconditionally, and a confirmed write also ends before its follow-up read");
});

test("the write remembers the control that started it, and focus moves to the feedback and back only when it was dropped", () => {
  const store = new MutationRegistry(clock().now);
  const fake = (name, extra = {}) => ({ name, focused: 0, focus() { this.focused += 1; }, isConnected: true, disabled: false, closest: () => null, ...extra });
  const body = fake("body"), container = fake("container"), button = fake("submit"), other = fake("search");
  const handle = store.begin({ projectId: projectA, surface: "tasks", operation: "create-task" }, button);
  assert.equal(handle.initiator, button, "the initiating control travels with the handle, captured before it is disabled");
  assert.equal(store.begin({ projectId: projectA, surface: "launch", operation: "start-run" }).initiator, null);

  // Start: the disabled control dropped focus to the body, so the container takes it.
  assert.equal(focusOnWriteStart(body, body, container), container);
  assert.equal(focusOnWriteStart(null, body, container), container);
  assert.equal(focusOnWriteStart(other, body, container), null, "focus that moved elsewhere on purpose is left alone");
  assert.equal(focusOnWriteStart(body, body, null), null, "nothing to take it before the feedback is mounted");

  // End: the control is enabled again and gets focus back from the body or the container.
  assert.equal(focusOnWriteEnd(button, body, body, container), button);
  assert.equal(focusOnWriteEnd(button, container, body, container), button);
  assert.equal(focusOnWriteEnd(button, null, body, container), button);
  assert.equal(focusOnWriteEnd(button, other, body, container), null, "a deliberate move to another control wins");
  assert.equal(focusOnWriteEnd(fake("gone", { isConnected: false }), body, body, container), null, "an unmounted control is not focused");
  assert.equal(focusOnWriteEnd(fake("still-disabled", { disabled: true }), body, body, container), null);
  assert.equal(focusOnWriteEnd(fake("in-disabled-fieldset", { closest: (selector) => selector === "fieldset:disabled" ? {} : null }), body, body, container), null);
  assert.equal(focusOnWriteEnd(null, body, body, container), null, "a write with no known initiator restores nothing");
  assert.equal(focusOnWriteEnd(container, body, body, container), null);

  // The component consults the same rules: the feedback container is what takes dropped focus.
  assert.match(feedbackSource, /focusOnWriteStart\(document\.activeElement, document\.body, container\.current\)/);
  assert.match(feedbackSource, /focusOnWriteEnd\(initiator, document\.activeElement, document\.body, surface\)/);
  assert.match(feedbackSource, /handle\.initiator instanceof HTMLElement/);
});
