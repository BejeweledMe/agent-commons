import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-provider-availability-"));
execFileSync(
  resolve(root, "node_modules/.bin/tsc"),
  [
    "--ignoreConfig", "--target", "ES2022", "--module", "ESNext",
    "--moduleResolution", "Bundler", "--lib", "ES2022,DOM",
    "--outDir", compiled, resolve(root, "src/api.ts"), resolve(root, "src/setupReadiness.ts")
  ],
  { cwd: root }
);
const api = await import(pathToFileURL(resolve(compiled, "api.js")).href);
const { providerReadinessLines } = await import(pathToFileURL(resolve(compiled, "setupReadiness.js")).href);
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const shellSource = readFileSync(resolve(root, "src/main.tsx"), "utf8");

function availability() {
  return {
    profile_id: "codex-builder",
    provider: "codex",
    model: "gpt-5",
    capabilities: {
      mcp: true,
      skills: true,
      resume: "unavailable",
      cancellation: "broker",
      usage_reporting: "none",
      sandbox_boundary: "os_enforced",
      budget_units: ["provider_units"],
      context_modes: ["fresh", "accumulated"]
    },
    capability_refusals: [
      { code: "provider_resume_unavailable", remediation: ["start_new_run"] },
      {
        code: "provider_monetary_budget_unavailable",
        remediation: ["use_provider_unit_budget", "choose_monetary_budget_profile"]
      }
    ],
    installation_state: "installed",
    initialization_state: "ready",
    qualification: {
      state: "qualified",
      freshness: "current",
      fingerprint: "a".repeat(64),
      checked_at: "2026-08-31T12:00:00Z"
    },
    authentication: { state: "ready", freshness: "fresh" },
    launchable: true,
    refusal: null
  };
}

test("provider availability parser owns one closed launchability contract", () => {
  const parsed = api.parseProviderAvailabilityList([availability()]);
  assert.equal(parsed[0].launchable, true);
  assert.deepEqual(parsed[0].capabilities.budgetUnits, ["provider_units"]);
  assert.equal(parsed[0].capabilities.resume, "unavailable");
});

test("provider availability refuses money claims, contradictions, and raw additions", () => {
  const money = availability();
  money.capabilities.budget_units = ["micro_usd", "provider_units"];
  assert.throws(() => api.parseProviderAvailabilityList([money]));

  const contradictory = availability();
  contradictory.launchable = false;
  assert.throws(() => api.parseProviderAvailabilityList([contradictory]));

  const arbitrary = availability();
  arbitrary.raw_stderr = "secret";
  assert.throws(() => api.parseProviderAvailabilityList([arbitrary]));
});

