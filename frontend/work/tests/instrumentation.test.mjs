import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "commons-instrumentation-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--resolveJsonModule", "--allowSyntheticDefaultImports",
  "--outDir", compiled,
  resolve(root, "src/instrumentation.ts"),
  resolve(root, "src/api.ts"),
  resolve(root, "src/components/InstrumentationSettings.tsx"),
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (file) => import(pathToFileURL(resolve(compiled, file)).href);
const {
  INSTRUMENTATION_BUCKETS, INSTRUMENTATION_KINDS, INSTRUMENTATION_OUTCOMES, MAX_INSTRUMENTATION_BATCH,
  InstrumentationBatcher, durationBucket, failureOutcome, instrumentationEvent, isInstrumentationEvent
} = await load("instrumentation.js");
const { ApiProblem, WorkApi, parseInstrumentation, requestOutcome } = await load("api.js");
const { InstrumentationSettings } = await load("components/InstrumentationSettings.js");

const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const mainSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");
const moduleSource = readFileSync(resolve(root, "src/instrumentation.ts"), "utf8");
const panelSource = readFileSync(resolve(root, "src/components/InstrumentationSettings.tsx"), "utf8");
const trackerSource = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
const boardSource = readFileSync(resolve(root, "src/components/ProjectBoard.tsx"), "utf8");
const libraryEditorSource = readFileSync(resolve(root, "src/components/ServiceLibraryEditor.tsx"), "utf8");

const API_BASE = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const REVISION = "a".repeat(64);
const NEXT_REVISION = "b".repeat(64);
/** One valid answer from GET /api/instrumentation, with the patch under test applied. */
const answer = (patch = {}) => ({
  schema: "agent_commons.instrumentation.v1",
  enabled: false,
  ui_version: "1",
  counters: {},
  retention: { window_days: 30, updated_at_day: "2026-09-19" },
  revision: REVISION,
  ...patch
});
const parsed = (patch = {}) => ({
  schema: "agent_commons.instrumentation.v1",
  enabled: false,
  uiVersion: "1",
  counters: {},
  retention: { windowDays: 30, updatedAtDay: "2026-09-19" },
  revision: REVISION,
  ...patch
});
const panelProps = (patch = {}) => ({
  instrumentation: null, unavailable: false, busy: false, writesEnabled: true, hint: null, clearAsked: false,
  onToggle() {}, onAskClear() {}, onCancelClear() {}, onConfirmClear() {}, text: (key) => messages.en[key],
  ...patch
});
// Anything that would move the counters off this computer, in either language.
const FORBIDDEN = /export|download|upload|share|экспорт|выгруз|загруз|поделит|отправ/i;

test("GET /api/instrumentation is parsed strictly: closed keys at every level, closed enums, integer counts", () => {
  assert.deepEqual(
    parseInstrumentation(answer({ enabled: true, counters: { task_create: { confirmed: { lt_1s: 2, ge_8s: 1 } } } })),
    parsed({ enabled: true, counters: { task_create: { confirmed: { lt_1s: 2, ge_8s: 1 } } } })
  );
  const withoutCounters = answer();
  delete withoutCounters.counters;
  const refused = [
    null, [], "x", 3, undefined,
    { ...answer(), extra: 1 },
    withoutCounters,
    answer({ schema: "agent_commons.instrumentation.v2" }),
    answer({ enabled: "true" }),
    answer({ ui_version: "" }),
    answer({ ui_version: 1 }),
    answer({ revision: "A".repeat(64) }),
    answer({ revision: "a".repeat(63) }),
    answer({ revision: `sha256:${"a".repeat(64)}` }),
    answer({ retention: { window_days: 30 } }),
    answer({ retention: { window_days: 30, updated_at_day: "2026-09-19", extra: 1 } }),
    answer({ retention: { window_days: 0, updated_at_day: "2026-09-19" } }),
    answer({ retention: { window_days: 30, updated_at_day: "19-09-2026" } }),
    answer({ counters: { task_delete: { confirmed: { lt_1s: 1 } } } }),
    answer({ counters: { task_create: { finished: { lt_1s: 1 } } } }),
    answer({ counters: { task_create: { confirmed: { lt_3s: 1 } } } }),
    answer({ counters: { task_create: { confirmed: { lt_1s: -1 } } } }),
    answer({ counters: { task_create: { confirmed: { lt_1s: 1.5 } } } }),
    answer({ counters: { task_create: { confirmed: { lt_1s: "1" } } } }),
    answer({ counters: { task_create: "none" } })
  ];
  for (const [index, value] of refused.entries()) {
    assert.throws(
      () => parseInstrumentation(value),
      (error) => error instanceof ApiProblem && error.status === 502,
      `refused[${index}]`
    );
  }
});

