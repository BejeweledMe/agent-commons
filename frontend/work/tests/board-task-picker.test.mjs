import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, symlinkSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(resolve(tmpdir(), "board-picker-"));
execFileSync(resolve(root, "node_modules/.bin/tsc"), ["--ignoreConfig", "--target", "ES2022", "--module", "ESNext", "--moduleResolution", "Bundler", "--lib", "ES2022,DOM", "--jsx", "react-jsx", "--resolveJsonModule", "--allowSyntheticDefaultImports", "--outDir", compiled, resolve(root, "src/components/BoardTaskPicker.tsx")], { cwd: root });
symlinkSync(resolve(root, "node_modules"), resolve(compiled, "node_modules"), "dir");
copyFileSync(resolve(root, "src/i18n.json"), resolve(compiled, "i18n.json"));
const { BoardTaskPicker } = await import(pathToFileURL(resolve(compiled, "components/BoardTaskPicker.js")).href);
const messages = JSON.parse(readFileSync(resolve(root, "src/i18n.json"), "utf8"));
const task = { taskId: "task.chosen", title: "Chosen task", taskState: "ready", roleName: null };
const ready = (state = "ready", tasks = [task]) => ({ kind: "ready", connection: "connected", snapshot: { state, tasks, attention: [], runs: [] } });
const render = (tracker, selected = null, locale = "en") => renderToStaticMarkup(createElement(BoardTaskPicker, {
  agentName: "Exact agent", preselectedTaskId: selected, tracker, text: (key) => messages[locale][key], writesEnabled: false,
  onCancel() {}, onChoose() { throw new Error("opening a picker must never choose a task"); }
}));
test("opening the board picker only preselects; confirmation is explicit and the target agent is named", () => {
  for (const locale of ["en", "ru"]) {
    const html = render(ready(), task.taskId, locale);
    assert.match(html, /Exact agent/); assert.match(html, /aria-pressed="true"/);
    assert.ok(html.includes(messages[locale].board_give_task_preselected));
    assert.ok(html.includes(messages[locale].board_give_task_read_only));
    const confirm = html.match(/<button[^>]*>[^<]*<\/button>/g).find((button) => button.includes(messages[locale].board_give_task_confirm));
    assert.doesNotMatch(confirm, /disabled/);
    assert.doesNotMatch(html, /style=/);
  }
});
test("unreadable projections never offer cached tasks; absent or filtered selections cannot confirm", () => {
  for (const tracker of [{ kind: "loading" }, { kind: "failure" }, ready("loading"), ready("error")]) {
    const html = render(tracker, task.taskId);
    assert.doesNotMatch(html, /Chosen task/);
    assert.match(html, /disabled=""[^>]*>Open Prepare run<\/button>/);
    assert.doesNotMatch(html, /No task matches/);
  }
  for (const tracker of [ready("ready", []), ready("ready", [{ ...task, taskState: "accepted" }])]) {
    const html = render(tracker, task.taskId);
    assert.ok(html.includes(messages.en.board_give_task_empty));
    assert.match(html, /disabled=""[^>]*>Open Prepare run<\/button>/);
  }
});
