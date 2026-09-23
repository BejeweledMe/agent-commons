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
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-stop-reason-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  resolve(root, "src/api.ts"), resolve(root, "src/stopReason.ts"),
  resolve(root, "src/components/TaskInspector.tsx")
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (file) => import(pathToFileURL(resolve(compiled, file)).href);
const { launchRefusalFromError, parseProviderDiagnostic, parseStopReason, parseStopReasonSummary,
  parseTaskDetail, parseTrackerSnapshot } = await load("api.js");
const { LAUNCH_REFUSAL_CODES, STOP_REASON_CODES, currentStopReason, newestRun, runInProgress,
  stopReasonAction, stopReasonSentence } = await load("stopReason.js");
const { TaskInspectorContent } = await load("components/TaskInspector.js");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const mainSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");
const cardSource = readFileSync(resolve(root, "src/components/DecisionCard.tsx"), "utf8");

const taskId = `task.${"0".repeat(26)}`;
const otherId = `task.${"1".repeat(26)}`;
const revision = `evt.${"0".repeat(26)}`;
const nextRevision = `evt.${"1".repeat(26)}`;
const artifactId = `artifact.${"0".repeat(26)}`;
const NEWLINE = String.fromCharCode(10);
const NUL = String.fromCharCode(0);
const ESCAPE = String.fromCharCode(27);

const detailPayload = () => ({ schema: "agent_commons.ui.entity.v1", kind: "task", id: taskId, record: {
  id: taskId, state: "ready", revision, effective_revision: nextRevision,
  title: "A readable task", description: "A concrete goal", acceptance_criteria: ["An observable criterion"],
  artifact_bindings: [{ ref: { kind: "artifact", id: artifactId }, revision }]
} });
const task = (overrides = {}) => ({ taskId, title: "A readable task", taskState: "ready", readiness: "ready",
  dependencyTaskIds: [], blockingDependencyIds: [], ownerSessionId: null, roleName: null, provider: null,
  profileId: null, phase: null, awaitsHuman: false, nextAction: "start_ready_work", freshness: "fresh",
  evidenceState: "complete", gaps: [], ...overrides });
const run = (overrides = {}) => ({ delegationId: `delegation.${"0".repeat(26)}`, taskId, agentId: null,
  roleName: "Builder", provider: "claude", profileId: "claude-builder", phase: "failed", attemptId: null,
  attemptNumber: 1, startedAt: "2026-09-21T10:00:00Z", updatedAt: "2026-09-21T10:05:00Z",
  finishedAt: "2026-09-21T10:05:00Z", durationSeconds: 300, wallTimeSeconds: 1200, awaitsHuman: false,
  nextAction: "inspect_failure", freshness: "fresh", evidenceState: "complete", stopReason: null,
  providerDiagnostic: null, ...overrides });
const stop = (overrides = {}) => ({ code: "executor_access_missing",
  reason: "The selected checkout is not a Git worktree root.", nextAction: "select_git_worktree", ...overrides });

const answerEntry = createElement("button", { type: "button", className: "button button-inline conversation-entry" }, "Answer");
function card(current, options = {}) {
  const { locale = "en", runs = [], detail = parseTaskDetail(detailPayload(), taskId),
    actionsCurrent = true, writesEnabled = true } = options;
  const text = (key) => { assert.equal(typeof messages[locale][key], "string", key); return messages[locale][key]; };
  return TaskInspectorContent({ task: current, tasks: [current, task({ taskId: otherId, title: "Other" })],
    runs, sourceRevision: revision, locale, text, actionsCurrent, writesEnabled,
    onSelectTask: () => {}, onLaunchTask: () => {}, detailState: { kind: "ready", taskId, detail },
    children: null, answerControl: answerEntry, onRefresh: () => {} });
}

// The native disclosure's initially visible subtree: a closed <details> shows
// only its summary. Copied from the inspector suite, which models it the same way.
function initiallyVisible(node) {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(initiallyVisible).join("");
  if (typeof node !== "object") return String(node);
  if (typeof node.type === "function") return initiallyVisible(node.type(node.props));
  const children = node.props?.children;
  if (node.type === "details" && !node.props.open) {
    return initiallyVisible((Array.isArray(children) ? children : [children]).find((child) => child?.type === "summary"));
  }
  return initiallyVisible(children);
}

// --------------------------------------------------------------------- parser

