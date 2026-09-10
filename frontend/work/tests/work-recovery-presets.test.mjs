import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "agent-commons-work-recovery-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext",
  "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx",
  "--outDir", compiled,
  resolve(root, "src/api.ts"), resolve(root, "src/rolePresetState.ts"),
  resolve(root, "src/components/RolePresetPicker.tsx")
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
const { WorkApi, ApiProblem, parseCatalog } = await import(pathToFileURL(resolve(compiled, "api.js")).href);
const { chooseRolePreset } = await import(pathToFileURL(resolve(compiled, "rolePresetState.js")).href);
const { RolePresetPicker } = await import(pathToFileURL(resolve(compiled, "components/RolePresetPicker.js")).href);
const strings = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const apiBase = "/api/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const taskId = "task." + "0".repeat(26);
const revision = "evt." + "0".repeat(26);
const nextRevision = "evt." + "0".repeat(25) + "1";
const presetId = "agent." + "0".repeat(26);
const hiredId = "agent." + "0".repeat(25) + "1";
const response = (payload, status = 200) => ({ ok: status === 200, status, json: async () => payload });
const api = () => Object.assign(new WorkApi(), { apiBase });

for (const operation of ["create", "review", "accept", "reopen"]) {
  test(`${operation} retry after commit and response loss preserves exact body/key and performs no new CAS read`, async () => {
    const client = api();
    const committed = new Map();
    const posts = [];
    let graphReads = 0;
    let graphRevision = revision;
    globalThis.fetch = async (url, init) => {
      if (url.endsWith("/graph")) {
        graphReads += 1;
        return response({ nodes: [{ kind: "task", id: taskId, effective_revision: graphRevision }] });
      }
      posts.push(init.body);
      const body = JSON.parse(init.body);
      const previous = committed.get(body.idempotency_key);
      if (previous !== undefined) {
        assert.equal(init.body, previous, "a changed retry would conflict with the canonical receipt");
        return response({ created: false, event_type: "task.created", entity_ref: { kind: "task", id: taskId }, event_id: revision, revision });
      }
      committed.set(body.idempotency_key, init.body);
      graphRevision = nextRevision;
      throw new TypeError("Connection lost after the canonical write committed");
    };
    const signal = new AbortController().signal;
    const key = `lost-${operation}`;
    const perform = () => operation === "create"
      ? client.createTask({ title: "Task", description: "Original", criteria: ["Ready"], dependencyIds: [] }, signal, key)
      : operation === "review" ? client.requestTaskReview(taskId, ["Original criterion"], key, signal)
        : operation === "accept" ? client.acceptTask(taskId, "Original approval", key, signal)
          : client.reopenTask(taskId, "Original reason", key, signal);
    await assert.rejects(perform, TypeError);
    await perform();
    assert.equal(committed.size, 1);
    assert.equal(posts.length, 2);
    assert.equal(posts[0], posts[1]);
    assert.equal(graphReads, operation === "create" ? 0 : 1);
    if (operation !== "create") assert.equal(JSON.parse(posts[1]).expected_revision, revision);
  });
}

test("retry rejects changed intent under the previous key and preparation failure remains retryable", async () => {
  const client = api();
  let reads = 0;
  const posts = [];
  globalThis.fetch = async (url, init) => {
    if (url.endsWith("/graph")) {
      if (++reads === 1) throw new TypeError("Read unavailable before any write");
      return response({ nodes: [{ kind: "task", id: taskId, revision }] });
    }
    posts.push(init.body);
    return response({ ok: true });
  };
  const signal = new AbortController().signal;
  await assert.rejects(() => client.acceptTask(taskId, "Original", "key", signal), TypeError);
  assert.equal(posts.length, 0);
  await client.acceptTask(taskId, "Original", "key", signal);
  await assert.rejects(() => client.acceptTask(taskId, "Changed", "key", signal),
    (error) => error instanceof ApiProblem && error.apiError.code === "task_retry_intent_changed");
  assert.equal(posts.length, 1);
  assert.equal(reads, 2);
});

