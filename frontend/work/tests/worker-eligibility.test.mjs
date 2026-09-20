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
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-worker-eligibility-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--outDir", compiled,
  resolve(root, "src/api.ts"), resolve(root, "src/components/WorkerPicker.tsx"), resolve(root, "src/hireGate.ts")
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const load = (path) => import(pathToFileURL(resolve(compiled, path)).href);
const { WorkApi, ApiProblem, parseWorkerEligibility, parseWorkerRefusal } = await load("api.js");
const { WorkerPicker, workerChoices } = await load("components/WorkerPicker.js");
const { hireWorkerAllowed } = await load("hireGate.js");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const shellSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");

const version = "a".repeat(64);
const fingerprint = "b".repeat(64);
const roleRef = { kind: "role", source: "builtin", id: "frontend-engineer", version };
const base = "/api/" + "a".repeat(32);
const signal = () => new AbortController().signal;
const response = (payload, status = 200) => ({ ok: status === 200, status, json: async () => payload });

const eligible = () => ({
  profile_id: "claude-builder", provider: "claude", model: "claude-opus-5",
  eligibility: "eligible", refusal: null,
  capabilities: ["tools", "skills_projection", "trusted_workspace"],
  observed_revision: fingerprint
});
const reviewer = () => ({
  profile_id: "claude-independent-reviewer", provider: "claude", model: "claude-opus-5",
  eligibility: "ineligible",
  refusal: { code: "review_profile_incompatible", remediation: ["choose_builder_profile"] },
  capabilities: ["tools", "review_only"], observed_revision: fingerprint
});
const unobserved = () => ({
  profile_id: "codex-builder", provider: "codex", model: null, eligibility: "unknown",
  refusal: { code: "worker_observation_missing", remediation: ["refresh_worker_availability"] },
  capabilities: [], observed_revision: null
});
const payload = (workers = [eligible(), reviewer(), unobserved()]) => ({
  schema: "agent_commons.worker-eligibility.v1", specialization: { ...roleRef }, workers
});

test("the eligibility answer is one closed contract in the server's own order", () => {
  const parsed = parseWorkerEligibility(payload());
  assert.deepEqual(parsed.workers.map((worker) => worker.profileId), [
    "claude-builder", "claude-independent-reviewer", "codex-builder"
  ]);
  assert.deepEqual(parsed.specialization, roleRef);
  assert.equal(parsed.workers[0].eligibility, "eligible");
  assert.equal(parsed.workers[0].refusal, null);
  assert.deepEqual(parsed.workers[1].refusal, {
    code: "review_profile_incompatible", remediation: ["choose_builder_profile"]
  });
  assert.equal(parsed.workers[2].model, null);
  assert.equal(parsed.workers[2].observedRevision, null);
  assert.deepEqual(Object.keys(parsed).sort(), ["schema", "specialization", "workers"]);
  assert.deepEqual(Object.keys(parsed.workers[0]).sort(), [
    "capabilities", "eligibility", "model", "observedRevision", "profileId", "provider", "refusal"
  ]);
  const withoutSpecialization = payload();
  withoutSpecialization.specialization = null;
  assert.equal(parseWorkerEligibility(withoutSpecialization).specialization, null);
});

test("an addition, an unknown value or a missing worker list is unreadable, never eligible", () => {
  for (const mutate of [
    (value) => { value.raw_stderr = "secret"; },
    (value) => { value.schema = "agent_commons.worker-eligibility.v2"; },
    (value) => { delete value.workers; },
    (value) => { value.workers = null; },
    (value) => { value.workers = "claude-builder"; },
    (value) => { value.specialization = { ...roleRef, version: "not-a-version" }; },
    (value) => { value.workers[0].eligibility = "probably"; },
    (value) => { value.workers[0].provider = "codex"; },
    (value) => { value.workers[0].profile_id = "claude-operator"; },
    (value) => { value.workers[0].capabilities = ["tools", "root"]; },
    (value) => { value.workers[0].capabilities = ["tools", "tools"]; },
    (value) => { value.workers[0].observed_revision = "short"; },
    (value) => { value.workers[0].granted = ["write"]; },
    (value) => { delete value.workers[0].refusal; },
    // An eligible worker with a reason, and a refused worker without one, are
    // both contradictions: the answer cannot be read at all.
    (value) => { value.workers[0].refusal = { code: "worker_observation_missing", remediation: ["refresh_worker_availability"] }; },
    (value) => { value.workers[1].refusal = null; },
    (value) => { value.workers[1].refusal.code = "worker_is_busy"; },
    (value) => { value.workers[1].refusal.remediation = ["choose_builder_profile", "ignore_the_refusal"]; },
    (value) => { value.workers[1].refusal.hint = "call the operator"; },
    (value) => { value.workers[2].profile_id = "claude-builder"; }
  ]) {
    const value = payload();
    mutate(value);
    assert.throws(() => parseWorkerEligibility(value), ApiProblem);
  }
});