test("the typed field is accepted only as the server's exact three keys", () => {
  assert.deepEqual(parseStopReason({ code: "provider_reported_error", reason: "Safe diagnostic.",
    next_action: "inspect_provider_diagnostic" }),
  { code: "provider_reported_error", reason: "Safe diagnostic.", nextAction: "inspect_provider_diagnostic" });
  const valid = () => ({ code: "executor_access_missing", reason: "A bounded reason.", next_action: "select_checkout" });
  for (const mutation of [
    (value) => { delete value.reason; },
    (value) => { value.extra = "must not be read"; },
    (value) => { value.reason = 7; },
    (value) => { value.reason = ""; },
    (value) => { value.code = ""; },
    (value) => { value.next_action = ""; },
    (value) => { value.reason = "r".repeat(257); },
    (value) => { value.code = "c".repeat(65); },
    (value) => { value.reason = `A reason${NUL}with a control character`; },
    (value) => { value.reason = `A reason${ESCAPE}[31m`; }
  ]) {
    const value = valid(); mutation(value);
    assert.equal(parseStopReason(value), null, JSON.stringify(value));
  }
  for (const value of [null, undefined, "stop", 7, [], { }]) assert.equal(parseStopReason(value), null);
});

test("an additive code keeps its object and reads as the neutral sentence", () => {
  const additive = parseStopReason({ code: "future_server_code", reason: "A bounded reason.", next_action: "select_checkout" });
  assert.equal(additive.code, "future_server_code");
  assert.equal(stopReasonSentence("future_server_code"), "stop_reason_unknown");
  assert.equal(stopReasonSentence("unknown"), "stop_reason_unknown");
  for (const code of STOP_REASON_CODES) {
    assert.equal(typeof messages.en[stopReasonSentence(code)], "string", code);
    if (code !== "unknown") assert.notEqual(stopReasonSentence(code), "stop_reason_unknown", code);
  }
  // The pre-attempt set is the launch subset of the same closed code set.
  assert.deepEqual([...LAUNCH_REFUSAL_CODES].sort(), ["executor_access_missing", "provider_mcp_handshake_failed",
    "unknown", "workspace_snapshot_unreadable"]);
  for (const code of LAUNCH_REFUSAL_CODES) assert.ok(STOP_REASON_CODES.includes(code), code);
});

test("the bounded summary decodes the same object and nothing else", () => {
  const encoded = '{"code":"provider_reported_error","next_action":"inspect_provider_diagnostic","reason":"Safe diagnostic."}';
  assert.deepEqual(parseStopReasonSummary(`stop_reason:${encoded}`),
    { code: "provider_reported_error", reason: "Safe diagnostic.", nextAction: "inspect_provider_diagnostic" });
  // The runtime appends the same JSON to its prose in one bounded summary.
  assert.deepEqual(parseStopReasonSummary(`The allowlisted provider exited without a canonical successful result. stop_reason:${encoded}`),
    { code: "provider_reported_error", reason: "Safe diagnostic.", nextAction: "inspect_provider_diagnostic" });
  for (const malformed of [
    "The provider exited after requesting input.",
    "stop_reason:",
    "stop_reason:not json",
    'stop_reason:{"code":"unknown"}',
    'stop_reason:{"code":"unknown","reason":"r","next_action":"select_checkout","extra":"x"}',
    `stop_reason:${encoded} and trailing prose`,
    'prefix stop_reason_like:{"code":"unknown","reason":"r","next_action":"select_checkout"}',
    null, 7, { code: "unknown" }
  ]) {
    assert.equal(parseStopReasonSummary(malformed), null, String(malformed));
  }
  // Nothing is ever read out of process output: a stderr-shaped line is not a code.
  assert.equal(parseStopReasonSummary(`handshaking with MCP server failed: connection closed${NEWLINE}provider_mcp_handshake_failed`), null);
});