test("the event mapper is total over the closed vocabulary, its buckets are exclusive, and it refuses everything else", () => {
  const durations = { lt_1s: [0, 1, 999], lt_2s: [1000, 1999], lt_8s: [2000, 7999], ge_8s: [8000, 60_000] };
  const seen = new Set();
  for (const kind of INSTRUMENTATION_KINDS) {
    for (const outcome of INSTRUMENTATION_OUTCOMES) {
      for (const bucket of INSTRUMENTATION_BUCKETS) {
        for (const ms of durations[bucket]) {
          const event = instrumentationEvent(kind, outcome, ms);
          assert.deepEqual(event, { kind, outcome, duration_bucket: bucket }, `${kind}/${outcome}/${ms}`);
          assert.equal(Object.keys(event).length, 3, "an event has exactly three fields");
          assert.ok(isInstrumentationEvent(event));
          seen.add(`${kind}|${outcome}|${bucket}`);
        }
      }
    }
  }
  assert.equal(seen.size, INSTRUMENTATION_KINDS.length * INSTRUMENTATION_OUTCOMES.length * INSTRUMENTATION_BUCKETS.length);
  assert.equal(seen.size, 112, "every kind × outcome × bucket triple is reachable");
  for (const ms of [0, 999, 1000, 1999, 2000, 7999, 8000, 1e9]) {
    const bucket = durationBucket(ms);
    assert.equal(INSTRUMENTATION_BUCKETS.filter((candidate) => candidate === bucket).length, 1, `${ms} lands in one bucket`);
  }
  for (const kind of ["task_delete", "TASK_CREATE", "", "navigation ", "task.create", "__proto__"]) {
    assert.equal(instrumentationEvent(kind, "confirmed", 10), null, kind);
  }
  for (const outcome of ["finished", "ok", "", "Confirmed"]) {
    assert.equal(instrumentationEvent("task_create", outcome, 10), null, outcome);
  }
  for (const ms of [-1, NaN, Infinity, -Infinity, "500", null, undefined, {}]) {
    assert.equal(instrumentationEvent("task_create", "confirmed", ms), null, String(ms));
    assert.equal(durationBucket(ms), null, String(ms));
  }
  // The payload guard is the same closed shape, so no id, title or free text can
  // ride along inside something that merely looks like an event.
  assert.equal(isInstrumentationEvent({ kind: "task_create", outcome: "confirmed" }), false);
  assert.equal(isInstrumentationEvent({ kind: "task_create", outcome: "confirmed", duration_bucket: "lt_1s", taskId: "task.x" }), false);
  assert.equal(isInstrumentationEvent({ kind: "task_create", outcome: "confirmed", duration_bucket: "lt_3s" }), false);
  assert.equal(isInstrumentationEvent(null), false);
  assert.equal(isInstrumentationEvent([]), false);
  assert.equal(isInstrumentationEvent("task_create"), false);
});

test("a failed action's outcome comes from the abort flag and the status, never from a message", () => {
  assert.equal(failureOutcome(true, 422), "cancelled");
  assert.equal(failureOutcome(true, null), "cancelled");
  assert.equal(failureOutcome(false, 400), "refused");
  assert.equal(failureOutcome(false, 409), "refused");
  assert.equal(failureOutcome(false, 499), "refused");
  assert.equal(failureOutcome(false, 500), "uncertain");
  assert.equal(failureOutcome(false, 399), "uncertain");
  assert.equal(failureOutcome(false, null), "uncertain");
  assert.equal(requestOutcome(new ApiProblem(422, null)), "refused");
  assert.equal(requestOutcome(new ApiProblem(503, null)), "uncertain");
  assert.equal(requestOutcome(new Error("boom")), "uncertain");
  assert.equal(requestOutcome(new DOMException("aborted", "AbortError")), "cancelled");
});

