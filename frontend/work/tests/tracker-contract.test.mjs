import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("tracker keeps a closed provider-safe browser contract", () => {
  const contracts = source("src/contracts.ts");
  const api = source("src/api.ts");
  assert.match(contracts, /agent-commons\.tracker\.v1/);
  assert.match(api, /parseTrackerSnapshot/);
  assert.match(api, /TRACKER_MAX_TASKS/);
  assert.match(api, /TRACKER_MAX_RUNS/);
  assert.match(api, /TRACKER_SOURCE_REVISION/);
  assert.match(api, /openTrackerStream/);
  assert.match(api, /withCredentials: true/);
  for (const forbidden of ["stderr", "transcript", "tool_arguments", "token_count", "cost", "eta", "percentage"]) {
    assert.equal(new RegExp(`\\b${forbidden}\\b`, "i").test(contracts), false, forbidden);
  }
});

test("tracker renders honest states and keyboard navigation", () => {
  const component = source("src/components/TrackerSection.tsx");
  const trackerState = source("src/trackerState.ts");
  for (const state of ["loading", "empty", "error", "partial", "stale"]) {
    assert.equal(component.includes(`snapshot.state === "${state}"`) || component.includes(`state.kind === "${state}"`), true, state);
  }
  assert.match(component, /ArrowDown/);
  assert.match(component, /ArrowUp/);
  assert.match(component, /aria-pressed/);
  assert.match(component, /aria-live/);
  assert.match(component, /role="alert"/);
  assert.match(trackerState, /current\.snapshot\.sequence >= snapshot\.sequence/);
  assert.match(trackerState, /current\.kind === "ready"/);
  assert.match(component, /tracker_critical_path_note/);
  assert.match(component, /tracker_source_revision/);
  assert.match(component, /tracker_truncated/);
  assert.match(source("src/contracts.ts"), /criticalPathPredictive: false/);
  for (const forbidden of ["percentage", "estimated cost", "token count", "live progress"] ) {
    assert.equal(component.toLowerCase().includes(forbidden), false, forbidden);
  }
});

test("tracker task actions use server revisions and canonical routes", () => {
  const api = source("src/api.ts");
  const component = source("src/components/TrackerSection.tsx");
  assert.match(api, /currentTaskRevision/);
  assert.match(api, /this\.get\("\/graph"/);
  assert.match(api, /\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/review-request/);
  assert.match(api, /\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/accept/);
  assert.match(api, /\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/reopen/);
  assert.match(api, /expected_revision: revision/);
  assert.match(component, /api\.requestTaskReview/);
  assert.match(component, /api\.acceptTask/);
  assert.match(component, /api\.reopenTask/);
  assert.match(component, /task\.nextAction === "request_review"/);
  assert.match(component, /task\.nextAction === "accept_task"/);
  assert.match(component, /task\.nextAction === "revise_work"/);
  assert.match(component, /tracker_action_success/);
  assert.match(component, /tracker_action_failed/);
  assert.match(component, /api\.loadTracker\(signal\)/);
  assert.doesNotMatch(component, /setSelectedTaskId\(.*nextAction/);
});

test("tracker locale keys stay paired and actionable", () => {
  const messages = JSON.parse(source("src/i18n.json"));
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
  for (const locale of ["en", "ru"]) {
    for (const key of [
      "tracker_loading",
      "tracker_empty",
      "tracker_error_next",
      "tracker_partial_next",
      "tracker_stale_next",
      "tracker_resume_gap",
      "tracker_keyboard_help",
      "tracker_actions_title",
      "tracker_request_review_action",
      "tracker_accept_action",
      "tracker_reopen_action"
    ]) {
      assert.equal(typeof messages[locale][key], "string");
      assert.notEqual(messages[locale][key].trim(), "");
    }
  }
});