function snapshot(runOverrides = {}) {
  return {
    schema: "agent-commons.tracker.v1", sequence: 3, source_revision: `sha256:${"0".repeat(64)}`,
    truncated: false, state: "ready",
    tasks: [{ task_id: "task.1", title: "Build", task_state: "active", readiness: "in_progress",
      dependency_task_ids: [], blocking_dependency_ids: [], owner_session_id: null, role_name: "Builder",
      provider: "claude", profile_id: "claude-builder", phase: "failed", awaits_human: false,
      next_action: "retry_new_run", freshness: "fresh", evidence_state: "complete", gaps: [] }],
    edges: [],
    runs: [{ delegation_id: "delegation.1", task_id: "task.1", agent_id: "agent.1", role_name: "Builder",
      provider: "claude", profile_id: "claude-builder", phase: "failed", attempt_id: "attempt.1",
      attempt_number: 1, started_at: "2026-09-21T00:00:00Z", updated_at: "2026-09-21T00:03:21Z",
      finished_at: "2026-09-21T00:03:21Z", duration_seconds: 201, wall_time_seconds: 1200,
      awaits_human: false, next_action: "retry_new_run", freshness: "fresh", evidence_state: "complete",
      ...runOverrides }],
    attention: [], capacity: { state: "available", active: 0, limit: 4, queued: 0, queue_capacity: 8 },
    freshness: { generated_at: "2026-09-21T00:03:30Z", source_updated_at: "2026-09-21T00:03:21Z",
      state: "fresh", resume_gap: false },
    focus_task_ids: [], critical_path_task_ids: [], critical_path_basis: "dependency_depth_only",
    critical_path_predictive: false, gaps: []
  };
}

test("a run carries the reason from its own field, from the summary, or not at all", () => {
  const typed = parseTrackerSnapshot(snapshot({ stop_reason: { code: "provider_mcp_handshake_failed",
    reason: "The executor MCP server did not complete initialization.", next_action: "repair_mcp_configuration" } }));
  assert.deepEqual(typed.runs[0].stopReason, { code: "provider_mcp_handshake_failed",
    reason: "The executor MCP server did not complete initialization.", nextAction: "repair_mcp_configuration" });

  const fromSummary = parseTrackerSnapshot(snapshot({ summary:
    'The allowlisted provider failed before canonical start. stop_reason:{"code":"provider_reported_error","next_action":"inspect_provider_diagnostic","reason":"Safe diagnostic."}' }));
  assert.equal(fromSummary.runs[0].stopReason.code, "provider_reported_error");

  // A field the client cannot read never discards the snapshot, and never
  // suppresses the summary the server also wrote.
  const recovered = parseTrackerSnapshot(snapshot({ stop_reason: { code: "unknown" },
    summary: 'stop_reason:{"code":"unknown","next_action":"inspect_canonical_outcome","reason":"The provider exited successfully but did not record a canonical terminal result."}' }));
  assert.equal(recovered.runs[0].stopReason.nextAction, "inspect_canonical_outcome");
  const absent = parseTrackerSnapshot(snapshot());
  assert.equal(absent.runs[0].stopReason, null);
  assert.equal(absent.runs[0].providerDiagnostic, null);
  assert.equal(parseTrackerSnapshot(snapshot({ stop_reason: "provider_reported_error",
    summary: "no encoded reason here" })).runs[0].stopReason, null);
});

test("the bounded diagnostic survives sanitized, and an incomplete or oversized tail does not", () => {
  const diagnostic = `failed to load models cache: missing field${NEWLINE}required MCP servers failed to initialize`;
  const parsed = parseTrackerSnapshot(snapshot({ stderr_diagnostic_tail: `${diagnostic}${ESCAPE}[0m` }));
  assert.ok(parsed.runs[0].providerDiagnostic.includes("required MCP servers failed to initialize"));
  assert.ok(parsed.runs[0].providerDiagnostic.includes(NEWLINE), "the diagnostic keeps its own lines");
  assert.equal(parsed.runs[0].providerDiagnostic.includes(ESCAPE), false, "no control sequence reaches the DOM");
  // A tail the server marked incomplete is withheld, not shown as evidence.
  const truncated = parseTrackerSnapshot(snapshot({ stderr_diagnostic_tail: diagnostic,
    stderr_diagnostic_tail_truncated: true }));
  assert.equal(truncated.runs[0].providerDiagnostic, null);
  assert.equal(parseProviderDiagnostic("x".repeat(4097)), null);
  assert.equal(parseProviderDiagnostic("я".repeat(2049)), null, "the bound is bytes, not characters");
  assert.equal(parseProviderDiagnostic(""), null);
  assert.equal(parseProviderDiagnostic(NUL + NUL), null);
  assert.equal(parseProviderDiagnostic(7), null);
  assert.equal(parseProviderDiagnostic("x".repeat(4096)).length, 4096);
});