test("concurrent identical retries share one revision resolution and immutable body", async () => {
  const client = api();
  let reads = 0;
  const bodies = [];
  globalThis.fetch = async (url, init) => {
    if (url.endsWith("/graph")) {
      reads += 1;
      await Promise.resolve();
      return response({ nodes: [{ kind: "task", id: taskId, revision }] });
    }
    bodies.push(init.body);
    return response({ ok: true });
  };
  const signal = new AbortController().signal;
  await Promise.all([client.reopenTask(taskId, "Reason", "parallel", signal), client.reopenTask(taskId, "Reason", "parallel", signal)]);
  assert.equal(reads, 1);
  assert.equal(bodies[0], bodies[1]);
});

const preset = {
  id: presetId, name: "Implementer", profile_id: "claude-builder", context_mode: "fresh",
  template: true, state: "active", skills: ["commons-start", "commons-coordinate", "commons-record"],
  grants: { create_roles: "deny", retire_roles: "deny", open_links: "deny" }, private_metadata: "must not reach the picker"
};
const catalog = () => parseCatalog({ profiles: ["claude-builder"], context_modes: ["fresh"], presets: [preset] });

for (const locale of ["en", "ru"]) {
  test(`${locale}: applied presets have a labelled native keyboard select, preserve typed input and inherit skills through hire and launch adapters`, async () => {
    let roles = [];
    let hired;
    let launched;
    roles = [preset];
    globalThis.fetch = async (url, init) => {
      const body = JSON.parse(init.body);
      if (url.endsWith("/agents")) {
        assert.equal(body.from_preset_id, presetId);
        assert.equal("skills" in body, false, "server inherits the canonical preset's skills");
        assert.equal("profile_id" in body, false);
        assert.equal("context_mode" in body, false);
        hired = { ...roles.find((role) => role.id === body.from_preset_id), ...body, id: hiredId };
        return response({ entity_ref: { kind: "agent", id: hiredId } });
      }
      assert.ok(url.endsWith("/delegations"));
      assert.equal(body.agent_id, hired.id);
      launched = hired.skills;
      return response({ ok: true });
    };
    const client = api();
    const signal = new AbortController().signal;
    const options = catalog().presets;
    assert.equal(JSON.stringify(options).includes("private_metadata"), false);
    let draft = { name: "My typed name", rationale: "My typed purpose", profileId: "", contextMode: "fresh", fromPresetId: "" };
    const component = RolePresetPicker({ presets: options, value: "", text: (key) => strings[locale][key],
      onChange: (id) => { draft = chooseRolePreset(draft, id, options); } });
    const select = component.props.children.find((element) => element?.type === "select");
    assert.equal(select.props.id, "role-preset");
    assert.equal(select.props.onKeyDown, undefined, "native select keeps browser keyboard behavior");
    select.props.onChange({ currentTarget: { value: presetId } });
    assert.equal(draft.name, "My typed name");
    assert.equal(draft.rationale, "My typed purpose");
    assert.equal(draft.profileId, "claude-builder");
    const selected = RolePresetPicker({ presets: options, value: draft.fromPresetId, text: (key) => strings[locale][key], onChange: () => {} });
    const html = renderToStaticMarkup(selected);
    assert.ok(html.includes('for="role-preset"'));
    assert.ok(html.includes('aria-describedby="role-preset-help"'));
    assert.ok(html.includes(strings[locale].role_preset));
    assert.ok(html.includes(`value="${presetId}" selected=""`));
    for (const skill of preset.skills) assert.ok(html.includes(skill));
    await client.createRole(draft, signal);
    await client.startRun({ agentId: hiredId, taskId, contextPackId: null, contextPackRevision: null, designPackageId: null, designPackageRevision: null }, "launch", signal);
    assert.deepEqual(launched, preset.skills);
  });
}

test("preset catalog rejects non-template and oversized choices instead of inventing a preset", () => {
  assert.throws(() => parseCatalog({ profiles: [], presets: [{ ...preset, template: false }] }), ApiProblem);
  assert.throws(() => parseCatalog({ profiles: [], presets: Array(513).fill(preset) }), ApiProblem);
  assert.deepEqual(parseCatalog({ profiles: [] }).presets, []);
});