test("the batcher emits nothing while the toggle is off, and turning it off drops what was queued", async () => {
  const posted = [];
  const batcher = new InstrumentationBatcher({
    post: async (events, revision) => { posted.push(events); return { kind: "accepted", revision }; },
    flushDelayMs: 5000
  });
  batcher.setRevision(REVISION);
  batcher.record("task_create", "confirmed", 10);
  assert.equal(batcher.pending(), 0, "an off batcher queues nothing");
  await batcher.flush();
  assert.deepEqual(posted, [], "an off batcher issues no request");

  batcher.setEnabled(true);
  batcher.record("task_create", "confirmed", 10);
  batcher.record("task_create", "not_an_outcome", 10);
  assert.equal(batcher.pending(), 1, "an event outside the vocabulary is dropped, not repaired");
  batcher.setEnabled(false);
  assert.equal(batcher.pending(), 0, "turning the toggle off drops the queue");
  await batcher.flush();
  assert.deepEqual(posted, []);

  // Without a revision from a GET there is nothing to bind a batch to.
  const unbound = new InstrumentationBatcher({ post: async () => { throw new Error("unreachable"); }, flushDelayMs: 5000 });
  unbound.setEnabled(true);
  unbound.record("navigation", "confirmed", 0);
  await unbound.flush();
  assert.equal(unbound.pending(), 1);
  unbound.setEnabled(false);
});

test("a full batch flushes at 64 and never offers the server more than it accepts", async () => {
  assert.equal(MAX_INSTRUMENTATION_BATCH, 64);
  const posted = [];
  const batcher = new InstrumentationBatcher({
    post: async (events, revision) => { posted.push(events); return { kind: "accepted", revision: NEXT_REVISION }; },
    flushDelayMs: 5000
  });
  batcher.setEnabled(true);
  batcher.setRevision(REVISION);
  for (let index = 0; index < 63; index += 1) batcher.record("navigation", "confirmed", index);
  assert.equal(posted.length, 0, "a partial batch waits for the timer");
  batcher.record("navigation", "confirmed", 63);
  await new Promise((done) => setTimeout(done, 0));
  assert.equal(posted.length, 1);
  assert.equal(posted[0].length, 64);
  assert.equal(batcher.pending(), 0);
  batcher.setEnabled(false);
});

test("the short timer flushes a partial batch exactly once", async () => {
  const posted = [];
  const batcher = new InstrumentationBatcher({
    post: async (events, revision) => { posted.push(events); return { kind: "accepted", revision }; },
    flushDelayMs: 1
  });
  batcher.setEnabled(true);
  batcher.setRevision(REVISION);
  batcher.record("library_edit", "confirmed", 1500);
  batcher.record("board_edit", "cancelled", 30);
  await new Promise((done) => setTimeout(done, 20));
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0], [
    { kind: "library_edit", outcome: "confirmed", duration_bucket: "lt_2s" },
    { kind: "board_edit", outcome: "cancelled", duration_bucket: "lt_1s" }
  ]);
  await new Promise((done) => setTimeout(done, 20));
  assert.equal(posted.length, 1, "an empty queue never posts again");
  batcher.setEnabled(false);
});

test("204 is ignored, 409 re-reads and hints, 422 hints and drops, and none of them retries in a loop", async () => {
  const cases = [
    { outcome: { kind: "accepted", revision: NEXT_REVISION }, hints: [null], rereads: 0 },
    { outcome: { kind: "ignored" }, hints: [], rereads: 0 },
    { outcome: { kind: "stale" }, hints: ["stale"], rereads: 1 },
    { outcome: { kind: "rejected" }, hints: ["rejected"], rereads: 0 },
    { outcome: { kind: "unavailable" }, hints: [], rereads: 0 }
  ];
  for (const { outcome, hints, rereads } of cases) {
    const posted = [];
    const seenHints = [];
    let reread = 0;
    const batcher = new InstrumentationBatcher({
      post: async (events) => { posted.push(events); return outcome; },
      onHint: (hint) => seenHints.push(hint),
      onReread: () => { reread += 1; },
      flushDelayMs: 5000
    });
    batcher.setEnabled(true);
    batcher.setRevision(REVISION);
    batcher.record("board_edit", "refused", 100);
    await batcher.flush();
    assert.equal(posted.length, 1, outcome.kind);
    assert.equal(batcher.pending(), 0, `${outcome.kind} drops the batch it sent`);
    assert.deepEqual(seenHints, hints, outcome.kind);
    assert.equal(reread, rereads, outcome.kind);
    await batcher.flush();
    assert.equal(posted.length, 1, `${outcome.kind} never replays the batch`);
    batcher.setEnabled(false);
  }
});

