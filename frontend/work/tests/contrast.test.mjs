import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

/* The dark token system is the only theme, so its contrast can be checked from
   the declared :root values alone: no rendering, no browser. Ratios follow WCAG
   2.1 relative luminance; AA is 4.5:1 for body text and 3:1 for large text and
   non-text boundaries. */

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(resolve(root, "src/styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, " ");

function readRootTokens(source) {
  const tokens = new Map();
  for (const block of source.matchAll(/:root\s*\{([^}]*)\}/g)) {
    for (const declaration of block[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+)/gi)) {
      tokens.set(declaration[1], declaration[2].trim());
    }
  }
  return tokens;
}

const tokens = readRootTokens(css);

function resolveToken(name, seen = new Set()) {
  assert.ok(tokens.has(name), `styles.css :root declares ${name}`);
  assert.ok(!seen.has(name), `${name} resolves without a cycle`);
  seen.add(name);
  const value = tokens.get(name);
  const alias = value.match(/^var\(\s*(--[a-z0-9-]+)\s*\)$/i);
  return alias ? resolveToken(alias[1], seen) : value;
}

function channels(value) {
  const hex = value.trim().toLowerCase();
  assert.match(hex, /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/, `${value} is an opaque hex colour`);
  const pairs = hex.length === 4
    ? [...hex.slice(1)].map((digit) => digit + digit)
    : [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)];
  return pairs.map((pair) => Number.parseInt(pair, 16) / 255);
}

function luminance(value) {
  const [red, green, blue] = channels(value)
    .map((channel) => (channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(foreground, background) {
  const first = luminance(resolveToken(foreground));
  const second = luminance(resolveToken(background));
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}

function assertRatio(foreground, background, minimum) {
  const ratio = contrast(foreground, background);
  assert.ok(
    ratio >= minimum,
    `${foreground} on ${background} is ${ratio.toFixed(2)}:1, below ${minimum}:1`,
  );
}

const SURFACES = ["--surface-1", "--surface-2", "--surface-3"];

test("body text meets AA (4.5:1) on every surface token", () => {
  for (const surface of SURFACES) {
    for (const text of ["--text-1", "--text-2"]) assertRatio(text, surface, 4.5);
  }
});

test("the quietest text meets 3:1 on every surface token", () => {
  for (const surface of SURFACES) assertRatio("--text-3", surface, 3);
});

test("the default border meets 3:1 against the page surface", () => {
  assertRatio("--border-1", "--surface-1", 3);
  // The stronger control edge is never quieter than the default one.
  assert.ok(contrast("--border-2", "--surface-1") >= contrast("--border-1", "--surface-1"));
});

test("status text meets AA on its own status surface", () => {
  assertRatio("--status-accepted-text", "--status-accepted-surface", 4.5);
  assertRatio("--status-attention-text", "--status-attention-surface", 4.5);
  assertRatio("--status-error-text", "--status-error-surface", 4.5);
});

test("the legacy token names resolve to the semantic values", () => {
  const aliases = {
    "--surface": "--surface-2",
    "--surface-subtle": "--surface-3",
    "--surface-input": "--surface-1",
    "--surface-warning": "--status-attention-surface",
    "--text-primary": "--text-1",
    "--text-muted": "--text-2",
    "--text-warning": "--status-attention-text",
    "--border": "--border-1",
    "--border-control": "--border-2",
  };
  for (const [alias, target] of Object.entries(aliases)) {
    assert.equal(resolveToken(alias), resolveToken(target), alias);
  }
});