test("provider availability enforces bounded scalar and exact capability invariants", () => {
  for (const length of [129, 256]) {
    const boundedModel = availability();
    boundedModel.model = "x".repeat(length);
    assert.equal(api.parseProviderAvailabilityList([boundedModel])[0].model.length, length);
  }

  const unsafeModel = availability();
  unsafeModel.model = "x".repeat(257);
  assert.throws(() => api.parseProviderAvailabilityList([unsafeModel]));

  const unsafeTime = availability();
  unsafeTime.qualification.checked_at = "tomorrow";
  assert.throws(() => api.parseProviderAvailabilityList([unsafeTime]));

  for (const timestamp of [
    "2026-02-30T12:00:00Z",
    "2026-08-31T12:00:00.000000Z",
    "2026-08-31T12:00:00.1Z"
  ]) {
    const impossibleTime = availability();
    impossibleTime.qualification.checked_at = timestamp;
    assert.throws(() => api.parseProviderAvailabilityList([impossibleTime]));
  }

  const preciseTime = availability();
  preciseTime.qualification.checked_at = "2026-08-31T12:00:00.000001Z";
  assert.equal(
    api.parseProviderAvailabilityList([preciseTime])[0].qualification.checkedAt,
    "2026-08-31T12:00:00.000001Z"
  );

  const duplicateBudget = availability();
  duplicateBudget.capabilities.budget_units = ["provider_units", "provider_units"];
  assert.throws(() => api.parseProviderAvailabilityList([duplicateBudget]));

  const noResumeRefusal = availability();
  noResumeRefusal.capability_refusals = noResumeRefusal.capability_refusals.filter(
    (item) => item.code !== "provider_resume_unavailable"
  );
  assert.throws(() => api.parseProviderAvailabilityList([noResumeRefusal]));

  const staleSkillRefusal = availability();
  staleSkillRefusal.capability_refusals.push({
    code: "provider_skill_projection_unavailable",
    remediation: ["remove_skill_requirement", "use_manual_workflow"]
  });
  assert.throws(() => api.parseProviderAvailabilityList([staleSkillRefusal]));

  const wrongRemediation = availability();
  wrongRemediation.capability_refusals[0].remediation = ["do_anything"];
  assert.throws(() => api.parseProviderAvailabilityList([wrongRemediation]));

  const wrongProvider = availability();
  wrongProvider.provider = "claude";
  wrongProvider.capabilities.budget_units = ["micro_usd", "provider_units"];
  wrongProvider.capability_refusals = wrongProvider.capability_refusals.filter(
    (item) => item.code !== "provider_monetary_budget_unavailable"
  );
  assert.throws(() => api.parseProviderAvailabilityList([wrongProvider]));

  const wrongSandbox = availability();
  wrongSandbox.capabilities.sandbox_boundary = "trusted_workspace";
  assert.throws(() => api.parseProviderAvailabilityList([wrongSandbox]));

  const impossibleFailedFreshness = availability();
  impossibleFailedFreshness.qualification = {
    state: "failed", freshness: "missing", fingerprint: null, checked_at: null
  };
  impossibleFailedFreshness.initialization_state = "not_checked";
  impossibleFailedFreshness.launchable = false;
  impossibleFailedFreshness.refusal = {
    code: "provider_qualification_failed",
    remediation: ["inspect_failed_provider_probe", "rerun_provider_canary"]
  };
  assert.throws(() => api.parseProviderAvailabilityList([impossibleFailedFreshness]));
});

test("provider authentication requires a coherent fresh observation", () => {
  const staleReady = availability();
  staleReady.authentication = { state: "ready", freshness: "stale" };
  staleReady.launchable = false;
  staleReady.refusal = {
    code: "provider_authentication_unconfirmed",
    remediation: ["check_provider_authentication"]
  };
  assert.equal(api.parseProviderAvailabilityList([staleReady])[0].launchable, false);

  const unknownPair = availability();
  unknownPair.authentication = { state: "ready", freshness: "unknown" };
  unknownPair.launchable = false;
  unknownPair.refusal = {
    code: "provider_authentication_unconfirmed",
    remediation: ["check_provider_authentication"]
  };
  assert.throws(() => api.parseProviderAvailabilityList([unknownPair]));

  const unsupported = availability();
  unsupported.authentication = { state: "unsupported", freshness: "fresh" };
  assert.equal(api.parseProviderAvailabilityList([unsupported])[0].launchable, true);

  const staleUnsupported = availability();
  staleUnsupported.authentication = { state: "unsupported", freshness: "stale" };
  staleUnsupported.launchable = false;
  staleUnsupported.refusal = {
    code: "provider_authentication_unconfirmed",
    remediation: ["check_provider_authentication"]
  };
  assert.equal(api.parseProviderAvailabilityList([staleUnsupported])[0].launchable, false);
});

