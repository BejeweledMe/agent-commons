import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-context-launch-"));
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

const packId = `context_pack.${"0".repeat(25)}1`;
const designPackageId = `design_package.${"0".repeat(25)}5`;
const revision = `evt.${"0".repeat(25)}2`;

function launchOptions() {
  return {
    launch_enabled: true,
    roles: [{
      id: `agent.${"0".repeat(25)}3`,
      name: "Backend",
      profile_id: "claude-builder",
      context_mode: "accumulated"
    }],
    tasks: [{ id: `task.${"0".repeat(25)}4`, title: "Build", state: "ready" }],
    context_packs: [{
      context_pack_id: packId,
      revision,
      summary: "Frozen baseline",
      fact_count: 1,
      open_question_count: 1,
      source_refs: ["must not cross the DTO"]
    }],
    context_pack_options_status: {
      freshness: "current",
      truncated: false,
      refusal: null
    },
    design_packages: [{
      design_package_id: designPackageId,
      revision,
      title: "Checkout reference",
      screen_count: 1,
      source_path: "/private/must-not-cross"
    }],
    design_package_options_status: {
      freshness: "current",
      truncated: false,
      refusal: null
    }
  };
}

test("launch parser owns the bounded exact Context Pack selection", () => {
  const parsed = apiModule.parseLaunch(launchOptions());
  assert.equal(parsed.roles[0].contextMode, "accumulated");
  assert.deepEqual(parsed.contextPacks, [{
    contextPackId: packId,
    revision,
    summary: "Frozen baseline",
    factCount: 1,
    openQuestionCount: 1
  }]);
  assert.deepEqual(parsed.designPackages, [{
    designPackageId,
    revision,
    title: "Checkout reference",
    screenCount: 1
  }]);
  assert.equal(JSON.stringify(parsed).includes("source_refs"), false);
  assert.equal(JSON.stringify(parsed).includes("source_path"), false);
});

test("launch parser refuses arbitrary modes, identifiers, and oversized pack lists", () => {
  const arbitraryMode = launchOptions();
  arbitraryMode.roles[0].context_mode = "provider_memory";
  assert.throws(() => apiModule.parseLaunch(arbitraryMode));

  const unsafeId = launchOptions();
  unsafeId.context_packs[0].context_pack_id = "context_pack.not-exact";
  assert.throws(() => apiModule.parseLaunch(unsafeId));

  const hugeSummary = launchOptions();
  hugeSummary.context_packs[0].summary = "x".repeat(4097);
  assert.throws(() => apiModule.parseLaunch(hugeSummary));

  const tooMany = launchOptions();
  tooMany.context_packs = Array.from({ length: 257 }, () => launchOptions().context_packs[0]);
  assert.throws(() => apiModule.parseLaunch(tooMany));

  const unsafeDesignId = launchOptions();
  unsafeDesignId.design_packages[0].design_package_id = "design_package.not-exact";
  assert.throws(() => apiModule.parseLaunch(unsafeDesignId));

  const tooManyDesignPackages = launchOptions();
  tooManyDesignPackages.design_packages = Array.from(
    { length: 257 },
    () => launchOptions().design_packages[0]
  );
  assert.throws(() => apiModule.parseLaunch(tooManyDesignPackages));
});

test("launch parser counts summary code points and UTF-8 bytes like the canonical bounds", () => {
  const validEmojiSummary = launchOptions();
  validEmojiSummary.context_packs[0].summary = "😀".repeat(3000);
  assert.equal(
    apiModule.parseLaunch(validEmojiSummary).contextPacks[0].summary,
    "😀".repeat(3000)
  );

  const overCodePointBound = launchOptions();
  overCodePointBound.context_packs[0].summary = "😀".repeat(4097);
  assert.throws(() => apiModule.parseLaunch(overCodePointBound));
});

test("launch parser requires explicit and internally consistent option-set status", () => {
  const truncated = launchOptions();
  truncated.context_pack_options_status = {
    freshness: "current",
    truncated: true,
    refusal: "context_pack_options_truncated"
  };
  assert.equal(apiModule.parseLaunch(truncated).contextPackOptionsStatus.truncated, true);

  const contradictory = launchOptions();
  contradictory.context_pack_options_status.truncated = true;
  assert.throws(() => apiModule.parseLaunch(contradictory));

  const designTruncated = launchOptions();
  designTruncated.design_package_options_status = {
    freshness: "current",
    truncated: true,
    refusal: "design_package_options_truncated"
  };
  assert.equal(apiModule.parseLaunch(designTruncated).designPackageOptionsStatus.truncated, true);

  const designContradictory = launchOptions();
  designContradictory.design_package_options_status.truncated = true;
  assert.throws(() => apiModule.parseLaunch(designContradictory));
});

test("startRun sends exact launch selections and omits unset selections", async () => {
  const bodies = [];
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ launched: true }) };
  };
  const api = new apiModule.WorkApi();
  api.apiBase = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const signal = new AbortController().signal;
  await api.startRun({
    agentId: "agent.1",
    taskId: "task.1",
    contextPackId: packId,
    contextPackRevision: revision,
    designPackageId,
    designPackageRevision: revision
  }, "launch-1", signal);
  await api.startRun({
    agentId: "agent.2",
    taskId: "task.2",
    contextPackId: null,
    contextPackRevision: null,
    designPackageId: null,
    designPackageRevision: null
  }, "launch-2", signal);

  assert.equal(bodies[0].context_pack_id, packId);
  assert.equal(bodies[0].context_pack_revision, revision);
  assert.equal(bodies[0].design_package_id, designPackageId);
  assert.equal(bodies[0].design_package_revision, revision);
  assert.equal("context_pack_id" in bodies[1], false);
  assert.equal("context_pack_revision" in bodies[1], false);
  assert.equal("design_package_id" in bodies[1], false);
  assert.equal("design_package_revision" in bodies[1], false);
});
