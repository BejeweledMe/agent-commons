import assert from "node:assert/strict";
import test from "node:test";
import { readLocalePreference, writeLocalePreference, LOCALE_PREFERENCE_KEY } from "../../shared/localePreference.ts";

test("Work and Gallery share a validated, content-free language preference across reloads", () => {
  const values = new Map();
  const writes = [];
  const storage = () => ({ getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { writes.push([key, value]); values.set(key, value); } });
  assert.equal(readLocalePreference(storage), "en");
  writeLocalePreference("ru", storage);
  assert.equal(readLocalePreference(storage), "ru");
  writeLocalePreference("en", storage);
  assert.equal(readLocalePreference(storage), "en");
  assert.deepEqual(writes, [[LOCALE_PREFERENCE_KEY, "ru"], [LOCALE_PREFERENCE_KEY, "en"]]);
  // Runtime callers cannot turn this preference API into arbitrary content storage.
  writeLocalePreference("private task content", storage);
  assert.equal(writes.length, 2);
});

test("missing, corrupt and blocked preference storage preserve the English fallback", () => {
  for (const value of [null, "", "RU", "fr", "{\"locale\":\"ru\"}", "task contents"]) {
    assert.equal(readLocalePreference(() => ({ getItem: () => value, setItem() {} })), "en");
  }
  const inaccessible = () => { throw new Error("SecurityError"); };
  assert.equal(readLocalePreference(inaccessible), "en");
  assert.doesNotThrow(() => writeLocalePreference("ru", inaccessible));
  const denied = () => ({ getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); } });
  assert.equal(readLocalePreference(denied), "en");
  assert.doesNotThrow(() => writeLocalePreference("ru", denied));
  assert.equal(readLocalePreference(), "en", "server render has no window");
});
