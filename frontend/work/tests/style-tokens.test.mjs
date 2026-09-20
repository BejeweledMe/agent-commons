import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FILES = ["styles.css", "board.css", "conversation.css", "outputs.css", "projectCreation.css", "taskGraph.css"];
const HEX_OUTSIDE_ROOT = {
  "styles.css": 0,
  "board.css": 0,
  "conversation.css": 0,
  "outputs.css": 0,
  "projectCreation.css": 0,
  "taskGraph.css": 0,
};
const EXISTING_TOKENS = [
  "--surface-1",
  "--surface-2",
  "--surface-3",
  "--text-1",
  "--text-2",
  "--text-3",
  "--border-1",
  "--border-2",
  "--status-attention",
  "--status-attention-surface",
  "--status-attention-text",
  "--status-active",
  "--status-active-border",
  "--status-accepted",
  "--status-accepted-surface",
  "--status-accepted-text",
  "--status-error",
  "--status-error-surface",
  "--status-error-text",
  "--accent",
  "--text-on-accent",
  "--space-1",
  "--space-2",
  "--space-3",
  "--space-4",
  "--space-5",
  "--space-6",
  "--space-7",
  "--type-1",
  "--type-2",
  "--type-3",
  "--type-4",
  "--type-5",
  "--type-6",
  "--surface",
  "--surface-subtle",
  "--surface-input",
  "--surface-warning",
  "--text-primary",
  "--text-muted",
  "--text-warning",
  "--border",
  "--border-control",
  "--button-primary",
  "--button-primary-hover",
  "--dialog-backdrop",
  "--shadow-dialog",
];
const HEX_RE = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])/g;
const TOKEN_RE = /--([a-z0-9-]+)\s*:\s*([^;]+)/gi;

function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "));
}

function parseFile(file, css) {
  const source = stripComments(css);
  const outside = [];
  const tokens = [];
  const stack = [];
  let buf = "";
  const flush = (selector, body, ancestors) => {
    const inRoot = [selector, ...ancestors].some((part) => /(^|,)\s*:root\s*(,|$)/.test(part));
    if (inRoot) {
      for (const match of body.matchAll(TOKEN_RE)) tokens.push(`--${match[1]}`);
    }
    for (const decl of body.split(";")) {
      const cut = decl.indexOf(":");
      if (cut === -1) continue;
      const value = decl.slice(cut + 1);
      HEX_RE.lastIndex = 0;
      for (const match of value.matchAll(HEX_RE)) {
        if (!inRoot) outside.push(match[0]);
      }
    }
  };
  for (const ch of source) {
    if (ch === "{") {
      stack.push({ selector: buf.trim() });
      buf = "";
      continue;
    }
    if (ch === "}") {
      const rule = stack.pop();
      if (rule) flush(rule.selector, buf, stack.map((item) => item.selector));
      buf = "";
      continue;
    }
    buf += ch;
  }
  return { outside, tokens };
}

const parsed = Object.fromEntries(FILES.map((file) => {
  const css = readFileSync(resolve(root, "src", file), "utf8");
  return [file, parseFile(file, css)];
}));

test("raw hex counts outside :root stay at the current ratchet", () => {
  for (const file of FILES) {
    assert.equal(parsed[file].outside.length, HEX_OUTSIDE_ROOT[file], file);
  }
});

test("styles.css still lists the existing :root tokens", () => {
  assert.deepEqual([...new Set(parsed["styles.css"].tokens)], EXISTING_TOKENS);
  assert.equal(EXISTING_TOKENS.length, 47);
});

test("color inventory records the same ratchet and token list", () => {
  const inventory = JSON.parse(readFileSync(resolve(root, "design/color-inventory.json"), "utf8"));
  assert.deepEqual(inventory.raw_hex_outside_root, HEX_OUTSIDE_ROOT);
  assert.deepEqual(inventory.existing_root_tokens.map((token) => token.name), EXISTING_TOKENS);
  assert.deepEqual(inventory.target_tokens, [
    "surface/1", "surface/2", "surface/3",
    "text/1", "text/2", "text/3",
    "border/1", "border/2",
    "status/attention", "status/active", "status/accepted", "status/error",
    "accent",
  ]);
});