test("a refusal is readable only with its own closed code and exact remediation", () => {
  assert.deepEqual(parseWorkerRefusal({ code: "specialization_unavailable", remediation: ["choose_available_specialization"] }), {
    code: "specialization_unavailable", remediation: ["choose_available_specialization"]
  });
  assert.deepEqual(parseWorkerRefusal({ code: "provider_authentication_required", remediation: ["authenticate_provider"] }), {
    code: "provider_authentication_required", remediation: ["authenticate_provider"]
  });
  for (const value of [
    null, "worker_observation_missing", { code: "worker_observation_missing" },
    { code: "worker_observation_missing", remediation: [] },
    { code: "worker_observation_missing", remediation: ["do_something_else"] },
    { code: "invented_code", remediation: ["refresh_worker_availability"] }
  ]) {
    assert.equal(parseWorkerRefusal(value), null);
  }
});

test("the read names one exact specialization version and refuses to guess one", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: base });
  const urls = [];
  globalThis.fetch = async (url) => { urls.push(url); return response(payload()); };
  const result = await client.workerEligibility(roleRef, signal());
  assert.equal(result.workers.length, 3);
  assert.deepEqual(urls, [`${base}/workers/eligibility?specialization=builtin%2Ffrontend-engineer%2F${version}`]);
  for (const bad of [null, undefined, { ...roleRef, version: "short" }, { ...roleRef, source: "internet" }, { ...roleRef, kind: "skill" }]) {
    await assert.rejects(() => client.workerEligibility(bad, signal()), ApiProblem);
  }
  assert.equal(urls.length, 1, "a reference the client cannot serialize never reaches the server");
  globalThis.fetch = async () => response({ ...payload(), workers: [{ profile_id: "claude-builder" }] });
  await assert.rejects(() => client.workerEligibility(roleRef, signal()), ApiProblem);
});

test("a refused hire arrives as its typed refusal, not as a generic failure", async () => {
  const client = Object.assign(new WorkApi(), { apiBase: base });
  let sent = null;
  globalThis.fetch = async (_url, init) => {
    sent = JSON.parse(init.body);
    return response({
      code: "worker_ineligible",
      refusal: { code: "review_profile_incompatible", remediation: ["choose_builder_profile"] }
    }, 409);
  };
  const input = {
    name: "Frontend A", profileId: "claude-independent-reviewer", rationale: "Build the screen",
    contextMode: "fresh", specializationRef: roleRef, modelMode: "profile", model: ""
  };
  await assert.rejects(() => client.createRole(input, signal(), "hire"), (error) => {
    assert.ok(error instanceof ApiProblem);
    assert.equal(error.status, 409);
    assert.equal(error.apiError.code, "worker_ineligible");
    assert.deepEqual(error.apiError.refusal, {
      code: "review_profile_incompatible", remediation: ["choose_builder_profile"]
    });
    return true;
  });
  // The hire request is exactly the one this form always sent.
  assert.deepEqual(Object.keys(sent).sort(), [
    "context_mode", "idempotency_key", "name", "profile_id", "rationale", "specialization_ref"
  ]);
  // An unreadable refusal payload is still a refusal, and still carries no reason.
  globalThis.fetch = async () => response({ code: "worker_ineligible", refusal: { code: "made_up" } }, 409);
  await assert.rejects(() => client.createRole(input, signal(), "hire-2"), (error) => {
    assert.equal(error.apiError.code, "worker_ineligible");
    assert.equal(error.apiError.refusal, null);
    return true;
  });
});