test("a batch is posted revision-bound with exactly the three allowlisted fields and nothing else", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: API_BASE });
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return { ok: true, status: 200, json: async () => answer({ enabled: true, revision: NEXT_REVISION }) };
  };
  const events = [
    instrumentationEvent("task_create", "confirmed", 10),
    instrumentationEvent("run_start", "uncertain", 9000)
  ];
  const updated = await client.recordInstrumentation(events, REVISION, new AbortController().signal);
  assert.equal(updated.revision, NEXT_REVISION);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${API_BASE}/instrumentation/events`);
  assert.deepEqual(calls[0].body, {
    events: [
      { kind: "task_create", outcome: "confirmed", duration_bucket: "lt_1s" },
      { kind: "run_start", outcome: "uncertain", duration_bucket: "ge_8s" }
    ],
    expected_revision: REVISION
  });

  // 204 carries no body: the server is not recording, which is not an error.
  globalThis.fetch = async () => ({ ok: true, status: 204, json: async () => { throw new Error("no body"); } });
  assert.equal(await client.recordInstrumentation(events, REVISION, new AbortController().signal), null);

  // Nothing outside the vocabulary leaves the client, whatever a caller holds.
  let sent = 0;
  globalThis.fetch = async () => { sent += 1; return { ok: true, status: 204, json: async () => { throw new Error("no body"); } }; };
  for (const rejected of [
    [{ kind: "task_create", outcome: "confirmed", duration_bucket: "lt_1s", taskId: "task.x" }],
    [{ kind: "task_delete", outcome: "confirmed", duration_bucket: "lt_1s" }],
    [],
    new Array(65).fill(events[0])
  ]) {
    await assert.rejects(
      () => client.recordInstrumentation(rejected, REVISION, new AbortController().signal),
      (error) => error instanceof ApiProblem && error.status === 400
    );
  }
  assert.equal(sent, 0, "a refused batch never reaches the network");

  // 409 and 422 are the server's own refusals, surfaced by status alone.
  for (const status of [409, 422]) {
    globalThis.fetch = async () => ({ ok: false, status, json: async () => ({ error: { code: "instrumentation_revision_conflict", message: "", safe_next_actions: [] } }) });
    await assert.rejects(
      () => client.recordInstrumentation(events, REVISION, new AbortController().signal),
      (error) => error instanceof ApiProblem && error.status === status
    );
  }
});

test("the toggle and the clearing control post exactly the revision the panel was shown", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: API_BASE });
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return url.endsWith("/clear")
      ? { ok: true, status: 204, json: async () => { throw new Error("no body"); } }
      : { ok: true, status: 200, json: async () => answer({ enabled: true }) };
  };
  assert.equal((await client.setInstrumentation(true, REVISION, new AbortController().signal)).enabled, true);
  assert.equal(await client.clearInstrumentation(REVISION, new AbortController().signal), undefined);
  assert.deepEqual(calls.map((call) => call.url), [
    `${API_BASE}/instrumentation/settings`,
    `${API_BASE}/instrumentation/clear`
  ]);
  assert.deepEqual(calls[0].body, { enabled: true, expected_revision: REVISION });
  assert.deepEqual(calls[1].body, { expected_revision: REVISION });

  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => answer({ enabled: true, ui_version: "2" }) });
  await assert.rejects(
    () => client.setInstrumentation(true, REVISION, new AbortController().signal),
    (error) => error instanceof ApiProblem && error.status === 502,
    "an answer outside the schema is refused rather than shown"
  );
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: Settings offers the toggle, the plain-words disclosure and the counted totals`, () => {
    const text = (key) => messages[locale][key];
    const instrumentation = parsed({
      enabled: true,
      counters: { task_create: { confirmed: { lt_1s: 2, lt_2s: 3 }, refused: { lt_1s: 1 } } }
    });
    const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({ instrumentation, text })));
    assert.ok(markup.includes(messages[locale].instrumentation_toggle_label), "the toggle is named in words");
    assert.match(markup, /id="instrumentation-enabled"/);
    assert.match(markup, /type="checkbox"/);
    assert.match(markup, /checked=""/);
    assert.doesNotMatch(markup, /disabled=""/, "a writable panel disables nothing here");
    // The disclosure exists and is closed until someone opens it.
    assert.match(markup, /<details class="instrumentation-disclosure">/);
    assert.doesNotMatch(markup, /<details class="instrumentation-disclosure" open/);
    for (const key of [
      "instrumentation_disclosure_summary", "instrumentation_counted_title", "instrumentation_counted",
      "instrumentation_never_title", "instrumentation_never", "instrumentation_storage_title",
      "instrumentation_storage", "instrumentation_stays_here"
    ]) {
      assert.ok(markup.includes(messages[locale][key]), `${locale}.${key}`);
    }
    // The counters are read-only totals per kind and outcome, in the enum order.
    assert.match(markup, /<table class="instrumentation-counters"/);
    assert.ok(markup.includes(messages[locale].instrumentation_kind_task_create));
    assert.match(markup, /<td>5<\/td><td>1<\/td><td>0<\/td><td>0<\/td>/);
    assert.equal(markup.includes(messages[locale].instrumentation_kind_navigation), false, "a kind with no counts is not listed");
    assert.ok(markup.includes(messages[locale].instrumentation_clear));
  });
}

