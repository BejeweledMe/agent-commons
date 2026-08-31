import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-context-editor-"));
execFileSync(
  resolve(root, "node_modules/.bin/tsc"),
  [
    "--ignoreConfig", "--target", "ES2022", "--module", "ESNext",
    "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--outDir", compiled,
    resolve(root, "src/api.ts"),
    resolve(root, "src/contextPackEditorState.ts")
  ],
  { cwd: root }
);
const apiModule = await import(pathToFileURL(resolve(compiled, "api.js")).href);
const editorStateModule = await import(pathToFileURL(resolve(compiled, "contextPackEditorState.js")).href);
const id = (kind, suffix) => `${kind}.${"0".repeat(25)}${suffix}`;

function detail() {
  return {
    schema: "agent-commons.ui.context-pack.v1",
    state: "published",
    context_pack_id: id("context_pack", "1"),
    revision: id("evt", "2"),
    recorded_at: "2026-08-31T00:00:00Z",
    summary: "Frozen baseline",
    facts: [{
      statement: "The source is exact.",
      source_refs: [{
        ref: { kind: "artifact", id: id("artifact", "3") },
        revision: id("evt", "4")
      }]
    }],
    decision_refs: [],
    open_questions: ["What is next?"],
    transcript: "must not survive"
  };
}

test("Context Pack parser owns exact bounded semantic data", () => {
  const parsed = apiModule.parseContextPackDetail(detail());
  assert.equal(parsed.contextPackId, id("context_pack", "1"));
  assert.equal(parsed.facts[0].sourceRefs[0].revision, id("evt", "4"));
  assert.equal(JSON.stringify(parsed).includes("transcript"), false);

  const catalog = apiModule.parseContextPackCatalog({
    schema: "agent-commons.ui.context-packs.v1",
    state: "ready",
    packs: [{
      context_pack_id: id("context_pack", "1"), revision: id("evt", "2"),
      summary: "Frozen baseline", fact_count: 1, decision_count: 0,
      open_question_count: 1
    }],
    truncated: false
  });
  assert.equal(catalog.packs.length, 1);
});

test("Context Pack parser refuses hostile arrays, strings, refs, and state", () => {
  const tooMany = detail();
  tooMany.facts = Array.from({ length: 65 }, () => detail().facts[0]);
  assert.throws(() => apiModule.parseContextPackDetail(tooMany));

  const numericFlood = detail();
  numericFlood.open_questions = Array.from({ length: 10_000 }, () => 1);
  assert.throws(() => apiModule.parseContextPackDetail(numericFlood));

  const huge = detail();
  huge.summary = "x".repeat(2 * 1024 * 1024);
  assert.throws(() => apiModule.parseContextPackDetail(huge));

  const aggregate = detail();
  aggregate.summary = "s".repeat(4096);
  aggregate.facts = Array.from({ length: 64 }, (_, index) => ({
    statement: `${index}`.padEnd(1024, "x"),
    source_refs: [{
      ref: { kind: "artifact", id: id("artifact", "3") },
      revision: id("evt", "4")
    }]
  }));
  assert.ok(new TextEncoder().encode(JSON.stringify(aggregate)).byteLength > 65_536);
  assert.throws(() => apiModule.parseContextPackDetail(aggregate));

  const arbitraryKind = detail();
  arbitraryKind.facts[0].source_refs[0].ref.kind = "provider_output";
  assert.throws(() => apiModule.parseContextPackDetail(arbitraryKind));

  const missingRef = detail();
  missingRef.facts[0].source_refs = [];
  assert.throws(() => apiModule.parseContextPackDetail(missingRef));

  const inconsistent = {
    schema: "agent-commons.ui.context-packs.v1", state: "empty", packs: [detail()], truncated: false
  };
  assert.throws(() => apiModule.parseContextPackCatalog(inconsistent));
});

test("failed retry retains identity while explicit New rotates it", () => {
  const generated = ["create-1", "create-2"];
  const identity = new editorStateModule.ContextPackRetryIdentity(() => generated.shift());
  assert.equal(identity.forOperation("same-draft"), "create-1");
  assert.equal(identity.forOperation("same-draft"), "create-1");
  identity.reset();
  assert.equal(identity.forOperation("same-draft"), "create-2");

  const component = readFileSync(resolve(root, "src/components/ContextPacksSection.tsx"), "utf8");
  assert.match(component, /retryIdentity\.current\.reset\(\); setEditor\(emptyEditor\)/);
});

test("publish and revise preserve caller idempotency and exact expected revision", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return { ok: true, status: 200, json: async () => detail() };
  };
  const api = new apiModule.WorkApi();
  api.apiBase = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const signal = new AbortController().signal;
  const draft = apiModule.parseContextPackDetail(detail());
  await api.publishContextPack(draft, "stable-create", signal);
  await api.reviseContextPack(draft.contextPackId, draft.revision, draft, "stable-revise", signal);
  assert.equal(calls[0].body.idempotency_key, "stable-create");
  assert.equal(calls[1].body.idempotency_key, "stable-revise");
  assert.equal(calls[1].body.expected_revision, draft.revision);
  assert.equal("transcript" in calls[0].body.draft, false);
});

test("editor keeps paired locale, semantic form, and honest context modes", () => {
  const component = readFileSync(resolve(root, "src/components/ContextPacksSection.tsx"), "utf8");
  const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
  assert.match(component, /aria-live/);
  assert.match(component, /<fieldset/);
  assert.match(messages.en.context_packs_context_notice, /fresh.*accumulated.*resume unavailable/);
  assert.match(messages.ru.context_packs_context_notice, /fresh.*accumulated.*resume unavailable/);
  assert.doesNotMatch(component, /demo|mock|dangerouslySetInnerHTML|style=|provider output.*value/i);
});