test("only a ready read may offer a choice; every other state is unknown", () => {
  const workers = parseWorkerEligibility(payload()).workers;
  const ready = workerChoices({ kind: "ready", workers });
  assert.deepEqual(ready.map((choice) => choice.selectable), [true, false, false]);
  assert.deepEqual(ready.map((choice) => choice.eligibility), ["eligible", "ineligible", "unknown"]);
  for (const kind of ["loading", "unavailable"]) {
    const choices = workerChoices({ kind, workers });
    assert.deepEqual(choices.map((choice) => choice.selectable), [false, false, false]);
    assert.deepEqual(choices.map((choice) => choice.eligibility), ["unknown", "unknown", "unknown"]);
    assert.deepEqual(choices.map((choice) => choice.refusal), [null, null, null]);
  }
  assert.deepEqual(workerChoices({ kind: "idle" }), []);
});

for (const locale of ["en", "ru"]) {
  test(`${locale}: the worker step names the server's own reason and never invents eligibility`, () => {
    const text = (key) => messages[locale][key];
    const workers = parseWorkerEligibility(payload()).workers;
    const render = (props) => renderToStaticMarkup(createElement(WorkerPicker, {
      value: "claude-builder", refusal: null, invalid: false,
      onChange() {}, onRetry() {}, text, ...props
    }));

    const idle = render({ state: { kind: "idle" } });
    assert.ok(idle.includes("<fieldset class=\"worker-picker\" disabled=\"\">"));
    assert.ok(idle.includes(text("worker_step_hint")));
    assert.equal(idle.includes("type=\"radio\""), false);

    const loading = render({ state: { kind: "loading", workers: [] } });
    assert.ok(loading.includes(text("worker_loading")));

    const ready = render({ state: { kind: "ready", workers } });
    assert.ok(ready.includes('id="worker-claude-builder"'));
    assert.ok(ready.includes(text("worker_step_title")));
    // The eligible worker is the only selectable one, and it is the selection.
    assert.equal(ready.match(/type="radio"/g).length, 3);
    assert.equal(ready.match(/disabled=""/g).length, 2);
    assert.ok(/id="worker-claude-builder"[^>]*checked=""/.test(ready));
    assert.ok(ready.includes(text("worker_refusal_review_profile_incompatible")));
    assert.ok(ready.includes(text("worker_remediation_choose_builder_profile")));
    assert.ok(ready.includes(text("worker_refusal_observation_missing")));
    assert.ok(ready.includes(text("worker_remediation_refresh_worker_availability")));
    // Capabilities are text chips, described as descriptions.
    for (const key of ["worker_capability_tools", "worker_capability_skills_projection",
      "worker_capability_trusted_workspace", "worker_capability_review_only"]) {
      assert.ok(ready.includes(`<li class="worker-chip">${text(key)}</li>`), key);
    }
    assert.ok(ready.includes(text("worker_capabilities_help")));

    const failed = render({ state: { kind: "unavailable", workers } });
    assert.ok(failed.includes(text("worker_read_unavailable")));
    assert.equal(failed.match(/disabled=""/g).length, 3);
    assert.equal(failed.includes("checked=\"\""), false);
    assert.equal(failed.includes(text("worker_refusal_review_profile_incompatible")), false);
    assert.equal(failed.match(new RegExp(text("worker_state_unknown"), "g")).length, 3);

    const refused = render({
      state: { kind: "ready", workers }, invalid: true,
      refusal: { refusal: { code: "review_profile_incompatible", remediation: ["choose_builder_profile"] } }
    });
    assert.ok(refused.includes(text("worker_hire_refused")));
    assert.ok(refused.includes(text("form_error_worker")));
    const unnamed = render({ state: { kind: "ready", workers }, refusal: { refusal: null } });
    assert.ok(unnamed.includes(text("worker_hire_refused")));
  });
}

test("EN and RU carry every new worker string", () => {
  const keys = Object.keys(messages.en).filter((key) => key.startsWith("worker_") || key === "role_advanced" || key === "form_error_worker");
  assert.ok(keys.length >= 28);
  for (const key of keys) {
    assert.equal(typeof messages.ru[key], "string");
    assert.notEqual(messages.ru[key], "");
    assert.notEqual(messages.ru[key], messages.en[key]);
  }
});