test("only a pre-attempt admission code becomes a launch refusal", () => {
  const refusal = launchRefusalFromError({ code: "executor_access_missing",
    message: "Provider launch was refused before any delegation attempt or child session existed. The selected checkout is unavailable.",
    safeNextActions: ["select_checkout"] });
  assert.equal(refusal.code, "executor_access_missing");
  assert.equal(refusal.nextAction, "select_checkout");
  assert.ok(refusal.reason.includes("The selected checkout is unavailable."));
  // A stop-only code, an unrelated failure, a missing message: no refusal here.
  assert.equal(launchRefusalFromError({ code: "provider_reported_error", message: "m", safeNextActions: [] }), null);
  assert.equal(launchRefusalFromError({ code: "run_limit_refused", message: "m", safeNextActions: [] }), null);
  assert.equal(launchRefusalFromError({ code: "unknown", message: "", safeNextActions: [] }), null);
  assert.equal(launchRefusalFromError(null), null);
  // No named next action stays empty rather than inventing one.
  assert.equal(launchRefusalFromError({ code: "unknown", message: "A bounded reason.", safeNextActions: [] }).nextAction, "");
});

// ------------------------------------------------------------------ rendering

for (const locale of ["en", "ru"]) {
  const text = (key) => messages[locale][key];
  test(`${locale}: every code renders one sentence, the recorded reason and one action`, () => {
    for (const code of [...STOP_REASON_CODES, "future_server_code"]) {
      const reason = stop({ code, reason: "The recorded bounded reason.", nextAction: "repair_control_files" });
      const visible = initiallyVisible(card(task({ nextAction: "retry_new_run" }), { locale, runs: [run({ stopReason: reason })] }));
      assert.ok(visible.includes(text(stopReasonSentence(code))), `${code} sentence`);
      assert.ok(visible.includes("The recorded bounded reason."), `${code} recorded reason`);
      assert.ok(visible.includes(text("stop_action_repair_control_files")), `${code} action`);
      assert.equal(visible.includes("repair_control_files"), false, `${code} raw token`);
    }
    // A code this interface does not know is never empty and never invented.
    const neutral = initiallyVisible(card(task(), { locale, runs: [run({ stopReason: stop({ code: "future_server_code" }) })] }));
    assert.ok(neutral.includes(text("stop_reason_unknown")));
  });

  test(`${locale}: an action token outside the glossary is shown verbatim, never guessed`, () => {
    const view = card(task(), { locale, runs: [run({ stopReason: stop({ nextAction: "future_action_token" }) })] });
    const markup = renderToStaticMarkup(view);
    assert.ok(markup.includes(text("stop_action_unrecognized")));
    assert.ok(markup.includes("<code>future_action_token</code>"));
    const unnamed = renderToStaticMarkup(card(task(), { locale, runs: [run({ stopReason: stop({ nextAction: "" }) })] }));
    assert.ok(unnamed.includes(text("stop_action_unnamed")));
  });

  test(`${locale}: a run stopped on the person says what is needed instead of the generic copy`, () => {
    for (const phase of ["needs_operator", "input_needed"]) {
      const reason = stop({ code: "executor_stopped_by_environment",
        reason: "The session was limited to the selected checkout.", nextAction: "answer_operator_request" });
      const current = task({ taskState: "active", readiness: "human_attention", awaitsHuman: true,
        phase, nextAction: "inspect_failure" });
      const visible = initiallyVisible(card(current, { locale, runs: [run({ phase, stopReason: reason })] }));
      assert.ok(visible.includes(text("stop_reason_executor_stopped_by_environment")), phase);
      assert.ok(visible.includes("The session was limited to the selected checkout."), phase);
      assert.equal(visible.includes(text("tracker_gloss_inspect_failure")), false, `${phase}: generic copy replaced`);
      // The one thing the interface can do for this token is open the conversation.
      assert.ok(visible.includes("Answer"), phase);
    }
  });

  test(`${locale}: a reason that asks for a re-read offers the inspector's refresh control`, () => {
    const reason = stop({ code: "unknown", reason: "The provider exited without a canonical result.",
      nextAction: "inspect_canonical_outcome" });
    const visible = initiallyVisible(card(task({ nextAction: "inspect_failure" }), { locale, runs: [run({ phase: "needs_operator", stopReason: reason })] }));
    assert.ok(visible.includes(text("stop_action_inspect_canonical_outcome")));
    assert.ok(visible.includes(text("inspector_detail_retry")));
    // A repair the interface cannot perform stays a hint: no control is invented.
    const hint = renderToStaticMarkup(card(task({ nextAction: "inspect_failure" }), { locale,
      runs: [run({ stopReason: stop({ nextAction: "repair_runtime_profile" }) })] }));
    assert.ok(hint.includes(text("stop_action_repair_runtime_profile")));
    assert.equal(hint.includes(text("inspector_detail_retry")), false);
  });

  test(`${locale}: the reason is stated for every run in the history, newest first on the card`, () => {
    const older = run({ delegationId: `delegation.${"2".repeat(26)}`, updatedAt: "2026-09-21T09:00:00Z",
      finishedAt: "2026-09-21T09:00:00Z", stopReason: stop({ code: "workspace_snapshot_unreadable",
        reason: "An older run reason.", nextAction: "select_git_worktree" }) });
    const newer = run({ stopReason: stop({ code: "provider_reported_error",
      reason: "The newest run reason.", nextAction: "inspect_provider_diagnostic" }) });
    const visible = initiallyVisible(card(task({ nextAction: "retry_new_run" }), { locale, runs: [older, newer] }));
    assert.ok(visible.includes("An older run reason."), "history keeps the older reason");
    assert.ok(visible.includes("The newest run reason."));
    // Only the newest run explains the state the card is describing.
    assert.equal(newestRun([older, newer]).delegationId, newer.delegationId);
    assert.equal(currentStopReason([older, newer]).reason, "The newest run reason.");
    assert.equal(currentStopReason([run()]), null);
  });

  test(`${locale}: the bounded provider diagnostic never reaches the first layer`, () => {
    const diagnostic = `failed to load models cache: missing field base_instructions${NEWLINE}handshaking with MCP server failed`;
    const view = card(task(), { locale, runs: [run({ providerDiagnostic: diagnostic, stopReason: stop() })] });
    const markup = renderToStaticMarkup(view);
    const visible = initiallyVisible(view);
    assert.ok(markup.includes("handshaking with MCP server failed"));
    assert.equal(visible.includes("handshaking with MCP server failed"), false);
    assert.equal(visible.includes("base_instructions"), false);
    assert.ok(markup.includes(text("inspector_run_diagnostic")));
    // It lives in a disclosure that starts closed, inside the run's details.
    assert.ok(markup.includes('<details class="run-diagnostic">'));
    assert.equal(markup.includes('class="run-diagnostic" open'), false);
    assert.ok(markup.indexOf('<details class="run-diagnostic">') < markup.indexOf("handshaking with MCP server failed"));
    assert.ok(markup.indexOf('class="inspector-technical"') < markup.indexOf('<details class="run-diagnostic">'));
  });

  test(`${locale}: a live run means the card does not offer to prepare another one`, () => {
    for (const phase of ["requested", "reserved", "launching", "running", "cancellation_requested"]) {
      const view = card(task({ nextAction: "start_ready_work" }), { locale, runs: [run({ phase, finishedAt: null })] });
      const markup = renderToStaticMarkup(view);
      assert.equal(markup.includes(text("inspector_prepare_run")), false, phase);
      assert.ok(initiallyVisible(view).includes(text("decision_run_in_progress")), phase);
      assert.equal(runInProgress([run({ phase })]).phase, phase);
    }
    // A finished run leaves the ready task exactly as it was.
    const settled = renderToStaticMarkup(card(task({ nextAction: "start_ready_work" }), { locale, runs: [run({ phase: "succeeded" })] }));
    assert.ok(settled.includes(text("inspector_prepare_run")));
    assert.equal(settled.includes(text("decision_run_in_progress")), false);
    assert.equal(runInProgress([run({ phase: "succeeded" })]), null);
    assert.equal(runInProgress([]), null);
  });

  test(`${locale}: a stop reason never adds a second primary control`, () => {
    const markup = renderToStaticMarkup(card(task({ nextAction: "start_ready_work" }),
      { locale, runs: [run({ stopReason: stop({ nextAction: "retry_new_run" }) })] }));
    assert.equal((markup.match(/class="button button-primary/g) ?? []).length, 1);
  });
}

test("EN and RU carry every new string, and the card never styles inline", () => {
  const keys = ["decision_run_in_progress", "stop_reason_next", "stop_reason_recorded", "stop_action_unnamed",
    "stop_action_unrecognized", "launch_refused_before_attempt", "inspector_run_diagnostic", "inspector_run_diagnostic_help",
    ...STOP_REASON_CODES.map((code) => stopReasonSentence(code)),
    ...["select_checkout", "select_git_worktree", "repair_control_files", "repair_runtime_profile",
      "repair_mcp_configuration", "run_provider_canary", "inspect_provider_diagnostic",
      "inspect_canonical_outcome"].map((token) => stopReasonAction(token).key)];
  for (const key of keys) {
    for (const locale of ["en", "ru"]) {
      assert.equal(typeof messages[locale][key], "string", `${locale}.${key}`);
      assert.notEqual(messages[locale][key], "", `${locale}.${key}`);
    }
    assert.notEqual(messages.en[key], messages.ru[key], key);
  }
  assert.equal(cardSource.includes("style={"), false);
  assert.equal(readFileSync(resolve(root, "src/components/StopReasonNotice.tsx"), "utf8").includes("style={"), false);
});

test("the closed action glossary names every token the runtime emits with a refusal", () => {
  const refusals = readFileSync(resolve(root, "../../src/agent_commons/runtime/refusals.py"), "utf8");
  for (const token of ["select_checkout", "select_git_worktree", "repair_control_files", "repair_runtime_profile"]) {
    assert.ok(refusals.includes(token), token);
    assert.notEqual(stopReasonAction(token).key, null, token);
  }
  for (const code of ["workspace_snapshot_unreadable", "executor_access_missing",
    "provider_mcp_handshake_failed", "executor_stopped_by_environment", "provider_reported_error", "unknown"]) {
    assert.ok(refusals.includes(code), code);
    assert.ok(STOP_REASON_CODES.includes(code), code);
  }
  assert.deepEqual(stopReasonAction("future_action_token"), { key: null, control: null });
});

// --------------------------------------------- the refusal beside Prepare run

test("a refused launch is rendered beside the control that asked for it, with the draft kept", () => {
  const refusal = launchRefusalFromError({ code: "workspace_snapshot_unreadable",
    message: "Provider launch was refused before any delegation attempt or child session existed. The selected checkout could not be snapshotted safely.",
    safeNextActions: ["select_git_worktree"] });
  for (const locale of ["en", "ru"]) {
    const text = (key) => messages[locale][key];
    // The same notice component the run card uses, rendered from the typed error.
    const view = card(task(), { locale, runs: [run({ stopReason: refusal })] });
    const visible = initiallyVisible(view);
    assert.ok(visible.includes(text("stop_reason_workspace_snapshot_unreadable")));
    assert.ok(visible.includes(text("stop_action_select_git_worktree")));
  }
  // The shell reads the refusal from the typed error, renders it inside the
  // launch form beside the submit control, and re-clears it on the next attempt.
  assert.match(mainSource, /const refusal = launchRefusalFromError\(problem\?\.apiError \?\? null\);/);
  assert.match(mainSource, /setLaunchRefusal\(\{ taskId: intent\.draft\.taskId, refusal \}\);/);
  assert.match(mainSource, /setLaunchRefusal\(null\);/);
  assert.match(mainSource, /launchRefused !== null \? <div className="launch-refusal" role="alert">/);
  const form = mainSource.slice(mainSource.indexOf('className="launch-refusal"'));
  assert.ok(form.indexOf('type="submit"') > 0, "the refusal precedes the control it refused");
  assert.ok(form.indexOf('type="submit"') < form.indexOf("</form>"));
  // The draft is never reset by a refusal and never leaves memory.
  const launch = mainSource.slice(mainSource.indexOf("async function launchSelectedRun"), mainSource.indexOf("async function runProviderAuthAction"));
  assert.equal(launch.includes("setRun("), false);
  assert.equal(mainSource.includes("localStorage"), false);
  for (const marker of ["sessionStorage.setItem(\"draft", "sessionStorage.setItem('draft"]) {
    assert.equal(mainSource.includes(marker), false, marker);
  }
  // No run exists: the refusal is a pre-attempt answer, so nothing is marked started.
  assert.ok(launch.indexOf("setLaunchedTaskId") < launch.indexOf("catch (error: unknown)"));
});