test("the read-only panel renders the section with the toggle and the clearing control unavailable", () => {
  const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({
    instrumentation: parsed(), writesEnabled: false
  })));
  assert.ok(markup.includes(messages.en.instrumentation_title));
  assert.equal((markup.match(/disabled=""/g) ?? []).length, 2, "the toggle and the clearing control are both unavailable");
  assert.ok(markup.includes(messages.en.instrumentation_readonly));
  assert.doesNotMatch(markup, /checked=""/);
  assert.ok(markup.includes(messages.en.instrumentation_counters_empty));
});

test("a stale or refused batch is one line beside the toggle, in both languages", () => {
  for (const locale of ["en", "ru"]) {
    for (const hint of ["stale", "rejected"]) {
      const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({
        hint, text: (key) => messages[locale][key]
      })));
      assert.ok(markup.includes(messages[locale][`instrumentation_hint_${hint}`]), `${locale}.${hint}`);
      assert.match(markup, /role="status"/);
    }
  }
  // An unreadable local store says so and counts nothing.
  const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({ unavailable: true })));
  assert.ok(markup.includes(messages.en.instrumentation_unavailable));
  assert.match(markup, /id="instrumentation-enabled"[^>]*disabled=""|disabled=""[^>]*id="instrumentation-enabled"/);
});

test("clearing asks first and names exactly what it removes", () => {
  const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({
    instrumentation: parsed(), clearAsked: true
  })));
  assert.match(markup, /role="dialog"/);
  assert.ok(markup.includes(messages.en.instrumentation_clear_title));
  assert.ok(markup.includes(messages.en.instrumentation_clear_details));
  assert.ok(markup.includes(messages.en.instrumentation_clear_confirm));
  assert.ok(markup.includes(messages.en.instrumentation_clear_cancel));
});

test("EN and RU cover every new string, and none of them offers to export, upload or share the counters", () => {
  const keys = Object.keys(messages.en).filter((key) => key.startsWith("instrumentation_"));
  assert.equal(keys.length, 35);
  assert.deepEqual(keys.sort(), Object.keys(messages.ru).filter((key) => key.startsWith("instrumentation_")).sort());
  for (const locale of ["en", "ru"]) {
    for (const key of keys) {
      const value = messages[locale][key];
      assert.equal(typeof value, "string", `${locale}.${key}`);
      assert.notEqual(value, "", `${locale}.${key}`);
      assert.doesNotMatch(value, FORBIDDEN, `${locale}.${key}`);
    }
    const markup = renderToStaticMarkup(createElement(InstrumentationSettings, panelProps({
      instrumentation: parsed({ enabled: true, counters: { board_edit: { confirmed: { lt_1s: 1 } } } }),
      clearAsked: true, text: (key) => messages[locale][key]
    })));
    assert.doesNotMatch(markup, FORBIDDEN, locale);
    assert.doesNotMatch(markup, /<a |href=|type="file"|formaction|<form/, locale);
  }
  // Nothing about the counters is kept in the browser.
  for (const [name, source] of [["instrumentation.ts", moduleSource], ["InstrumentationSettings.tsx", panelSource]]) {
    assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/, name);
  }
  const shell = mainSource.slice(
    mainSource.indexOf("const instrumentationBatcher"),
    mainSource.indexOf("// Role first")
  );
  assert.ok(shell.length > 0);
  assert.doesNotMatch(shell, /localStorage|sessionStorage|indexedDB|document\.cookie/);
});

