import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-work-task-create-"));
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

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload
  };
}

test("task creation sends canonical dependencies without private launch data", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse({ ok: true });
  };

  const api = new apiModule.WorkApi();
  api.apiBase = apiBase;
  await api.createTask(
    {
      title: "Dependent task",
      description: "Run only after the prerequisite is accepted",
      criteria: ["visible in tracker"],
      dependencyIds: ["task.5HH3SWV3Q27WQNK0B3Z1YQAAXQ"]
    },
    new AbortController().signal
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${apiBase}/tasks`);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.credentials, "same-origin");
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(Object.keys(body).sort(), [
    "acceptance_criteria",
    "dependencies",
    "description",
    "idempotency_key",
    "title"
  ]);
  assert.deepEqual(body.dependencies, ["task.5HH3SWV3Q27WQNK0B3Z1YQAAXQ"]);
  for (const forbidden of [
    "prompt",
    "transcript",
    "credentials",
    "operator_config",
    "raw_provider_output",
    "filesystem_path",
    "eta",
    "percentage",
    "cost",
    "token_count"
  ]) {
    assert.equal(forbidden in body, false, forbidden);
  }
});