test("Claude availability preserves actual safe sandbox policy and typed refusals", () => {
  const untrustedBuilder = availability();
  untrustedBuilder.profile_id = "claude-builder";
  untrustedBuilder.provider = "claude";
  untrustedBuilder.capabilities.sandbox_boundary = "none";
  untrustedBuilder.capabilities.budget_units = ["micro_usd", "provider_units"];
  untrustedBuilder.capability_refusals = untrustedBuilder.capability_refusals.filter(
    (item) => item.code !== "provider_monetary_budget_unavailable"
  );
  untrustedBuilder.initialization_state = "not_checked";
  untrustedBuilder.qualification = {
    state: "failed", freshness: "invalid", fingerprint: null, checked_at: null
  };
  untrustedBuilder.launchable = false;
  untrustedBuilder.refusal = {
    code: "provider_qualification_failed",
    remediation: ["inspect_failed_provider_probe", "rerun_provider_canary"]
  };
  const refused = api.parseProviderAvailabilityList([untrustedBuilder])[0];
  assert.equal(refused.capabilities.sandboxBoundary, "none");
  assert.equal(refused.refusal.code, "provider_qualification_failed");

  const trustedReviewer = availability();
  trustedReviewer.profile_id = "claude-independent-reviewer";
  trustedReviewer.provider = "claude";
  trustedReviewer.capabilities.sandbox_boundary = "trusted_workspace";
  trustedReviewer.capabilities.budget_units = ["micro_usd", "provider_units"];
  trustedReviewer.capability_refusals = trustedReviewer.capability_refusals.filter(
    (item) => item.code !== "provider_monetary_budget_unavailable"
  );
  const reviewer = api.parseProviderAvailabilityList([trustedReviewer])[0];
  assert.equal(reviewer.capabilities.sandboxBoundary, "trusted_workspace");
  assert.equal(reviewer.launchable, true);

  const contradictoryBuilder = structuredClone(untrustedBuilder);
  contradictoryBuilder.initialization_state = "ready";
  contradictoryBuilder.qualification = {
    state: "qualified",
    freshness: "current",
    fingerprint: "b".repeat(64),
    checked_at: "2026-08-31T12:00:00Z"
  };
  contradictoryBuilder.launchable = true;
  contradictoryBuilder.refusal = null;
  assert.throws(() => api.parseProviderAvailabilityList([contradictoryBuilder]));
});

test("Grok availability remains provider-specific and never falls back to Claude", () => {
  const grok = availability();
  grok.profile_id = "grok-builder";
  grok.provider = "grok";

  const parsed = api.parseProviderAvailabilityList([grok])[0];

  assert.equal(parsed.provider, "grok");
  assert.equal(parsed.profileId, "grok-builder");
  assert.deepEqual(parsed.capabilities.budgetUnits, ["provider_units"]);
});

test("skill projection refusal is present if and only if skills are unavailable", () => {
  const withoutSkills = availability();
  withoutSkills.capabilities.skills = false;
  withoutSkills.capability_refusals.splice(1, 0, {
    code: "provider_skill_projection_unavailable",
    remediation: ["remove_skill_requirement", "use_manual_workflow"]
  });
  assert.equal(
    api.parseProviderAvailabilityList([withoutSkills])[0].capabilities.skills,
    false
  );

  withoutSkills.capability_refusals = withoutSkills.capability_refusals.filter(
    (item) => item.code !== "provider_skill_projection_unavailable"
  );
  assert.throws(() => api.parseProviderAvailabilityList([withoutSkills]));
});

function readiness(overrides = {}) {
  return {
    installationState: "installed",
    initializationState: "ready",
    qualification: {
      state: "qualified", freshness: "current", fingerprint: "a".repeat(64), checkedAt: "2026-08-31T12:00:00Z"
    },
    authentication: { state: "ready", freshness: "fresh" },
    ...overrides
  };
}

const PROVIDER_FIRST_LEVEL_KEYS = Object.keys(messages.en).filter(
  (key) => key.startsWith("provider_readiness_") || key.startsWith("provider_availability_")
);

test("provider readiness reads as four stages in the order a run needs them", () => {
  const lines = providerReadinessLines(readiness());
  assert.deepEqual(lines.map((line) => line.stage), ["installation", "initialization", "check_run", "sign_in"]);
  assert.deepEqual(lines.map((line) => line.valueKey), [
    "provider_readiness_installed",
    "provider_readiness_initialized",
    "provider_readiness_check_passed",
    "provider_readiness_signed_in"
  ]);
  assert.deepEqual(lines.map((line) => line.tone), ["ok", "ok", "ok", "ok"]);
  assert.deepEqual(lines.map((line) => line.code), ["installed", "ready", "qualified/current", "ready/fresh"]);
  assert.deepEqual(lines.map((line) => line.labelKey), [
    "provider_availability_install_label",
    "provider_availability_init_label",
    "provider_availability_qualification_label",
    "provider_availability_auth_label"
  ]);
});

