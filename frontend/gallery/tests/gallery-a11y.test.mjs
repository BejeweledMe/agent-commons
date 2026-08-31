import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { applyDocumentLocale } from "../src/contracts.ts";

const source = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");
const authoringSource = await readFile(new URL("../src/AuthoringPanel.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const messages = JSON.parse(await readFile(new URL("../src/i18n.json", import.meta.url), "utf8"));

test("board cards and inspector have semantic keyboard controls", () => {
  assert.match(source, /<article className=/);
  assert.match(source, /<button[\s\S]*type="button"/);
  assert.match(source, /<dialog[\s\S]*aria-labelledby="inspector-title"/);
  assert.match(source, /onCancel=/);
  assert.match(source, /autoFocus/);
  assert.match(source, /openerRef\.current\?\.focus\(\)/);
  assert.match(source, /<ol className="screen-list">/);
  assert.match(source, /<label htmlFor="feedback-message">/);
  assert.match(source, /<textarea[\s\S]*id="feedback-message"/);
  assert.match(source, /aria-describedby="feedback-help"/);
});

test("dynamic state is announced and non-color labels remain visible", () => {
  assert.ok(source.match(/aria-live="polite"/g)?.length >= 2);
  assert.match(source, /role="alert"/);
  assert.ok(messages.en.preview_stale);
  assert.ok(messages.en.preview_unavailable);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /prefers-reduced-motion/);
  assert.match(styles, /@media \(max-width: 760px\)/);
});

test("language changes synchronize the document language for assistive technology", () => {
  const root = { lang: "en" };
  applyDocumentLocale(root, "ru");
  assert.equal(root.lang, "ru");
  applyDocumentLocale(root, "en");
  assert.equal(root.lang, "en");
  assert.match(source, /applyDocumentLocale\(document\.documentElement, locale\)/);
});

test("CSP-hostile rendering APIs and inline style props are absent", () => {
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML|eval\(/);
  assert.doesNotMatch(source, /style=\{/);
  assert.doesNotMatch(authoringSource, /dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML|eval\(/);
  assert.doesNotMatch(authoringSource, /style=\{/);
});

test("authoring is keyboard-operable and exposes labels and typed live states", () => {
  assert.match(authoringSource, /<form className="authoring-form"/);
  assert.match(authoringSource, /<label htmlFor="authoring-target">/);
  assert.match(authoringSource, /<select id="authoring-target"/);
  assert.match(authoringSource, /<label htmlFor="authoring-package-title">/);
  assert.match(authoringSource, /<fieldset>/);
  assert.match(authoringSource, /<legend>/);
  assert.match(authoringSource, /type="checkbox"/);
  assert.match(authoringSource, /authoring_move_up/);
  assert.match(authoringSource, /authoring_move_down/);
  assert.match(authoringSource, /aria-live="polite"/);
  assert.match(authoringSource, /role="alert"/);
});
