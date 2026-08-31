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
  assert.match(source("src/contracts.ts"), /criticalPathPredictive: false/);
  for (const forbidden of ["percentage", "estimated cost", "token count", "live progress"] ) {
    assert.equal(component.toLowerCase().includes(forbidden), false, forbidden);
  }
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
      "tracker_keyboard_help"
    ]) {
      assert.equal(typeof messages[locale][key], "string");
      assert.notEqual(messages[locale][key].trim(), "");
    }
  }
});