test("every canonical readiness value has one closed human reading", () => {
  const cases = [
    [readiness({ installationState: "unavailable" }), 0, "provider_readiness_not_installed", "attention"],
    [readiness({ initializationState: "failed" }), 1, "provider_readiness_initialization_failed", "attention"],
    [readiness({ initializationState: "passed_unqualified" }), 1, "provider_readiness_initialization_incomplete", "attention"],
    [readiness({ initializationState: "not_checked" }), 1, "provider_readiness_unchecked", "unknown"],
    [readiness({ qualification: { state: "required", freshness: "missing", fingerprint: null, checkedAt: null } }), 2, "provider_readiness_check_required", "attention"],
    [readiness({ qualification: { state: "failed", freshness: "invalid", fingerprint: null, checkedAt: null } }), 2, "provider_readiness_check_failed", "attention"],
    [readiness({ qualification: { state: "qualified", freshness: "invalid", fingerprint: null, checkedAt: null } }), 2, "provider_readiness_check_outdated", "attention"],
    [readiness({ authentication: { state: "authentication_required", freshness: "fresh" } }), 3, "provider_readiness_sign_in_required", "attention"],
    [readiness({ authentication: { state: "ready", freshness: "stale" } }), 3, "provider_readiness_sign_in_unconfirmed", "attention"],
    [readiness({ authentication: { state: "failed", freshness: "fresh" } }), 3, "provider_readiness_sign_in_unconfirmed", "attention"],
    [readiness({ authentication: { state: "unsupported", freshness: "fresh" } }), 3, "provider_readiness_sign_in_not_required", "ok"],
    [readiness({ authentication: { state: "not_checked", freshness: "unknown" } }), 3, "provider_readiness_unchecked", "unknown"]
  ];
  for (const [input, index, valueKey, tone] of cases) {
    const line = providerReadinessLines(input)[index];
    assert.equal(line.valueKey, valueKey);
    assert.equal(line.tone, tone);
    for (const locale of ["en", "ru"]) {
      assert.ok(messages[locale][valueKey].trim(), `${valueKey} needs ${locale} copy`);
    }
  }
});

test("the readable line keeps the canonical value only as a technical code", () => {
  const refused = readiness({
    qualification: { state: "required", freshness: "missing", fingerprint: null, checkedAt: null },
    authentication: { state: "authentication_required", freshness: "fresh" }
  });
  const lines = providerReadinessLines(refused);
  assert.deepEqual(lines.map((line) => line.code), [
    "installed", "ready", "required/missing", "authentication_required/fresh"
  ]);
  for (const line of lines) {
    for (const locale of ["en", "ru"]) {
      assert.doesNotMatch(messages[locale][line.valueKey], /_/, "a readable line never prints a canonical code");
    }
  }
});

test("first-level provider copy drops protocol jargon and names the check run in both locales", () => {
  for (const key of PROVIDER_FIRST_LEVEL_KEYS) {
    assert.ok(messages.ru[key].trim(), `${key} needs Russian copy`);
    assert.doesNotMatch(messages.en[key], /canary|probe|qualification/i);
    assert.doesNotMatch(messages.ru[key], /canary|канар|qualification|квалифик/i);
    assert.doesNotMatch(messages.en[key], /\d/, "no factual time limit is shown before WP-15");
    assert.doesNotMatch(messages.ru[key], /\d/, "no factual time limit is shown before WP-15");
  }
  assert.equal(messages.en.provider_availability_qualification_label, "Check run");
  assert.equal(messages.ru.provider_availability_qualification_label, "Проверочный запуск");
  assert.match(messages.en.provider_readiness_check_required, /check run/i);
  assert.match(messages.ru.provider_readiness_check_required, /проверочный запуск/i);
});

test("the settings surface renders one line per stage and hides codes behind technical details", () => {
  assert.match(shellSource, /<ul className="provider-readiness">[\s\S]*providerReadinessLines\(availability\)\.map\(\(line\) => \([\s\S]*text\(line\.labelKey\)[\s\S]*text\(line\.valueKey\)/);
  assert.match(shellSource, /<details className="provider-readiness-technical">[\s\S]*text\("provider_readiness_technical"\)[\s\S]*<code>\{line\.code\}<\/code>[\s\S]*availability\.refusal\?\.code \?\? "none"/);
  assert.doesNotMatch(shellSource, /text\("provider_availability_install_label"\)\}: \{availability\.installationState\}/);
  assert.doesNotMatch(shellSource, /freshForSeconds/);
});