test("the seven existing completion points report their own outcome and nothing else", () => {
  const sources = { mainSource, trackerSource, boardSource, libraryEditorSource };
  const emitted = [...Object.values(sources).join("\n").matchAll(/(?<![.\w])recordInstrumentation\("([a-z_]+)"/g)]
    .map((match) => match[1]);
  assert.deepEqual([...new Set(emitted)].sort(), [...INSTRUMENTATION_KINDS].sort(), "all seven kinds are emitted");
  assert.match(mainSource, /recordInstrumentation\("task_create", "confirmed", performance\.now\(\) - started\)/);
  assert.match(mainSource, /recordInstrumentation\("task_create", requestOutcome\(error\), performance\.now\(\) - started\)/);
  assert.match(mainSource, /recordInstrumentation\("run_start", "confirmed", performance\.now\(\) - started\)/);
  assert.match(mainSource, /recordInstrumentation\("run_start", requestOutcome\(error\), performance\.now\(\) - started\)/);
  assert.match(mainSource, /recordInstrumentation\("run_prepare", "confirmed", 0\)/);
  assert.match(mainSource, /recordInstrumentation\("navigation", "confirmed", 0\)/);
  assert.match(trackerSource, /recordInstrumentation\("task_action", "confirmed", performance\.now\(\) - started\)/);
  assert.match(trackerSource, /recordInstrumentation\("task_action", requestOutcome\(error\), performance\.now\(\) - started\)/);
  assert.match(boardSource, /recordInstrumentation\("board_edit", requestOutcome\(error\), performance\.now\(\) - started\)/);
  assert.match(libraryEditorSource, /recordInstrumentation\("library_edit", requestOutcome\(error\), performance\.now\(\) - started\)/);
  // Every call site passes a literal kind, a literal or derived outcome and a
  // measured duration: an id, a title or a canonical value has no way in.
  for (const [name, source] of Object.entries(sources)) {
    const calls = [...source.matchAll(/(?<![.\w])recordInstrumentation\(([^;]*?)\);/g)];
    assert.ok(calls.length > 0, name);
    for (const call of calls) {
      assert.match(
        call[1],
        /^"[a-z_]+", (?:"[a-z_]+"|requestOutcome\(error\)), (?:0|performance\.now\(\) - started)$/,
        `${name}: ${call[1]}`
      );
    }
  }
});

test("the shell reads the toggle from the server, maps its status to one outcome and stops emitting before the request", () => {
  assert.match(mainSource, /if \(next === null\) return \{ kind: "ignored" \};/);
  assert.match(mainSource, /if \(status === 409\) return \{ kind: "stale" \};/);
  assert.match(mainSource, /if \(status === 400 \|\| status === 422\) return \{ kind: "rejected" \};/);
  assert.match(mainSource, /return \{ kind: "unavailable" \};/);
  assert.match(mainSource, /onReread: \(\) => setInstrumentationRefresh\(\(current\) => current \+ 1\)/);
  assert.match(mainSource, /batcher\.setRevision\(instrumentation\?\.revision \?\? null\);/);
  assert.match(mainSource, /batcher\.setEnabled\(instrumentation\?\.enabled === true\);/);
  assert.match(mainSource, /if \(!enabled\) instrumentationBatcher\.current\?\.setEnabled\(false\);/);
  // A failed read counts nothing and says so, rather than assuming the toggle.
  assert.match(mainSource, /setInstrumentation\(null\);\s*\n\s*setInstrumentationUnavailable\(true\);/);
});