test("the shell asks for the specialization first and keeps provider and model under Advanced", () => {
  const form = shellSource.slice(shellSource.indexOf('id="hire-role-form"'), shellSource.indexOf('text("create_role")'));
  assert.ok(form.indexOf("<SpecializationPicker") < form.indexOf("<WorkerPicker"));
  assert.ok(form.indexOf("<WorkerPicker") < form.indexOf('htmlFor="role-name"'));
  // Advanced is a disclosure that starts closed and still owns both controls.
  assert.match(form, /<details className="role-advanced"><summary>\{text\("role_advanced"\)\}<\/summary>/);
  assert.equal(form.includes('<details className="role-advanced" open'), false);
  const advanced = form.slice(form.indexOf('className="role-advanced"'), form.indexOf("</details>", form.indexOf('className="role-advanced"')));
  assert.ok(advanced.includes('id="role-provider"'));
  assert.ok(advanced.includes('id="role-model-mode"'));
  assert.ok(advanced.includes("selectedWorkerModel"));
  // The old profile-first select is gone: the server's worker list replaces it.
  assert.equal(form.includes('id="role-profile"'), false);
  assert.match(shellSource, /selectedWorker\?\.model \?\? selectedHireProfile\?\.model \?\? null/);
});

test("the shell submits only a server-confirmed worker and re-reads after a refusal", () => {
  assert.match(shellSource, /hireWorkerAllowed\(role\.profileId, workerState\) \? \[\] : \["worker"\]/, "the shell's only worker gate is the shared pure one");
  assert.doesNotMatch(shellSource, /role\.fromPresetId \|\|/, "no preset exemption from the eligibility gate");
  assert.match(shellSource, /specializationRef: preset\?\.specializationRef \?\? null/, "a preset answers the specialization question with its own ref");
  assert.match(shellSource, /setHireRefusal\(\{ refusal: error\.apiError\?\.refusal \?\? null \}\);\s*\n\s*setWorkerRefresh/);
  // The refusal is handled where it happened: no generic failure, no role.
  const branch = shellSource.slice(
    shellSource.indexOf('error.apiError?.code === "worker_ineligible"'),
    shellSource.indexOf('action === "runtime"', shellSource.indexOf('error.apiError?.code === "worker_ineligible"'))
  );
  assert.ok(branch.includes("return false;"));
  assert.equal(branch.includes("recordActionError"), false);
  assert.equal(branch.includes("refreshAfterConfirmed"), false);
  // Eligibility is never inferred on the client: the catalog no longer offers
  // a profile for hire, and the read is abortable and re-read by key.
  assert.equal(shellSource.includes("hireProfiles"), false);
  assert.equal(shellSource.includes("chooseRoleProfile"), false);
  assert.match(shellSource, /api\.workerEligibility\(ref, controller\.signal\)/);
  assert.match(shellSource, /\}, \[specializationKey, workersReadable, workerRefresh\]\)/);
  assert.match(shellSource, /kind: "unavailable"/);
});

test("a preset-derived profile stays blocked until the current read for its specialization calls that exact profile eligible", () => {
  // A preset records profile claude-builder. The gate does not care where the
  // profile came from: every non-ready state and every non-eligible verdict blocks.
  const presetProfile = "claude-builder";
  const workers = [eligible(), reviewer(), unobserved()].map((entry) => parseWorkerEligibility(payload([entry])).workers[0]);
  const [eligibleWorker, ineligibleWorker, unknownWorker] = workers;
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "idle" }), false, "no specialization read yet");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "loading", workers: [eligibleWorker] }), false, "a read in flight is not a verdict");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "unavailable", workers: [eligibleWorker] }), false, "a failed read leaves the profile unknown");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "ready", workers: [{ ...ineligibleWorker, profileId: presetProfile }] }), false, "ineligible for this specialization");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "ready", workers: [{ ...unknownWorker, profileId: presetProfile }] }), false, "unknown is not eligible");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "ready", workers: [ineligibleWorker, unknownWorker] }), false, "the preset's profile is absent from the answer");
  assert.equal(hireWorkerAllowed("", { kind: "ready", workers: [eligibleWorker] }), false, "no profile chosen");
  assert.equal(hireWorkerAllowed(presetProfile, { kind: "ready", workers: [eligibleWorker] }), true, "only the server's eligible verdict for that exact profile opens the gate");
});
