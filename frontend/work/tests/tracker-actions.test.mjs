import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
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

test("tracker task actions fetch current graph revision and post canonical CAS bodies", async () => {
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
  await api.acceptTask(taskId, "accepted on exact review", "accept-key", signal);
  await api.reopenTask(taskId, "changes requested by review", "reopen-key", signal);

  assert.deepEqual(calls.map((call) => call.url), [
    `${apiBase}/graph`,
    `${apiBase}/tasks/${taskId}/review-request`,
    `${apiBase}/graph`,
    `${apiBase}/tasks/${taskId}/accept`,
    `${apiBase}/graph`,
    `${apiBase}/tasks/${taskId}/reopen`
  ]);

  const review = JSON.parse(calls[1].init.body);
  assert.deepEqual(review, {
    expected_revision: taskRevision,
    criteria: ["criterion one", "criterion two"],
    idempotency_key: "review-key"
  });
  const accept = JSON.parse(calls[3].init.body);
  assert.deepEqual(accept, {
    expected_revision: taskRevision,
    summary: "accepted on exact review",
    idempotency_key: "accept-key"
  });
  const reopen = JSON.parse(calls[5].init.body);
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
    () => newApi().acceptTask(taskId, "summary", "accept-key", new AbortController().signal),
    (error) => error instanceof apiModule.ApiProblem
      && error.status === 409
      && error.apiError?.code === "tracker_task_revision_unavailable"
  );
});

test("tracker action refuses malformed graph revisions before posting", async () => {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return jsonResponse({ nodes: [{ kind: "task", id: taskId, revision: "revision-from-provider" }] });
  };

  await assert.rejects(
    () => newApi().reopenTask(taskId, "reason", "reopen-key", new AbortController().signal),
    (error) => error instanceof apiModule.ApiProblem && error.status === 502
  );
  assert.deepEqual(calls, [`${apiBase}/graph`]);
});
