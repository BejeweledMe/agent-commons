import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-tracker-parser-"));
execFileSync(
  resolve(root, "node_modules/.bin/tsc"),
  [
    "--ignoreConfig",
    "--target", "ES2022",
    "--module", "ESNext",
    "--moduleResolution", "Bundler",
    "--lib", "ES2022,DOM",
    "--outDir", compiled,
    resolve(root, "src/api.ts"),
    resolve(root, "src/trackerState.ts")
  ],
  { cwd: root }
);
const apiModule = await import(pathToFileURL(resolve(compiled, "api.js")).href);
const trackerStateModule = await import(pathToFileURL(resolve(compiled, "trackerState.js")).href);

function snapshot() {
  return {
    schema: "agent-commons.tracker.v1",
    sequence: 7,
    state: "ready",
    tasks: [{
      task_id: "task.1",
      title: "Build tracker",
      task_state: "active",
      readiness: "in_progress",
      dependency_task_ids: [],
      blocking_dependency_ids: [],
      owner_session_id: "session.1",
      role_name: "Builder",
      provider: "claude",
      profile_id: "claude-builder",
      phase: "running",
      awaits_human: false,
      next_action: "wait_for_run",
      freshness: "fresh",
      evidence_state: "complete",
      gaps: [],
      stderr: "must not survive parsing"
    }],
    edges: [],
    runs: [{
      delegation_id: "delegation.1",
      task_id: "task.1",
      agent_id: "agent.1",
      role_name: "Builder",
      provider: "claude",
      profile_id: "claude-builder",
      phase: "running",
      attempt_id: "attempt.1",
      attempt_number: 1,
      started_at: "2026-08-31T00:00:00Z",
      updated_at: "2026-08-31T00:00:10Z",
      finished_at: null,
      duration_seconds: 10,
      awaits_human: false,
      next_action: "wait_for_run",
      freshness: "fresh",
      evidence_state: "complete",
      transcript: "must not survive parsing"
    }],
    attention: [],
    capacity: { state: "available", active: 1, limit: 4, queued: 0, queue_capacity: 8 },
    freshness: {
      generated_at: "2026-08-31T00:00:10Z",
      source_updated_at: "2026-08-31T00:00:10Z",
      state: "fresh",
      resume_gap: false
    },
    focus_task_ids: ["task.1"],
    critical_path_task_ids: ["task.1"],
    critical_path_basis: "dependency_depth_only",
    critical_path_predictive: false,
    gaps: [],
    raw_provider_output: "must not survive parsing"
  };
}

test("tracker parser owns a safe exact projection", () => {
  const input = snapshot();
  input.tasks[0].gaps = ["stale_review"];
  input.gaps = ["review_evidence_missing"];
  const parsed = apiModule.parseTrackerSnapshot(input);
  assert.equal(parsed.sequence, 7);
  assert.equal(parsed.tasks[0].provider, "claude");
  assert.equal(parsed.runs[0].durationSeconds, 10);
  assert.deepEqual(parsed.tasks[0].gaps, ["stale_review"]);
  assert.deepEqual(parsed.gaps, ["review_evidence_missing"]);
  const rendered = JSON.stringify(parsed);
  assert.equal(rendered.includes("must not survive parsing"), false);
  assert.equal(rendered.includes("stderr"), false);
  assert.equal(rendered.includes("transcript"), false);
  assert.equal(rendered.includes("raw_provider_output"), false);
});

test("tracker parser refuses malformed and oversized snapshots", () => {
  const malformed = snapshot();
  malformed.freshness.resume_gap = "yes";
  assert.throws(() => apiModule.parseTrackerSnapshot(malformed));

  const oversized = snapshot();
  oversized.tasks = Array.from({ length: 513 }, () => snapshot().tasks[0]);
  assert.throws(() => apiModule.parseTrackerSnapshot(oversized));

  const predictive = snapshot();
  predictive.critical_path_predictive = true;
  assert.throws(() => apiModule.parseTrackerSnapshot(predictive));

  const nonStrings = snapshot();
  nonStrings.focus_task_ids = Array.from({ length: 10_000 }, () => 1);
  assert.throws(() => apiModule.parseTrackerSnapshot(nonStrings));

  const longTitle = snapshot();
  longTitle.tasks[0].title = "é".repeat(151);
  assert.throws(() => apiModule.parseTrackerSnapshot(longTitle));

  const hugeTitle = snapshot();
  hugeTitle.tasks[0].title = "x".repeat(2 * 1024 * 1024);
  assert.throws(() => apiModule.parseTrackerSnapshot(hugeTitle));

  const arbitraryEnum = snapshot();
  arbitraryEnum.runs[0].phase = "provider_says_everything_is_fine";
  assert.throws(() => apiModule.parseTrackerSnapshot(arbitraryEnum));

  const unsafeIdentifier = snapshot();
  unsafeIdentifier.tasks[0].task_id = "task.1\nsecret";
  assert.throws(() => apiModule.parseTrackerSnapshot(unsafeIdentifier));

  const arbitraryGap = snapshot();
  arbitraryGap.gaps = ["provider_supplied_gap"];
  assert.throws(() => apiModule.parseTrackerSnapshot(arbitraryGap));

  const missingArray = snapshot();
  delete missingArray.edges;
  assert.throws(() => apiModule.parseTrackerSnapshot(missingArray));
});

test("tracker keeps a newer stream snapshot across initial request outcomes", () => {
  const streamSnapshot = apiModule.parseTrackerSnapshot(snapshot());
  const ready = trackerStateModule.trackerStreamSucceeded({ kind: "loading" }, streamSnapshot);
  const afterFailure = trackerStateModule.trackerLoadFailed(ready);
  assert.equal(afterFailure.kind, "ready");
  assert.equal(afterFailure.snapshot.sequence, 7);
  assert.equal(afterFailure.connection, "disconnected");

  const olderInput = snapshot();
  olderInput.sequence = 6;
  const older = apiModule.parseTrackerSnapshot(olderInput);
  assert.equal(trackerStateModule.trackerLoadSucceeded(ready, older), ready);
  assert.equal(trackerStateModule.trackerLoadSucceeded(ready, streamSnapshot), ready);
});
