import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-tracker-actions-"));
execFileSync(
  resolve(root, "node_modules/.bin/tsc"),
  [
    "--ignoreConfig",
    "--target", "ES2022",
    "--module", "ESNext",
    "--moduleResolution", "Bundler",
    "--lib", "ES2022,DOM",
    "--outDir", compiled,
    resolve(root, "src/api.ts")
  ],
  { cwd: root }
);
const apiModule = await import(pathToFileURL(resolve(compiled, "api.js")).href);

const apiBase = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const taskId = "task.5HH3SWV3Q27WQNK0B3Z1YQAAXQ";
const taskRevision = "evt.01M1FPPEZHYF9C9QTR6PFKM2Q9";

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload
  };
}

function graphPayload() {
  return {
    nodes: [
      { kind: "task", id: "task.other", revision: "evt.01M1FPPEZHYF9C9QTR6PFKM2QA" },
      { kind: "task", id: taskId, revision: "evt.01M1FPPEZHYF9C9QTR6PFKM2QB", effective_revision: taskRevision },
      { kind: "delegation", id: "delegation.1", task_id: taskId }
    ]
  };
}

function newApi() {
  const api = new apiModule.WorkApi();
  api.apiBase = apiBase;
  return api;
}

test("review requests fetch the current graph revision; accept and reopen post the rendered one", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/graph")) {
      assert.equal(init.method, "GET");
      assert.equal(init.credentials, "same-origin");
      return jsonResponse(graphPayload());
    }
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "same-origin");
    return jsonResponse({ ok: true });
  };

  const api = newApi();
  const signal = new AbortController().signal;
  await api.requestTaskReview(taskId, ["criterion one", "", "criterion two"], "review-key", signal);
  await api.acceptTask(taskId, "accepted on exact review", "accept-key", signal, taskRevision);
  await api.reopenTask(taskId, "changes requested by review", "reopen-key", signal, taskRevision);

  // Accept and reopen never consult the graph: the revision the card rendered is the CAS.
  assert.deepEqual(calls.map((call) => call.url), [
    `${apiBase}/graph`,
    `${apiBase}/tasks/${taskId}/review-request`,
    `${apiBase}/tasks/${taskId}/accept`,
    `${apiBase}/tasks/${taskId}/reopen`
  ]);

  const review = JSON.parse(calls[1].init.body);
  assert.deepEqual(review, {
    expected_revision: taskRevision,
    criteria: ["criterion one", "criterion two"],
    idempotency_key: "review-key"
  });
  const accept = JSON.parse(calls[2].init.body);
  assert.deepEqual(accept, {
    expected_revision: taskRevision,
    summary: "accepted on exact review",
    idempotency_key: "accept-key"
  });
  const reopen = JSON.parse(calls[3].init.body);
  assert.deepEqual(reopen, {
    expected_revision: taskRevision,
    reason: "changes requested by review",
    idempotency_key: "reopen-key"
  });
  for (const body of [review, accept, reopen]) {
    for (const forbidden of ["prompt", "transcript", "raw_provider_output", "token_count", "cost", "eta", "percentage"]) {
      assert.equal(forbidden in body, false, forbidden);
    }
  }
});

test("tracker action refuses when current task revision is unavailable", async () => {
  globalThis.fetch = async (url) => {
    assert.equal(url, `${apiBase}/graph`);
    return jsonResponse({ nodes: [{ kind: "task", id: "task.other", revision: taskRevision }] });
  };

  await assert.rejects(
    () => newApi().requestTaskReview(taskId, ["criterion"], "review-key", new AbortController().signal),
    (error) => error instanceof apiModule.ApiProblem
      && error.status === 409
      && error.apiError?.code === "tracker_task_revision_unavailable"
  );
});

test("accept posts the rendered revision even after the task moved on, and surfaces the server's stale refusal", async () => {
  const calls = [];
  const newer = "evt.01M1FPPEZHYF9C9QTR6PFKM2QC";
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/graph")) return jsonResponse({ nodes: [{ kind: "task", id: taskId, revision: newer }] });
    const body = JSON.parse(init.body);
    // The server compares against its current revision; the rendered one is stale.
    return jsonResponse({ code: "stale_revision", message: "", safe_next_actions: [], expected: newer, sent: body.expected_revision }, 409);
  };
  await assert.rejects(
    () => newApi().acceptTask(taskId, "summary", "accept-key", new AbortController().signal, taskRevision),
    (error) => error instanceof apiModule.ApiProblem && error.status === 409
  );
  assert.deepEqual(calls.map((call) => call.url), [`${apiBase}/tasks/${taskId}/accept`], "no graph read: the newer revision is never fetched");
  assert.equal(JSON.parse(calls[0].init.body).expected_revision, taskRevision, "exactly the rendered revision is posted");
  // The same holds for returning work for revision.
  calls.length = 0;
  await assert.rejects(
    () => newApi().reopenTask(taskId, "reason", "reopen-key", new AbortController().signal, taskRevision),
    (error) => error instanceof apiModule.ApiProblem && error.status === 409
  );
  assert.equal(JSON.parse(calls[0].init.body).expected_revision, taskRevision);
});

test("a rendered revision that is not an event id is refused before any request", async () => {
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return jsonResponse({ ok: true }); };
  for (const bad of ["revision-from-provider", "", "task.5HH3SWV3Q27WQNK0B3Z1YQAAXQ"]) {
    await assert.rejects(
      () => newApi().acceptTask(taskId, "summary", `accept-${bad}`, new AbortController().signal, bad),
      (error) => error instanceof apiModule.ApiProblem && error.status === 409
        && error.apiError?.code === "tracker_task_revision_unavailable"
    );
  }
  assert.deepEqual(calls, []);
});

