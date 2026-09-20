import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const snapshot = JSON.parse(readFileSync(resolve(root, "tests/side-registry-snapshot.json"), "utf8"));
const namespaces = {
  conversation: "conversation",
  outputs: "outputs",
  project_creation: "project_creation",
  task_graph: "task_graph",
};

test("EN and RU registries share one key set with no empty strings", () => {
  assert.deepEqual(Object.keys(messages.en).sort(), Object.keys(messages.ru).sort());
  for (const locale of ["en", "ru"]) {
    for (const [key, value] of Object.entries(messages[locale])) {
      assert.equal(typeof value, "string", `${locale}.${key}`);
      assert.notEqual(value, "", `${locale}.${key}`);
    }
  }
});

test("side-registry snapshot matches namespaced i18n.json values byte for byte", () => {
  for (const [ns, prefix] of Object.entries(namespaces)) {
    const pack = snapshot[ns];
    assert.ok(pack, ns);
    for (const locale of ["en", "ru"]) {
      assert.deepEqual(Object.keys(pack[locale]), pack.keys, `${ns} ${locale} key order`);
      for (const key of pack.keys) {
        const registryKey = `${prefix}.${key}`;
        assert.equal(messages[locale][registryKey], pack[locale][key], `${locale}.${registryKey}`);
      }
    }
  }
});

const compiled = mkdtempSync(resolve(tmpdir(), "commons-i18n-registry-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), [
  "--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler",
  "--lib", "ES2022", "--resolveJsonModule", "--allowSyntheticDefaultImports", "--outDir", compiled,
  resolve(root, "src/conversationStrings.ts"),
  resolve(root, "src/outputsStrings.ts"),
  resolve(root, "src/projectCreationStrings.ts"),
  resolve(root, "src/taskGraphStrings.ts"),
], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const load = (file) => import(pathToFileURL(resolve(compiled, file)).href);
const { conversationText } = await load("conversationStrings.js");
const { outputText } = await load("outputsStrings.js");
const { projectCreationText } = await load("projectCreationStrings.js");
const { taskGraphText } = await load("taskGraphStrings.js");

test("thin shims still export the snapshot values", () => {
  for (const locale of ["en", "ru"]) {
    const conversation = conversationText(locale);
    for (const key of snapshot.conversation.keys) {
      assert.equal(conversation[key], snapshot.conversation[locale][key], `conversation ${locale}.${key}`);
    }
    const creation = projectCreationText(locale);
    for (const key of snapshot.project_creation.keys) {
      assert.equal(creation[key], snapshot.project_creation[locale][key], `project_creation ${locale}.${key}`);
    }
    for (const key of snapshot.outputs.keys) {
      assert.equal(outputText(locale, key), snapshot.outputs[locale][key], `outputs ${locale}.${key}`);
    }
    for (const key of snapshot.task_graph.keys) {
      assert.equal(taskGraphText(locale, key), snapshot.task_graph[locale][key], `task_graph ${locale}.${key}`);
    }
  }
});
