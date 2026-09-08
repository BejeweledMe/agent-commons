import assert from "node:assert/strict";
import test from "node:test";
import { taskObservationCurrent } from "../src/taskPresentation.ts";

const task = { freshness: "fresh", readiness: "ready" };
const stale = { state: "stale", freshness: { state: "stale", resumeGap: false },
  gaps: ["projection_stale", "attempts_partial"] };

test("stale provider progress does not disable an observed canonical task", () => {
  assert.equal(taskObservationCurrent(stale, task), true);
  assert.equal(taskObservationCurrent(stale, { ...task, readiness: "in_progress" }), true);
  assert.equal(stale.freshness.state, "stale");
});

test("unknown subject, old graph, missing source and resume gaps remain gated", () => {
  for (const freshness of ["stale", "unknown"]) {
    assert.equal(taskObservationCurrent(stale, { ...task, freshness }), false);
  }
  for (const readiness of ["unknown", "policy_unknown", "terminal_dependency_failure"]) {
    assert.equal(taskObservationCurrent(stale, { ...task, readiness }), false);
  }
  for (const gap of ["graph_stale", "graph_malformed", "projection_missing",
    "source_revision_unavailable", "resume_gap"]) {
    assert.equal(taskObservationCurrent({ ...stale, gaps: [...stale.gaps, gap] }, task), false);
  }
  assert.equal(taskObservationCurrent({ ...stale,
    freshness: { state: "stale", resumeGap: true } }, task), false);
  assert.equal(taskObservationCurrent({ ...stale,
    freshness: { state: "unknown", resumeGap: false } }, task), false);
  for (const state of ["error", "loading", "empty"]) {
    assert.equal(taskObservationCurrent({ ...stale, state }, task), false);
  }
});