test("retrying a failed accept or reopen reuses the originally rendered revision; only starting over takes a new one", async () => {
  const calls = [];
  const rendered = taskRevision;
  const rerendered = "evt.01M1FPPEZHYF9C9QTR6PFKM2QD";
  let refuse = true;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    if (url.endsWith("/graph")) throw new Error("accept and reopen must not read the graph");
    return refuse ? jsonResponse({ code: "stale_revision", message: "", safe_next_actions: [] }, 409) : jsonResponse({ ok: true });
  };
  for (const [name, args] of [["acceptTask", ["accepted on exact review", "accept-key"]], ["reopenTask", ["changes requested", "reopen-key"]]]) {
    calls.length = 0; refuse = true;
    const api = newApi();
    const signal = new AbortController().signal;
    // 1. The first attempt at the rendered revision is refused as stale.
    await assert.rejects(() => api[name](taskId, ...args, signal, rendered), (error) => error instanceof apiModule.ApiProblem && error.status === 409);
    // 2. The detail has re-rendered with a newer revision, but the person presses "retry previous
    //    action": the same intent (same key) is sent again and must carry the ORIGINAL revision,
    //    because a retry may only repeat what the person already decided on.
    refuse = false;
    await api[name](taskId, ...args, signal, rerendered);
    assert.equal(calls.length, 2, name);
    assert.equal(calls[0].body.expected_revision, rendered, `${name}: first attempt posts the rendered revision`);
    assert.equal(calls[1].body.expected_revision, rendered, `${name}: the retry keeps the original rendered revision`);
    assert.deepEqual(calls[1].body, calls[0].body, `${name}: the retry is byte-identical`);
    // 3. Only the explicit "start over" path (forgetTaskWrite) forms a new intent at the newly rendered revision.
    api.forgetTaskWrite(args[1]);
    await api[name](taskId, ...args, signal, rerendered);
    assert.equal(calls[2].body.expected_revision, rerendered, `${name}: starting over posts the re-rendered revision`);
  }
  // The tracker's retry control replays the stored intent, whose expectedRevision was fixed when the
  // intent was formed, and never rebuilds it from the current render.
  const tracker = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  assert.match(tracker, /onClick=\{\(\) => void performTaskIntent\(actionState\.intent\)\}/, "retry replays the stored intent");
  assert.match(tracker, /return \{ action, taskId: task\.taskId, value, key, expectedRevision \};/, "the revision is captured into the intent once");
  assert.doesNotMatch(tracker, /expectedRevision\s*=[^=]/, "no code path reassigns a rendered revision");
  assert.doesNotMatch(tracker, /\.\.\.actionState\.intent,\s*expectedRevision/, "no retry rebuilds the intent with a newer revision");
});

test("the tracker hands its action panel the revision the inspector rendered", () => {
  const tracker = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  const inspector = readFileSync(resolve(root, "src/components/TaskInspector.tsx"), "utf8");
  // The inspector exposes the detail revision it renders; the tracker builds
  // every accept/return intent from that value and posts it unchanged.
  assert.match(inspector, /const rendered: RenderedTask = \{ revision: detail\?\.revision \?\? null \}/);
  assert.match(inspector, /typeof children === "function" \? children\(rendered\) : children/);
  assert.match(tracker, /\(\{ revision \}: RenderedTask\) => <>\{trackerTaskActions\(selectedTask, revision\)\}/);
  assert.match(tracker, /api\.acceptTask\(taskId, value as string, key, signal, expectedRevision\)/);
  assert.match(tracker, /api\.reopenTask\(taskId, value as string, key, signal, expectedRevision\)/);
  assert.match(tracker, /expectedRevision === null/, "an unrendered revision is refused, not fetched");
  assert.equal((tracker.match(/currentTaskRevision/g) ?? []).length, 0, "the tracker never fetches a revision to act on");
});

test("Now, Map and All tasks select through the one inspector path and one task row", () => {
  const tracker = readFileSync(resolve(root, "src/components/TrackerSection.tsx"), "utf8");
  const views = readFileSync(resolve(root, "src/components/TaskViews.tsx"), "utf8");
  // One selection callback and one keyboard handler serve every view, so the
  // inspector, its task actions and the keyboard help do not fork per view.
  assert.match(tracker, /onSelectTask=\{selectTask\} onTaskKeyDown=\{moveTaskFocus\}/);
  assert.equal((views.match(/function taskRow\(/g) ?? []).length, 1);
  assert.equal((views.match(/onSelectTask\(task\.taskId\)/g) ?? []).length, 1);
  assert.match(views, /onSelectTask=\{onSelectTask\}/, "graph nodes select the same task the rows do");
  assert.match(views, /id="tracker-keyboard-help"/);
  assert.equal((tracker.match(/<TaskInspector/g) ?? []).length, 1);
  // The role filter reaches every view: it narrows both inputs the views read.
  assert.match(tracker, /filterTrackerTasks\(state\.snapshot, filter, search, agentId\)/);
  assert.match(tracker, /bucketTasksForNow\(state\.snapshot, agentId\)/);
});

test("tracker action refuses malformed graph revisions before posting", async () => {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return jsonResponse({ nodes: [{ kind: "task", id: taskId, revision: "revision-from-provider" }] });
  };

  await assert.rejects(
    () => newApi().requestTaskReview(taskId, ["criterion"], "review-key", new AbortController().signal),
    (error) => error instanceof apiModule.ApiProblem && error.status === 502
  );
  assert.deepEqual(calls, [`${apiBase}/graph`]);
});
